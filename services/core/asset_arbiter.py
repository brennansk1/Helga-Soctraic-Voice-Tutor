"""Choosing between assets — across sources, and across a whole course.

TWO PROBLEMS THAT LOOK LIKE ONE
-------------------------------
1. **Cross-source arbitration.** A concept about mitochondria queries seven
   image sources plus the textbook. Several will answer. Nothing today ranks
   them, so a concept can collect five pictures of the same organelle from five
   institutions — each correctly licensed, each individually reasonable.

2. **Whole-course duplication.** Eight concepts each independently decide they
   want a water-cycle diagram, and each attaches its own. This is the image
   analogue of the re-teaching problem the taught-ledger solved for text, and
   `asset_collector`'s own docstring names it as the reason Phase 3 is a
   whole-course pass — "duplicate figures are the most visible way this feature
   could go wrong". The logic was never written.

Both are decided here, with no model in the loop.

WHY EXACT HASHING IS NOT ENOUGH
-------------------------------
`assets.sha256` already dedupes byte-identical files, which catches the same
image fetched twice. It does not catch the same diagram re-encoded, rescaled, or
served as PNG by one institution and JPEG by another — which is the normal case
across sources. A perceptual hash catches those; it is arithmetic on pixels, so
it cannot drift.

WHAT ARBITRATION IS NOT
-----------------------
It is not relevance judgement. Whether an image depicts a concept is decided
upstream by caption matching and the required role. This ranks assets that have
ALREADY earned a role, and it never promotes one that has not.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Source authority, highest first. The ordering is about how much editorial and
# licensing work the institution has already done for us, not about image
# quality — a textbook figure was drawn FOR teaching and carries a caption
# written by the author, which is why it outranks a museum object that merely
# happens to depict the subject.
SOURCE_RANK = {
    "textbook": 100,        # drawn for teaching, caption by the author
    "openstax": 95, "openstax/cnx": 95, "libretexts": 95,
    "phet": 90,             # CC-BY, purpose-built teaching diagrams
    "nasa": 80, "usgs": 80, "noaa": 80,
    "smithsonian": 75, "loc": 70, "library of congress": 70,
    "wikimedia commons": 60,
    "met": 50, "art institute of chicago": 50, "rijksmuseum": 50,
    "openverse": 30,        # an aggregator: least editorial control
}
DEFAULT_RANK = 40

# Licence preference. All of these are usable — the fail-closed filter already
# rejected anything that is not — but a public-domain asset carries no
# attribution obligation downstream, so it is preferred where all else is equal.
LICENCE_RANK = {"public domain": 30, "cc0": 30, "pdm": 30,
                "cc by 4.0": 20, "by": 20, "cc-by": 20,
                "cc by-sa 4.0": 10, "by-sa": 10, "cc-by-sa": 10}

# Hamming distance between 64-bit dHashes below which two images are the same
# picture. 10 tolerates re-encoding and rescaling; below ~5 misses genuine
# duplicates that were resized, above ~14 starts merging distinct diagrams that
# share a layout.
PHASH_THRESHOLD = 10

# How many concepts may share one asset before it is a course-wide motif rather
# than an illustration. A recurring diagram that anchors a spiral is good; the
# same stock picture on nine concepts is the failure this exists to stop.
MAX_CONCEPTS_PER_ASSET = 3


def dhash(image_bytes, size=8):
    """A 64-bit perceptual hash. None if the image cannot be read.

    Difference hash: downscale to 9x8 greyscale and record whether each pixel is
    brighter than its right-hand neighbour. Invariant to scale, re-encoding and
    small brightness shifts, which is exactly the difference between the same
    figure served by two institutions.
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize(
            (size + 1, size), Image.LANCZOS)
        px = list(img.getdata())
        bits = 0
        for row in range(size):
            for col in range(size):
                left = px[row * (size + 1) + col]
                right = px[row * (size + 1) + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return bits
    except Exception as e:
        logger.debug(f"dhash failed: {e}")
        return None


def hamming(a, b):
    if a is None or b is None:
        return 64
    return bin(a ^ b).count("1")


def score(asset):
    """Rank an asset. Higher is better.

    Deliberately transparent arithmetic rather than a learned weighting: this
    decides what a learner sees, and "why did it pick that one" has to be
    answerable.
    """
    src = (asset.get("source") or "").strip().lower()
    lic = (asset.get("license") or "").strip().lower()
    s = SOURCE_RANK.get(src, DEFAULT_RANK)
    s += max((v for k, v in LICENCE_RANK.items() if k in lic), default=0)
    # A publisher's own caption is worth more than a filename, and a verified
    # one more than a guess.
    if asset.get("caption_verified"):
        s += 25
    if asset.get("caption"):
        s += 10
    if asset.get("alt_text"):
        s += 5
    # Resolution, capped: a bigger diagram is mildly better and a huge photo is
    # not 40 points better than an adequate one.
    px = (asset.get("width") or 0) * (asset.get("height") or 0)
    s += min(15, px // 100_000)
    return s


def arbitrate(candidates, keep=1):
    """Choose between assets offered for ONE concept.

    Groups perceptual near-duplicates, keeps the best-scoring member of each
    group, and returns the top `keep` distinct pictures. Returns
    `(kept, dropped)` so a caller can log what it discarded rather than having
    assets vanish silently.
    """
    if not candidates:
        return [], []
    for c in candidates:
        if c.get("phash") is None and c.get("bytes"):
            c["phash"] = dhash(c["bytes"])

    groups = []
    for c in sorted(candidates, key=score, reverse=True):
        placed = False
        for g in groups:
            if hamming(c.get("phash"), g[0].get("phash")) <= PHASH_THRESHOLD:
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])

    winners = [g[0] for g in groups]
    dropped = [c for g in groups for c in g[1:]]
    kept = winners[:keep]
    dropped += winners[keep:]
    if dropped:
        logger.info(f"[ARBITER] kept {len(kept)} of {len(candidates)} "
                    f"candidate(s); {len(dropped)} dropped as duplicate or lower-ranked")
    return kept, dropped


