"""Domain-routed public sources: the right archive for the right subject.

WHY ROUTED AND NOT GLOBAL
-------------------------
Querying every source for every concept is worse than not having them. A
biology concept has no use for a museum object, and a hit returned anyway is
not neutral: it costs latency, and it adds a citation that inflates grounding
confidence while teaching nothing. That is how a scoring system starts
manufacturing confidence instead of measuring it.

So each source declares the domains it serves, and a concept only queries the
sources that match.

EVERY ENDPOINT HERE WAS PROBED BEFORE BEING WIRED (2026-08-04). Three of the
candidates did not work as published and are recorded so nobody re-adds them
from a stale list:

  - Chronicling America's legacy API (chroniclingamerica.loc.gov/search/...)
    was RETIRED in 2025 and 404s. The collection now lives behind the loc.gov
    JSON API, which is what this module uses.
  - Open Library's search.json HANGS — openlibrary.org itself answers in
    0.18 s, but the search endpoint did not respond in 45 s across repeated
    attempts. Internet Archive is the same corpus through a working door.
  - Data USA's documented endpoint 404s.

No key, no login, no rate-limit tier for anything below.
"""

import logging
import re

logger = logging.getLogger(__name__)

try:  # container (flat)
    from syllabus_sources import _get_json
except ImportError:  # imported as a package
    from services.research.syllabus_sources import _get_json


# --- domain classification ---------------------------------------------------
#
# Deliberately keyword-based rather than an LLM call. This runs per concept
# during hydration, and a 4.5 s call to decide whether to query a museum would
# cost more than the museum lookup saves. Misrouting is cheap: the source
# simply returns nothing.

DOMAIN_KEYWORDS = {
    "art": ("art", "painting", "sculpture", "artist", "renaissance", "baroque",
            "impressionis", "modernis", "gallery", "museum", "aesthetic",
            "drawing", "portrait", "landscape art", "art history", "design",
            "architecture", "photography"),
    "history": ("history", "historical", "war", "revolution", "century",
                "ancient", "medieval", "colonial", "empire", "civilization",
                "civilisation", "dynasty", "treaty", "suffrage", "movement",
                "reconstruction", "depression", "immigration"),
    "philosophy": ("philosophy", "ethic", "moral", "metaphysic", "epistem",
                   "logic", "existential", "phenomenolog", "aesthetics",
                   "political theory", "rationality", "consciousness"),
    "science": ("biology", "chemistry", "physics", "geology", "astronomy",
                "ecology", "genetic", "molecul", "organism", "quantum",
                "thermodynamic", "evolution", "neuroscience", "anatomy"),
    "social": ("economic", "sociolog", "psycholog", "demograph", "statistic",
               "policy", "population", "census", "inequality", "labor",
               "labour", "market", "poverty", "education system"),
}


def classify_domains(*texts):
    """Which domains a topic plausibly belongs to. May be several, or none."""
    blob = " ".join(t for t in texts if t).lower()
    hits = {d for d, words in DOMAIN_KEYWORDS.items()
            if any(w in blob for w in words)}
    return sorted(hits)


# --- sources -----------------------------------------------------------------

def met_museum(query, limit=3):
    """Metropolitan Museum of Art — objects with curatorial description.

    Art history is genuinely taught from primary artefacts, which is why this
    earns a place where a general web search would not.
    """
    ids = _get_json("https://collectionapi.metmuseum.org/public/collection/v1/search",
                    {"q": query, "hasImages": "true"}, timeout=15)
    if not ids or not ids.get("objectIDs"):
        return []
    out = []
    for oid in (ids["objectIDs"] or [])[:limit]:
        obj = _get_json(
            f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}",
            {}, timeout=15)
        if not obj or not obj.get("title"):
            continue
        bits = [obj.get("artistDisplayName"), obj.get("objectDate"),
                obj.get("medium"), obj.get("culture")]
        out.append({
            "type": "artefact", "source": "Met Museum",
            "title": obj["title"][:160],
            "url": obj.get("objectURL") or "",
            "text": " — ".join(b for b in bits if b)[:600],
        })
    return out


def art_institute(query, limit=3):
    """Art Institute of Chicago — artworks with descriptive text."""
    data = _get_json("https://api.artic.edu/api/v1/artworks/search",
                     {"q": query, "limit": limit,
                      "fields": "id,title,artist_display,date_display,medium_display"},
                     timeout=15)
    if not data:
        return []
    out = []
    for a in data.get("data", [])[:limit]:
        if not a.get("title"):
            continue
        bits = [a.get("artist_display"), a.get("date_display"),
                a.get("medium_display")]
        out.append({
            "type": "artefact", "source": "Art Institute of Chicago",
            "title": a["title"][:160],
            "url": f"https://www.artic.edu/artworks/{a.get('id')}",
            "text": " — ".join(b for b in bits if b)[:600],
        })
    return out


def loc_chronicling_america(query, limit=3):
    """Historical US newspapers via the loc.gov JSON API.

    History is genuinely taught from primary sources. The legacy
    chroniclingamerica.loc.gov API was retired in 2025; this is the current
    endpoint and was verified returning results.
    """
    data = _get_json("https://www.loc.gov/collections/chronicling-america/",
                     {"qs": query, "dl": "page", "c": limit, "fo": "json"},
                     timeout=25)
    if not data:
        return []
    out = []
    for r in (data.get("results") or [])[:limit]:
        title = r.get("title")
        if not title:
            continue
        out.append({
            "type": "primary_document", "source": "Library of Congress",
            "title": str(title)[:160],
            "url": r.get("id") or r.get("url") or "",
            "text": " ".join((r.get("description") or []))[:600]
                    if isinstance(r.get("description"), list)
                    else str(r.get("description") or "")[:600],
        })
    return out


def wikidata_facts(query, limit=1):
    """Structured entity facts — dates, classifications, relations.

    Useful as a factual spine: an LLM writing about an entity gets its
    identifiers and classification rather than recalling them.
    """
    hits = _get_json("https://www.wikidata.org/w/api.php",
                     {"action": "wbsearchentities", "search": query,
                      "language": "en", "format": "json", "limit": limit},
                     timeout=15)
    if not hits:
        return []
    out = []
    for h in (hits.get("search") or [])[:limit]:
        desc = h.get("description")
        if not desc:
            continue
        out.append({
            "type": "structured_fact", "source": "Wikidata",
            "title": h.get("label") or query,
            "url": h.get("concepturi") or "",
            "text": desc[:400],
        })
    return out


# Registry: source -> domains it serves. "*" means every subject.
DOMAIN_SOURCES = (
    ("met_museum", met_museum, ("art",)),
    ("art_institute", art_institute, ("art",)),
    ("loc_newspapers", loc_chronicling_america, ("history", "social")),
    ("wikidata", wikidata_facts, ("*",)),
)


def fetch_domain_sources(query, domains, per_source=2, budget=4):
    """Sources relevant to `domains` only.

    `budget` caps the total returned so a well-covered subject cannot drown the
    concept in citations — more sources is not more grounding.
    """
    if not query:
        return []
    out = []
    for name, fn, serves in DOMAIN_SOURCES:
        if len(out) >= budget:
            break
        if "*" not in serves and not (set(serves) & set(domains or ())):
            continue
        try:
            out.extend(fn(query, limit=per_source)[:per_source])
        except Exception as e:
            logger.debug(f"domain source {name} failed for {query!r}: {e}")
    return out[:budget]
