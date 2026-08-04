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
IMAGE_SOURCES = (
    (wikimedia_commons, ("*",)),
    (met_images, ("art",)),
    (art_institute_images, ("art",)),
    (loc_images, ("history",)),
    (nasa_images, ("science", "geography")),
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
