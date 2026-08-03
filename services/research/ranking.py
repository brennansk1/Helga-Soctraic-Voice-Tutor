"""Pure ranking / query / scoring helpers for the research (online-search) service.

Extracted from research_server.py so this logic is unit-testable WITHOUT the heavy
network deps (aiohttp / trafilatura / wikipediaapi / diskcache). research_server
imports from here; behavior is unchanged.
"""
from urllib.parse import urlparse

# --- Domain quality tiers ---
TIER_1 = {
    "en.wikipedia.org", "plato.stanford.edu", "ocw.mit.edu",
    "arxiv.org", "www.khanacademy.org", "mathworld.wolfram.com",
    "www.nature.com", "www.britannica.com", "www.ncbi.nlm.nih.gov",
}
TIER_2 = {
    "developer.mozilla.org", "docs.python.org", "realpython.com",
    "www.investopedia.com", "www.sciencedirect.com", "stackoverflow.com",
}
BLOCKED = {
    "chegg.com", "coursehero.com", "brainly.com", "quizlet.com",
    "studocu.com", "bartleby.com", "www.chegg.com", "www.coursehero.com",
}


def domain_tier(url: str) -> int:
    """Quality tier for a URL's domain. -1 = blocked, 1 = best, 3 = unknown."""
    domain = urlparse(url).netloc.lower()
    if domain in BLOCKED:
        return -1
    if domain in TIER_1 or domain.endswith(".edu") or domain.endswith(".gov"):
        return 1
    if domain in TIER_2 or domain.startswith("docs."):
        return 2
    return 3


def build_search_queries(title: str, module_title: str, mastery: int = 1):
    """Search queries for a concept. Deeper courses (higher mastery) get extra,
    more academic queries — this is the dormant feature now activated by passing
    mastery through from the hydrator."""
    queries = [
        f"{title} {module_title} explained",
        f"{title} definition examples",
    ]
    if mastery >= 3:
        queries.append(f"{title} in-depth analysis")
    if mastery >= 4:
        queries.append(f"{title} academic overview research")
    return queries


def compute_confidence(has_wikipedia: bool, web_source_count: int,
                       primary_source_count: int = 0) -> float:
    """Heuristic grounding confidence in [0, 1].

    Weights reflect evidence strength, strongest first:
        primary literature (DOI / arXiv)  0.25 each
        secondary web sources             0.20 each
        Wikipedia (tertiary)              0.40 once

    PRIMARY SOURCES WERE NOT COUNTED AT ALL. The caller filtered
    `[s for s in sources if s["type"] == "web"]`, and Crossref/arXiv results
    carry type "journal"/"preprint" — so a concept grounded in Wikipedia plus
    two peer-reviewed papers scored 0.40, identical to Wikipedia alone. With
    SearXNG down that capped EVERY concept at 0.4 against a 0.5 floor, so every
    concept in every course shipped with a "Limited sources" marker and no
    course could clear the grounding criterion.

    Peer-reviewed literature is stronger evidence than an arbitrary web page,
    so it is weighted above it rather than merely included.
    """
    confidence = 0.4 if has_wikipedia else 0.0
    confidence += min(web_source_count * 0.2, 0.6)
    confidence += min(primary_source_count * 0.25, 0.5)
    return round(min(confidence, 1.0), 2)


def dedup_by_url(results):
    """Stable de-dup of result dicts by their 'url' field."""
    seen = set()
    out = []
    for r in results:
        if r.get("url") not in seen:
            seen.add(r.get("url"))
            out.append(r)
    return out
