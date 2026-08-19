"""Photographs and artefacts — when a diagram is the wrong answer.

A concept map beats a photo of a leaf for explaining photosynthesis. But there
are subjects where the PARTICULAR THING is the content and no diagram
substitutes: a Vermeer, a basalt column, the Zapruder frame, an actual cell
under a microscope, Saturn. Drawing those would be a lie.

So this module fetches real images — and the whole design turns on two rules.

RULE 1: LICENCE, FAIL CLOSED.
An unknown licence is a REJECTED licence. This is teaching material that may be
shown to schoolchildren and printed onto worksheets; "probably fine" is not a
licence. Public domain, CC0, CC BY and CC BY-SA are accepted. NonCommercial and
NoDerivatives are refused — not because this project sells anything, but
because a downstream teacher printing a worksheet is a derivative use and the
restriction would follow the image there. Attribution travels with every hit,
because CC BY without the BY is just infringement with extra steps.

RULE 2: FETCH ONLINE, SERVE LOCAL.
Images are downloaded at COURSE-BUILD time and cached on disk; the tutor never
reaches the network mid-dialogue. That is not offline dogma — it is that a
tutoring turn already costs ~30 s of inference and cannot also wait on a CDN,
and a lesson must not break because a remote host 404s a year later. Being
online makes courses *richer*; being offline never makes them *broken*.

Nothing here is called during a dialogue turn.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

try:  # container (flat)
    from syllabus_sources import _get_json
except ImportError:  # imported as a package
    from services.research.syllabus_sources import _get_json


# --- Licence filter ----------------------------------------------------------
# Matched against the free-text licence strings these APIs actually return,
# which are inconsistent enough that a substring match is the honest approach.
_ALLOWED = (
    r"public\s*domain", r"\bpd\b", r"\bcc0\b", r"cc[\s-]*zero",
    r"cc[\s-]*by(?![\s-]*(nc|nd))", r"creative\s*commons\s*attribution(?!.*non)",
    r"attribution[\s-]*share[\s-]*alike", r"\bcc[\s-]*by[\s-]*sa\b",
    r"no known copyright", r"^open access$", r"united states government work",
)
# Checked FIRST — a string can match both "CC BY" and "NC", and the refusal wins.
_REFUSED = (
    r"non[\s-]*commercial", r"\bnc\b", r"no[\s-]*deriv", r"\bnd\b",
    r"all rights reserved", r"fair use", r"educational use only",
    r"rights reserved", r"copyright", r"in copyright",
)


def licence_ok(text):
    """True only if the licence is affirmatively open. Unknown -> False."""
    if not text:
        return False
    blob = str(text).strip().lower()
    for bad in _REFUSED:
        if re.search(bad, blob):
            # "Public domain" sometimes appears alongside the word copyright
            # ("no known copyright restrictions") — allow that specific case.
            if re.search(r"no known copyright|public\s*domain|cc0", blob):
                break
            return False
    return any(re.search(good, blob) for good in _ALLOWED)


def _hit(source, title, url, page, licence, author="", detail=""):
    return {"type": "image", "source": source, "title": (title or "")[:160],
            "image_url": url, "url": page or "", "license": (licence or "")[:120],
            "author": (author or "")[:120], "text": (detail or "")[:400]}


# --- Sources -----------------------------------------------------------------
def wikimedia_commons(query, limit=4):
    """Wikimedia Commons — ~120M freely licensed files, no key.

    The reason this is first among equals: `extmetadata` returns a per-FILE
    licence and attribution, which is exactly what rule 1 needs. Most image
    APIs make you infer the licence from the collection, which is how
    incorrectly-licensed material ends up in a textbook.
    """
    data = _get_json("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": "6",
        "gsrlimit": str(max(1, min(int(limit) * 3, 20))),
        "prop": "imageinfo", "iiprop": "url|extmetadata|size",
        "iiurlwidth": "1024",
    }, timeout=20)
    if not data:
        return []
    out = []
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}

        def _m(key):
            return re.sub(r"<[^>]+>", "", str((meta.get(key) or {}).get("value", ""))).strip()

        licence = _m("LicenseShortName") or _m("UsageTerms") or _m("License")
        if not licence_ok(licence):
            continue
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        out.append(_hit("Wikimedia Commons", page.get("title", "").replace("File:", ""),
                        url, info.get("descriptionurl"), licence,
                        _m("Artist"), _m("ImageDescription")))
        if len(out) >= limit:
            break
    return out


def nasa_images(query, limit=3):
    """NASA image library — public domain, no key. Astronomy and earth science."""
    data = _get_json("https://images-api.nasa.gov/search",
                     {"q": query, "media_type": "image"}, timeout=20)
    if not data:
        return []
    out = []
    for item in (data.get("collection", {}).get("items") or [])[:limit * 2]:
        meta = (item.get("data") or [{}])[0]
        links = item.get("links") or []
        url = next((l.get("href") for l in links if l.get("render") == "image"), None)
        if not url:
            continue
        out.append(_hit("NASA", meta.get("title"), url,
                        f"https://images.nasa.gov/details-{meta.get('nasa_id','')}",
                        "Public Domain (NASA)", meta.get("photographer", ""),
                        meta.get("description", "")))
        if len(out) >= limit:
            break
    return out


def loc_images(query, limit=3):
    """Library of Congress — historical photographs and primary documents.

    Only takes items LoC itself marks as having no known copyright restriction;
    the collection contains plenty that is still in copyright.
    """
    data = _get_json("https://www.loc.gov/photos/",
                     {"q": query, "fo": "json", "c": str(limit * 2)}, timeout=25)
    if not data:
        return []
    out = []
    for item in (data.get("results") or [])[:limit * 2]:
        rights = " ".join(str(x) for x in (item.get("rights") or item.get("rights_advisory") or []))
        if not licence_ok(rights or "no known copyright"):
            continue
        url = item.get("image_url")
        url = url[-1] if isinstance(url, list) and url else url
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        out.append(_hit("Library of Congress", item.get("title"), url,
                        item.get("id"), rights or "No known copyright restrictions",
                        "", " ".join(item.get("description") or [])[:400]))
        if len(out) >= limit:
            break
    return out


def met_images(query, limit=2):
    """Met Museum artefact images. The Met's `isPublicDomain` flag is
    authoritative, so this trusts it rather than parsing a licence string."""
    ids = _get_json("https://collectionapi.metmuseum.org/public/collection/v1/search",
                    {"q": query, "hasImages": "true"}, timeout=15)
    if not ids or not ids.get("objectIDs"):
        return []
    out = []
    for oid in (ids["objectIDs"] or [])[:limit * 3]:
        obj = _get_json(
            f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}",
            {}, timeout=15)
        if not obj or not obj.get("primaryImageSmall") or not obj.get("isPublicDomain"):
            continue
        bits = [obj.get("artistDisplayName"), obj.get("objectDate"), obj.get("medium")]
        out.append(_hit("Met Museum", obj.get("title"), obj["primaryImageSmall"],
                        obj.get("objectURL"), "Public Domain (CC0)",
                        obj.get("artistDisplayName", ""),
                        " — ".join(b for b in bits if b)))
        if len(out) >= limit:
            break
    return out


def art_institute_images(query, limit=2):
    """Art Institute of Chicago via IIIF. Only CC0 / public-domain works."""
    data = _get_json("https://api.artic.edu/api/v1/artworks/search", {
        "q": query, "limit": limit * 3,
        "fields": "id,title,image_id,artist_display,date_display,is_public_domain,thumbnail",
    }, timeout=20)
    if not data:
        return []
    base = (data.get("config") or {}).get("iiif_url", "https://www.artic.edu/iiif/2")
    out = []
    for a in (data.get("data") or []):
        if not a.get("image_id") or not a.get("is_public_domain"):
            continue
        out.append(_hit("Art Institute of Chicago", a.get("title"),
                        f"{base}/{a['image_id']}/full/843,/0/default.jpg",
                        f"https://www.artic.edu/artworks/{a.get('id')}",
                        "Public Domain (CC0)", a.get("artist_display", ""),
                        a.get("date_display", "")))
        if len(out) >= limit:
            break
    return out


# Routed, not global — same principle as domain_sources: an irrelevant hit costs
# latency and teaches nothing. Commons is the only "*" because it genuinely
# spans every subject.
# --- vetted educational collections (2026-08-19) -----------------------------
#
# PHOTOGRAPHS ARE PERMITTED FROM THESE, and only these.
#
# The research on this phase recommended no photographs at all, on two grounds.
# Only one of them is answered by a medium restriction:
#
#   SAFETY — the argument is about INSPECTING AN UNKNOWN IMAGE, and it holds: a
#   general 9B VLM at 4-bit is not a safety classifier, and 4-bit erodes exactly
#   the tail-case calibration a gate exists for. But it says nothing about an
#   image whose provenance is a curated collection that has already done
#   editorial selection. Provenance is a stronger gate than pixel inspection,
#   which is why every source below is an institution that publishes for
#   education, not an open-web search.
#
#   PEDAGOGY — the seductive-details effect (g = -0.16 over 177 effect sizes) is
#   about DECORATIVE images, and the research used "photograph" as a proxy for
#   decorative. Levin's taxonomy classifies by FUNCTION: a photograph of an
#   actual mitochondrion is representational, doing the same job as a diagram.
#   The medium proxy is replaced by the REQUIRED ROLE on concept_assets — an
#   asset that is merely related to a concept has no role and cannot attach.
#
# Licensing stays fail-closed. "No Known Copyright Restrictions" is NOT CC0, and
# an institution's terms of use can differ from its per-item licence, so an
# unrecognised string is refused rather than assumed.


def smithsonian_images(query, limit=3):
    """Smithsonian Open Access — CC0.

    Their own FAQ warns CC0 covers copyright only: publicity, privacy and
    third-party rights may still attach, and culturally sensitive objects are
    excluded from the CC0 set. So this takes only items explicitly flagged CC0
    and carries the provenance through for attribution regardless.
    """
    # Needs a free api.data.gov key. Absent one this returns nothing and SAYS
    # SO — a source that is silently empty because it was never authenticated
    # looks identical to a subject with no images, which is the absent-vs-zero
    # confusion this project keeps paying for.
    key = os.getenv("SMITHSONIAN_API_KEY")
    if not key:
        logger.info("smithsonian: no SMITHSONIAN_API_KEY set — source SKIPPED, "
                    "not empty")
        return []
    data = _get_json("https://api.si.edu/openaccess/api/v1.0/search",
                     {"q": f"{query} AND online_media_type:Images",
                      "rows": limit, "api_key": key}, timeout=20)
    rows = ((data or {}).get("response") or {}).get("rows") or []
    out = []
    for r in rows[:limit]:
        content = (r.get("content") or {})
        descr = (content.get("descriptiveNonRepeating") or {})
        usage = ((descr.get("online_media") or {}).get("media") or [{}])[0]
        rights = (usage.get("usage") or {}).get("access", "")
        if "CC0" not in str(rights).upper():
            continue
        out.append(_hit("Smithsonian", r.get("title") or query,
                        usage.get("content") or "",
                        descr.get("record_link") or "", "CC0"))
    return [o for o in out if o.get("image_url")]


def openverse_images(query, limit=3):
    """Openverse — an aggregator, so the per-item licence is the only truth.

    Deliberately restricted to the unambiguous licences. Openverse indexes
    CC-BY-NC and CC-BY-ND alongside open material, and a generated course is a
    derivative work, so those are refused rather than filtered downstream.
    """
    data = _get_json("https://api.openverse.org/v1/images/",
                     {"q": query, "page_size": limit,
                      "license": "cc0,pdm,by,by-sa"}, timeout=20)
    out = []
    for r in ((data or {}).get("results") or [])[:limit]:
        lic = (r.get("license") or "").lower()
        if lic in ("by-nc", "by-nd", "by-nc-sa", "by-nc-nd"):
            continue
        if not licence_ok(lic) and lic not in ("cc0", "pdm", "by", "by-sa"):
            continue
        out.append(_hit("Openverse", r.get("title") or query, r.get("url") or "",
                        r.get("foreign_landing_url") or "", lic,
                        author=r.get("creator") or ""))
    return out


# PROBED AND NOT WIRED, recorded so nobody re-adds them from a list:
#
#   * CDC PHIL   — public domain and ideal for a health course, but the site is
#                  a JavaScript catalogue with no JSON endpoint.
#   * Wellcome   — has an API and a genuinely mixed licence pool per item; worth
#                  adding, but every item needs an individual rights check that
#                  is more than a filter expression.
#   * USGS       — public domain imagery lives behind several separate systems
#                  rather than one searchable API.
#
# All three are wanted. None is a one-function addition.


IMAGE_SOURCES = (
    (wikimedia_commons, ("*",)),
    (met_images, ("art",)),
    (art_institute_images, ("art",)),
    (loc_images, ("history",)),
    (nasa_images, ("science", "geography")),
    # Vetted educational collections — see the note above for why photographs
    # are permitted from these and not from an open-web search.
    (smithsonian_images, ("*",)),
    (openverse_images, ("*",)),
)


def fetch_images(query, domains=(), per_source=2, budget=6):
    """Gather licence-clean images for a concept. Never raises — a source that
    is down, slow or has changed its schema costs that source's results only."""
    domains = set(domains or ())
    out = []
    for fn, serves in IMAGE_SOURCES:
        if len(out) >= budget:
            break
        if "*" not in serves and not (domains & set(serves)):
            continue
        try:
            hits = fn(query, limit=per_source) or []
            logger.info("image source %s: %d hit(s) for %r", fn.__name__, len(hits), query)
            out.extend(hits)
        except Exception as e:
            logger.warning("image source %s failed: %s", fn.__name__, e)
    return out[:budget]
