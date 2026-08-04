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
                       primary_source_count: int = 0,
                       textbook_count: int = 0) -> float:
    """Heuristic grounding confidence in [0, 1], weighted BY KIND.

        open textbook (Wikibooks/Wikiversity)  0.30 each, cap 0.60
        primary literature (DOI / arXiv)       0.25 each, cap 0.50
        secondary web page                     0.20 each, cap 0.40
        Wikipedia (tertiary)                   0.40 once

    THIS FUNCTION HAS NOW BEEN THE SITE OF THE SAME BUG TWICE, so the shape of
    it is worth stating plainly: **a source kind the caller does not pass is a
    source kind that does not exist.**

    First time: primary literature. The caller filtered
    `[s for s in sources if s["type"] == "web"]`, and Crossref/arXiv carry type
    "journal"/"preprint", so a concept grounded in Wikipedia plus two
    peer-reviewed papers scored 0.40 — identical to Wikipedia alone.

    Second time: open textbooks, added 2026-08-04 and given type "textbook".
    The caller still filtered only "web" and "journal"/"preprint", so the whole
    point of the textbook work — the source kind a COURSE most wants — counted
    for exactly nothing.

    WHY TEXTBOOKS OUTWEIGH PAPERS HERE. This score grades material for building
    a course, not for advancing a field. A textbook chapter is the settled
    canon, already selected, sequenced and explained for a learner; a paper is
    one result at the frontier. Both are good evidence, but only one is the
    right shape for teaching, so the weighting says so.

    The caps matter as much as the weights: without them this rewards COUNT,
    and three mediocre web pages outscore one excellent textbook — which
    manufactures confidence rather than measuring it.

    The web cap is deliberately set so that Wikipedia plus any number of web
    pages tops out at 0.80. FULL confidence has to be earned with a textbook or
    primary source, because an arbitrary pile of web results is exactly the
    evidence profile that looks strong and teaches badly.
    """
    confidence = 0.4 if has_wikipedia else 0.0
    confidence += min(textbook_count * 0.30, 0.6)
    confidence += min(primary_source_count * 0.25, 0.5)
    confidence += min(web_source_count * 0.2, 0.4)
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
