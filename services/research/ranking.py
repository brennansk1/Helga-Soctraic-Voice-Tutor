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


def compute_confidence(has_wikipedia: bool, web_source_count: int) -> float:
    """Heuristic confidence in [0, 1]: Wikipedia worth 0.4, web sources 0.2 each
    (capped at 0.6)."""
    confidence = 0.4 if has_wikipedia else 0.0
    confidence += min(web_source_count * 0.2, 0.6)
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
