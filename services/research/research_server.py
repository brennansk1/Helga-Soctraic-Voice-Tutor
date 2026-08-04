"""
Helga Research Service — Web search and content extraction for course creation.

Provides search augmentation via SearXNG + Wikipedia + trafilatura extraction.
Only used during course creation (batch), never during live tutoring.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from urllib.parse import urlparse

import aiohttp
import re
import requests
import trafilatura
import wikipediaapi
from diskcache import Cache
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://helga-searxng:8080")
CACHE_DIR = os.getenv("CACHE_DIR", "/app/data/research_cache")
CACHE_TTL_SEARCH = 86400       # 24 hours for search results
CACHE_TTL_EXTRACT = 604800     # 7 days for extracted page content

os.makedirs(CACHE_DIR, exist_ok=True)
cache = Cache(CACHE_DIR)

wiki = wikipediaapi.Wikipedia(user_agent="Helga/1.0 (Socratic Tutor)", language="en")

# Pure ranking/query/scoring helpers live in ranking.py (dep-free + unit-tested).
from ranking import domain_tier, build_search_queries, compute_confidence, dedup_by_url


def cache_key(prefix, text):
    return f"{prefix}:{hashlib.md5(text.encode()).hexdigest()}"


# --- Wikipedia lookup ---
def wiki_search_title(query):
    """Resolve free text to a real article title via Wikipedia's search API.

    wiki.page() is an EXACT title match, which fails on everything Helga
    actually generates:
        "Identify the Right Angle"                    -> no page (a task, not a topic)
        "Right Triangle Components and Hypotenuse..." -> no page (invented phrase)
        "the pythagorean theorem"                     -> no page (article + casing)
    All three cascade levels missed, so every concept shipped ungrounded with
    confidence 0.0. Search resolves phrasing to the real article
    ("Pythagorean theorem"), which is what a human would do.
    """
    key = cache_key("wikisearch", query)
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": query, "limit": 1,
                    "namespace": 0, "format": "json"},
            headers={"User-Agent": "Helga/1.0 (Socratic Tutor)"},
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            if len(data) > 1 and data[1]:
                title = data[1][0]
                cache.set(key, title, expire=CACHE_TTL_SEARCH)
                return title
    except Exception as e:
        logger.debug(f"wiki search failed for {query!r}: {e}")
    cache.set(key, "", expire=CACHE_TTL_SEARCH)
    return None


# --- Open textbook lookup (Wikibooks / Wikiversity) --------------------------
#
# WHY THESE AND NOT MORE PAPERS
# -----------------------------
# Crossref and arXiv were added to raise grounding confidence, and they do —
# but they are the wrong shape for building a COURSE. A paper reports a result
# at the frontier; a course needs the settled canon, explained. You do not
# teach introductory biology from a 2024 Nature letter, you teach it from a
# textbook, and no amount of primary literature substitutes for that.
#
# Wikibooks and Wikiversity are open textbooks and course materials: content
# that has already done the work of selecting, sequencing and explaining. That
# is exactly what a hydrator wants to assimilate.
#
# Both run MediaWiki, so this is the same client shape as the Wikipedia lookup
# above — no new dependency, no key, no rate-limit tier, and it degrades to
# nothing if a site is unreachable.
#
# Measured on "eigenvalues": Wikibooks returns 2,773 chars of
# "Linear Algebra/Eigenvalues and Eigenvectors"; Wikiversity returns 5,020
# chars written explicitly for physics and engineering students.

TEXTBOOK_SITES = (
    ("wikibooks", "https://en.wikibooks.org/w/api.php", "Wikibooks"),
    ("wikiversity", "https://en.wikiversity.org/w/api.php", "Wikiversity"),
)

TEXTBOOK_MIN_CHARS = 400          # below this it is a stub, not teaching material


def _mediawiki_extract(api, title, timeout=12):
    """Plain-text extract of a page, or '' if unavailable."""
    try:
        r = requests.get(api, params={
            "action": "query", "prop": "extracts", "explaintext": 1,
            "redirects": 1, "titles": title, "format": "json",
        }, headers={"User-Agent": "Helga/1.0 (Socratic Tutor)"}, timeout=timeout)
        if r.status_code != 200:
            return ""
        pages = (r.json().get("query") or {}).get("pages") or {}
        for page in pages.values():
            return page.get("extract") or ""
    except Exception as e:
        logger.debug(f"mediawiki extract failed for {title!r}: {e}")
    return ""


# Wikibooks and Wikiversity host books ABOUT words as well as books about
# subjects. Searching "Cell Biology" returned "Pinyin/Cell (biology)" — a
# Chinese-language-learning page about the WORD "cell" — which would have been
# cited to a learner as a biology textbook. Title matching alone cannot tell
# those apart; the body can.
_OFF_TOPIC_PREFIXES = ("pinyin/", "wikijunior:", "subject:", "shelf:",
                       "wikibooks:", "wikiversity:", "portal:", "school:")


def _is_relevant(query, title, text):
    """Does this page actually teach the subject, or merely mention the word?"""
    low_title = (title or "").lower()
    if low_title.startswith(_OFF_TOPIC_PREFIXES):
        return False

    terms = {w for w in re.findall(r"[a-z]{4,}", (query or "").lower())}
    if not terms:
        return True                      # nothing to check against

    body = (text or "").lower()
    # A page that teaches the subject uses its vocabulary repeatedly, not once
    # in a gloss. Require at least half the query's content words to appear,
    # and the strongest one to appear more than in passing.
    present = {t for t in terms if t in body}
    if len(present) * 2 < len(terms):
        return False
    return max((body.count(t) for t in present), default=0) >= 3


def textbook_lookup(query, limit=2):
    """Open-textbook passages for a concept, newest-canon-first.

    Returns [] on any failure — grounding degrades, it never raises.
    """
    # Version the key. When the extraction or filtering logic changes, entries
    # cached under the old behaviour are wrong, not merely old — the relevance
    # filter shipped and "Pinyin/Cell (biology)" kept being served from cache.
    key = cache_key("textbook", f"v2|{query}|{limit}")
    cached = cache.get(key)
    if cached is not None:
        return cached

    out = []
    for slug, api, label in TEXTBOOK_SITES:
        if len(out) >= limit:
            break
        try:
            r = requests.get(api, params={
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": 2, "format": "json",
            }, headers={"User-Agent": "Helga/1.0 (Socratic Tutor)"}, timeout=12)
            if r.status_code != 200:
                continue
            hits = ((r.json().get("query") or {}).get("search") or [])
        except Exception as e:
            logger.debug(f"{label} search failed for {query!r}: {e}")
            continue

        for hit in hits:
            if len(out) >= limit:
                break
            title = hit.get("title")
            if not title:
                continue
            text = _mediawiki_extract(api, title)
            # A one-line stub is worse than nothing: it adds a citation and a
            # confidence point while teaching the learner nothing.
            if len(text) < TEXTBOOK_MIN_CHARS:
                continue
            if not _is_relevant(query, title, text):
                logger.debug(f"{label}: rejected {title!r} as off-topic")
                continue
            out.append({
                "type": "textbook",
                "title": title,
                "url": f"https://en.{slug}.org/wiki/" + title.replace(" ", "_"),
                "source": label,
                "text": text[:6000],
            })

    cache.set(key, out, expire=CACHE_TTL_SEARCH)
    return out


def primary_source_lookup(query, limit=2):
    """Find PRIMARY literature (DOI / arXiv) for a query.

    The depth contract requires a primary source at mastery >= 4, on the
    principle that an "advanced undergraduate" course citing only Wikipedia is
    not advanced. But with SearXNG down, research returned Wikipedia and nothing
    else, so `primary_source` was UNSATISFIABLE and the Advanced Undergraduate
    and Graduate presets promised a level the system could not deliver.

    Crossref and arXiv are free, keyless, and need no SearXNG. Crossref is
    queried first because it spans every discipline — arXiv would silently
    exclude the humanities.

    Returns [] rather than raising: a missing primary source must degrade the
    course's recorded grounding, never abort its creation.
    """
    key = cache_key("primary", f"{query}|{limit}")
    cached = cache.get(key)
    if cached is not None:
        return cached or []

    out = []
    # 1. Crossref — all disciplines, returns a DOI.
    try:
        r = requests.get(
            "https://api.crossref.org/works",
            params={"query": query, "rows": limit,
                    "select": "DOI,title,type,issued"},
            headers={"User-Agent": "Helga/1.0 (Socratic Tutor; mailto:noreply@localhost)"},
            timeout=15)
        if r.status_code == 200:
            for item in (r.json().get("message", {}).get("items") or [])[:limit]:
                doi = item.get("DOI")
                if not doi:
                    continue
                out.append({
                    "url": f"https://doi.org/{doi}",
                    "title": (item.get("title") or ["Untitled"])[0][:200],
                    "type": "journal",
                    "domain_tier": 1,
                })
    except Exception as e:
        logger.debug(f"crossref lookup failed for {query!r}: {e}")

    # 2. arXiv — preprints, STEM-leaning. HTTPS: the http endpoint 301s.
    if len(out) < limit:
        try:
            r = requests.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": f"all:{query}",
                        "max_results": limit - len(out)},
                headers={"User-Agent": "Helga/1.0 (Socratic Tutor)"},
                timeout=15)
            if r.status_code == 200:
                ids = re.findall(r"<id>(http[^<]*abs[^<]*)</id>", r.text)
                titles = re.findall(r"<title>([^<]*)</title>", r.text)
                for i, url in enumerate(ids[:limit - len(out)]):
                    out.append({
                        "url": url,
                        "title": (titles[i + 1] if len(titles) > i + 1
                                  else "arXiv preprint")[:200].strip(),
                        "type": "preprint",
                        "domain_tier": 1,
                    })
        except Exception as e:
            logger.debug(f"arxiv lookup failed for {query!r}: {e}")

    cache.set(key, out, expire=CACHE_TTL_SEARCH)
    return out


def wiki_lookup(title):
    key = cache_key("wiki", title)
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        page = wiki.page(title)
        if page.exists():
            text = page.summary[:2000]
            if len(page.summary) < 500 and page.text:
                text += "\n\n" + page.text[:3000]
            result = {
                "text": text,
                "url": page.fullurl,
                "title": page.title,
            }
            cache.set(key, result, expire=CACHE_TTL_EXTRACT)
            return result
    except Exception as e:
        logger.warning(f"Wikipedia lookup failed for '{title}': {e}")

    cache.set(key, None, expire=CACHE_TTL_SEARCH)
    return None


# --- SearXNG search ---
async def searxng_search(session, query, max_results=5):
    key = cache_key("search", query)
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        async with session.get(
            f"{SEARXNG_URL}/search",
            params={
                "q": query, "format": "json", "categories": "general",
                "language": "en", "pageno": 1,
            },
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = []
                seen_domains = set()
                for r in data.get("results", [])[:max_results * 2]:
                    url = r.get("url", "")
                    tier = domain_tier(url)
                    if tier == -1:
                        continue
                    dom = urlparse(url).netloc
                    if dom in seen_domains:
                        continue
                    seen_domains.add(dom)
                    results.append({
                        "url": url,
                        "title": r.get("title", ""),
                        "snippet": r.get("content", ""),
                        "tier": tier,
                    })
                    if len(results) >= max_results:
                        break
                results.sort(key=lambda x: x["tier"])
                cache.set(key, results, expire=CACHE_TTL_SEARCH)
                return results
    except Exception as e:
        logger.warning(f"SearXNG search failed for '{query}': {e}")

    return []


# --- Page extraction ---
async def extract_page(session, url):
    key = cache_key("page", url)
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "Helga/1.0 (Educational Research Bot)"},
        ) as resp:
            if resp.status == 200:
                html = await resp.text()
                text = trafilatura.extract(
                    html,
                    output_format="txt",
                    include_formatting=False,
                    include_links=False,
                )
                if text and len(text) > 100:
                    cache.set(key, text, expire=CACHE_TTL_EXTRACT)
                    return text
    except Exception as e:
        logger.warning(f"Extraction failed for {url}: {e}")

    return None


# --- Full research pipeline for one concept ---
async def _research_concept_async(title, module_title, course_title, mastery=1):
    sources = []
    combined_parts = []

    # 1. Wikipedia (synchronous, fast)
    #
    # Cascade concept -> module -> course. wiki_lookup does an EXACT page-title
    # match, and generated concept titles are pedagogical TASKS, not topics:
    # "Identify the Right Angle", "Name the Short Sides", "Find the Longest
    # Side". None of those is a Wikipedia article, so every concept in a real
    # 12-concept build returned confidence 0.0 and ZERO sources — which in turn
    # failed the depth contract's `any_source` requirement 10 times over.
    #
    # Verified: "Identify the Right Angle" -> 0 sources;
    #           "Pythagorean theorem"      -> 0.4 confidence, 1 source.
    # The subject is knowable from the module/course context even when the
    # concept title is a task, so fall back to it rather than teaching
    # ungrounded.
    wiki_result = None
    for candidate in (title, module_title, course_title):
        if not candidate:
            continue
        # Exact page first (cheap, cached), then SEARCH — exact match fails on
        # essentially everything this pipeline generates.
        wiki_result = wiki_lookup(candidate)
        if not wiki_result:
            resolved = wiki_search_title(candidate)
            # Retry on ANY difference, including case only. An earlier guard
            # skipped when resolved.lower() == candidate.lower(), which blocked
            # precisely the common case: "the pythagorean theorem" resolves to
            # "The Pythagorean theorem" — a real page with a 1587-char summary —
            # and differs ONLY by capitalisation. Wikipedia titles are
            # case-sensitive after the first letter, so a case-only difference
            # is a real, resolvable difference.
            if resolved and resolved != candidate:
                wiki_result = wiki_lookup(resolved)
                if wiki_result:
                    logger.info(f"wiki: {candidate!r} -> {resolved!r}")
        if wiki_result:
            if candidate is not title:
                logger.info(
                    f"wiki: '{title}' had no page; grounded via '{candidate}'")
            break
    wikipedia_data = None
    if wiki_result:
        combined_parts.append(f"## Source: Wikipedia - {wiki_result['title']}\n{wiki_result['text']}")
        wikipedia_data = wiki_result
        sources.append({
            "url": wiki_result["url"],
            "title": wiki_result["title"],
            "domain_tier": 1,
            "type": "wikipedia",
        })

    # 2a. PRIMARY LITERATURE for advanced levels. The depth contract requires
    #     a primary source at mastery >= 4; without this the Advanced
    #     Undergraduate and Graduate presets promise a level the system cannot
    #     deliver, because Wikipedia correctly does not count.
    # Fetched at EVERY level, not just >=4. Two different things were being
    # conflated: the depth contract REQUIRES a primary citation only at
    # mastery >= 4, but the grounding confidence floor (0.5) applies to every
    # level. Gating the lookup on mastery meant a beginner course could reach
    # 0.4 at best and every one of its concepts shipped marked "Limited
    # sources". Corroboration is worth having at any level; only the
    # REQUIREMENT is level-dependent.
    _n_primary = 2 if (mastery or 1) >= 4 else 1
    if True:
        # Query the SUBJECT, not the pedagogical task title — "Identify the
        # Right Angle" matches no literature.
        subject = (module_title or course_title or title)
        for ps in primary_source_lookup(subject, limit=_n_primary):
            sources.append(ps)
            combined_parts.append(
                f"## Source: {ps['type']} - {ps['title']}\n{ps['url']}")

    # 2a-bis. OPEN TEXTBOOKS — the shape of source a COURSE actually wants.
    #
    # Papers report the frontier; a course teaches the canon. You do not build
    # introductory biology from a 2024 Nature letter, you build it from a
    # textbook. Wikibooks and Wikiversity are exactly that: content already
    # selected, sequenced and explained for a learner.
    #
    # Queried on the SUBJECT for the same reason as the literature lookup —
    # "Identify the Right Angle" is a task, not a topic.
    _subject = (module_title or course_title or title)
    for tb in textbook_lookup(_subject, limit=2):
        sources.append({
            "url": tb["url"],
            "title": tb["title"],
            "domain_tier": 1,
            "type": "textbook",
            "source": tb["source"],
        })
        combined_parts.append(
            f"## Source: {tb['source']} - {tb['title']}\n{tb['text']}")

    # 2b. Generate search queries (mastery-aware)
    queries = build_search_queries(title, module_title, mastery)

    # 3. Search via SearXNG
    async with aiohttp.ClientSession() as session:
        all_search_results = []
        for q in queries:
            results = await searxng_search(session, q)
            all_search_results.extend(results)

        # De-duplicate by URL, then sort by tier, take top 5
        unique_results = dedup_by_url(all_search_results)
        unique_results.sort(key=lambda x: x["tier"])
        top_results = unique_results[:5]

        # 4. Extract content from top pages
        for result in top_results[:3]:
            text = await extract_page(session, result["url"])
            if text:
                combined_parts.append(
                    f"## Source: {result['title']}\n{text[:2000]}"
                )
                sources.append({
                    "url": result["url"],
                    "title": result["title"],
                    "domain_tier": result["tier"],
                    "type": "web",
                })

    combined_text = "\n\n".join(combined_parts)
    # Truncate to ~3000 words
    words = combined_text.split()
    if len(words) > 3000:
        combined_text = " ".join(words[:3000])

    # Confidence: based on source count and quality
    # EVERY kind the pipeline can produce must be passed. A kind that is not
    # passed does not exist as far as the score is concerned — that has now
    # been the bug here twice (primary literature, then textbooks).
    web_sources = [s for s in sources if s.get("type") == "web"]
    primary_sources = [s for s in sources
                       if s.get("type") in ("journal", "preprint")]
    textbook_sources = [s for s in sources if s.get("type") == "textbook"]
    confidence = compute_confidence(bool(wikipedia_data), len(web_sources),
                                    len(primary_sources), len(textbook_sources))

    return {
        "sources": sources,
        "wikipedia": wikipedia_data,
        "combined_text": combined_text,
        "confidence": confidence,
    }


def research_concept_sync(title, module_title, course_title, mastery=1):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _research_concept_async(title, module_title, course_title, mastery)
        )
    finally:
        loop.close()


# --- Flask routes ---

@app.route("/api/research_concept", methods=["POST"])
def api_research_concept():
    data = request.get_json(force=True)
    title = data.get("title", "")
    module_title = data.get("module_title", "")
    course_title = data.get("course_title", "")
    mastery = data.get("mastery", 1)

    if not title:
        return jsonify({"error": "title required"}), 400

    result = research_concept_sync(title, module_title, course_title, mastery)
    return jsonify(result)


@app.route("/api/research_batch", methods=["POST"])
def api_research_batch():
    data = request.get_json(force=True)
    concepts = data.get("concepts", [])
    course_title = data.get("course_title", "")
    mastery = data.get("mastery", 1)

    results = {}
    for concept in concepts:
        title = concept.get("title", "")
        module_title = concept.get("module_title", "")
        uid = concept.get("uid", title)
        if title:
            results[uid] = research_concept_sync(
                title, module_title, course_title, mastery
            )

    return jsonify({"results": results})


@app.route("/api/cache_stats", methods=["GET"])
def api_cache_stats():
    return jsonify({
        "cached_entries": len(cache),
        "cache_size_mb": round(cache.volume() / (1024 * 1024), 2),
    })


@app.route("/health", methods=["GET"])
def health():
    searxng_reachable = False
    try:
        resp = requests.get(f"{SEARXNG_URL}/healthz", timeout=3)
        searxng_reachable = resp.status_code == 200
    except Exception:
        pass

    return jsonify({
        "status": "healthy",
        "searxng_reachable": searxng_reachable,
        "cache_entries": len(cache),
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5006"))
    app.run(host="0.0.0.0", port=port, debug=False)