def course_duplicates(attachments):
    """Assets attached to too many concepts across a course.

    `attachments` is [{concept_uid, asset_id, phash?}]. Returns the asset ids
    that exceed MAX_CONCEPTS_PER_ASSET, with the concepts they are on.

    The threshold is not 1, deliberately. A diagram that anchors a spiral
    curriculum SHOULD recur — the same argument that made the text redundancy
    gate a share rather than a count. What this catches is the picture that has
    become wallpaper.
    """
    by_asset = defaultdict(list)
    for a in attachments or []:
        by_asset[a["asset_id"]].append(a["concept_uid"])
    over = {aid: sorted(set(cs)) for aid, cs in by_asset.items()
            if len(set(cs)) > MAX_CONCEPTS_PER_ASSET}
    return over


def near_duplicate_groups(assets):
    """Perceptually identical assets stored under different ids.

    The whole-course pass: two institutions supplying the same diagram produce
    two rows with different sha256 and the same picture. Returns groups of ids
    that should be collapsed to their best-scoring member.
    """
    scored = sorted(assets, key=score, reverse=True)
    for a in scored:
        if a.get("phash") is None and a.get("bytes"):
            a["phash"] = dhash(a["bytes"])
    groups, used = [], set()
    for i, a in enumerate(scored):
        if a["asset_id"] in used or a.get("phash") is None:
            continue
        group = [a]
        used.add(a["asset_id"])
        for b in scored[i + 1:]:
            if b["asset_id"] in used or b.get("phash") is None:
                continue
            if hamming(a["phash"], b["phash"]) <= PHASH_THRESHOLD:
                group.append(b)
                used.add(b["asset_id"])
        if len(group) > 1:
            groups.append({"keep": group[0]["asset_id"],
                           "collapse": [g["asset_id"] for g in group[1:]],
                           "sources": [g.get("source") for g in group]})
    return groups
