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
import threading
import time
from urllib.parse import urlparse

import aiohttp
import re
import requests
import trafilatura
import wikipediaapi

try:
    import ratelimit as _rl
except ImportError:
    from services.research import ratelimit as _rl
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
# EXPLICIT SIZE LIMIT. diskcache defaults to ~1 GB and nothing here ever set
# it; `background_ops` only LOGS past 100 MB, which is a warning rather than a
# control. Disk is the plentiful resource on this machine (1.4 TB free) while
# RAM is not, so a generous cap is the right trade — but a cap, not a hope.
cache = Cache(CACHE_DIR, size_limit=int(os.getenv("HELGA_CACHE_BYTES",
                                                  2 * 1024 ** 3)))

wiki = wikipediaapi.Wikipedia(user_agent=_rl.user_agent(), language="en")

# Pure ranking/query/scoring helpers live in ranking.py (dep-free + unit-tested).
from ranking import (domain_tier, build_search_queries, compute_confidence,
                     dedup_by_url, is_documentation)


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
    # A recent miss is remembered briefly so a dead lookup is not retried on
    # every concept of a build — but for 15 minutes, not the 24 hours the old
    # shared key gave it.
    if cache.get("miss:" + key):
        return None
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": query, "limit": 1,
                    "namespace": 0, "format": "json"},
            headers=_rl.headers(),
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            if len(data) > 1 and data[1]:
                title = data[1][0]
                cache.set(key, title, expire=CACHE_TTL_SEARCH)
                return title
    except Exception as e:
        logger.debug(f"wiki search failed for {query!r}: {e}")
    # A MISS IS NOT A RESULT. This wrote an empty value under the SAME key a
    # successful 7-day extract uses, so one transient Wikipedia blip blocked
    # that concept from grounding for a full day. Negative results get their
    # own short-lived key instead.
    cache.set("miss:" + key, True, expire=900)
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
        }, headers=_rl.headers(), timeout=timeout)
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
            }, headers=_rl.headers(), timeout=12)
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
        _u = "https://api.crossref.org/works"
        _rl.wait(_u)
        # mailto only when HELGA_CONTACT is a real address: the polite pool is
        # checking for a reachable contact, and noreply@localhost is not one.
        _p = {"query": query, "rows": limit, "select": "DOI,title,type,issued"}
        _p.update(_rl.contact_param())
        r = requests.get(_u, params=_p, headers=_rl.headers(), timeout=15)
        _rl.note_response(_u, r.status_code, r.headers)
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
            _u = "https://export.arxiv.org/api/query"
            _rl.wait(_u)   # arXiv documents 1 request / 3 s; we were ignoring it
            r = requests.get(
                _u,
                params={"search_query": f"all:{query}",
                        "max_results": limit - len(out)},
                headers=_rl.headers(), timeout=15)
            _rl.note_response(_u, r.status_code, r.headers)
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
_SEARCH_STATS = threading.local()


def search_stats_reset():
    """Begin recording search outcomes on this thread."""
    _SEARCH_STATS.d = {"ok": 0, "cached": 0, "empty": 0, "degraded": 0}


def search_stats():
    d = getattr(_SEARCH_STATS, "d", None)
    return dict(d) if d is not None else None


def _search_tally(key):
    d = getattr(_SEARCH_STATS, "d", None)
    if d is not None:
        d[key] = d.get(key, 0) + 1


async def searxng_search(session, query, max_results=5):
    """Web results for one query, or [] .

    SearXNG is a META-search proxy: it scrapes Google/Brave/DDG/Startpage rather
    than calling an API, so it inherits their bot defences. Measured 2026-08-18
    on this box: the engine pool was exhausted after ~14 queries -- startpage
    and brave suspended, then duckduckgo (the only engine still contributing)
    CAPTCHA'd -- and every query after that returned **HTTP 200 with an empty
    result list**. A real build issues 24-80 queries, so this is the normal case
    partway through a course, not an edge case.

    Two consequences were being handled wrongly:

      * `[]` from a CAPTCHA storm was indistinguishable from `[]` for a genuinely
        obscure query, so a build with a dead search engine scored the same as a
        build on a topic nobody has written about.
      * that empty list was CACHED for 24 h, turning a transient block into a
        day-long "there is nothing on the web about this concept".

    `unresponsive_engines` is how SearXNG reports the difference, so use it:
    empty results WITH dead engines is degradation, and degradation is neither
    cached nor reported as a clean zero.
    """
    key = cache_key("search", query)
    cached = cache.get(key)
    if cached is not None:
        _search_tally("cached")
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
                dead = data.get("unresponsive_engines") or []
                if not results and dead:
                    # Cache this and the block outlives the block, by a day.
                    _search_tally("degraded")
                    logger.warning(
                        f"SearXNG returned nothing for {query!r} with "
                        f"{len(dead)} engine(s) down ({dead}) — treating as "
                        f"NO SEARCH, not as 'no results'; not cached")
                    return []
                _search_tally("ok" if results else "empty")
                cache.set(key, results, expire=CACHE_TTL_SEARCH)
                return results
            logger.warning(f"SearXNG HTTP {resp.status} for {query!r}")
    except Exception as e:
        logger.warning(f"SearXNG search failed for '{query}': {e}")

    _search_tally("degraded")
    return []


# --- Page extraction ---
async def _fetch_html(session, url):
    """Raw HTML for one URL, or None. Separated so the documentation crawler
    can read a page's LINKS, which `trafilatura.extract` discards."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
            headers=_rl.headers("Helga-Research/1.0"),
        ) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception as e:
        logger.warning(f"Fetch failed for {url}: {e}")
    return None


def _extract_text(html):
    if not html:
        return None
    text = trafilatura.extract(html, output_format="txt",
                               include_formatting=False, include_links=False)
    return text if (text and len(text) > 100) else None


async def extract_page(session, url):
    """Text for `url`.

    When `url` is official documentation, this reads the DOCUMENTATION SET
    rather than the single page: the entry page's own navigation is followed
    one hop, within the same host and the same doc root, up to a hard page cap.

    Why: `is_documentation()` weights these sources 0.30 — the highest
    non-Wikipedia weight — but fetching only ever took one page and 6000
    characters. A guide that spans tens of pages (the Python tutorial, the dbt
    docs) was grounding a whole concept on one page of one search hit while
    being scored as though the docs had been read. The weight was earned by the
    host's authority, not by the coverage.

    Falls back to single-page behaviour whenever the URL is not documentation
    or discovery finds nothing, so the caller's contract is unchanged.
    """
    key = cache_key("page", url)
    cached = cache.get(key)
    if cached is not None:
        return cached

    html = await _fetch_html(session, url)
    text = _extract_text(html)
    if not text:
        return None

    try:
        from services.research.ranking import is_documentation
        from services.research import doc_crawler
        if is_documentation(url):
            links = doc_crawler.discover(url, html)
            if links:
                pages = [(url, url, text)]
                for link in links:
                    sub_html = await _fetch_html(session, link)
                    sub_text = _extract_text(sub_html)
                    if sub_text:
                        pages.append((link, link, sub_text))
                combined = doc_crawler.combine(pages)
                if len(combined) > len(text):
                    logger.info(
                        f"doc set: {url} -> {len(pages)} pages, "
                        f"{len(combined)} chars (single page was {len(text)})")
                    text = combined
    except Exception as e:                   # a crawl bug must not lose the page
        logger.warning(f"Doc-set expansion failed for {url}: {e}")

    cache.set(key, text, expire=CACHE_TTL_EXTRACT)
    return text


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

    # 2a-ter. DOMAIN-ROUTED ARCHIVES. Art history is taught from artefacts and
    # history from primary documents; a biology concept has no use for either.
    # Routed, never global — an irrelevant hit is not neutral, it costs latency
    # AND inflates grounding confidence while teaching nothing.
    try:
        from domain_sources import classify_domains, fetch_domain_sources
    except ImportError:
        from services.research.domain_sources import (
            classify_domains, fetch_domain_sources)
    try:
        _domains = classify_domains(title, module_title, course_title)
        for ds in fetch_domain_sources(title or _subject, _domains):
            sources.append({
                "url": ds.get("url", ""), "title": ds["title"],
                "domain_tier": 1, "type": ds["type"], "source": ds["source"],
            })
            if ds.get("text"):
                combined_parts.append(
                    f"## Source: {ds['source']} - {ds['title']}\n{ds['text']}")
    except Exception as e:
        logger.debug(f"domain sources failed: {e}")

    # 2b. Generate search queries (mastery-aware)
    search_stats_reset()
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
    # Official documentation is promoted OUT of the generic web bucket before
    # counting, so it is not double-counted at the lower weight. For a
    # technical concept this is the authoritative source and the only one that
    # is reliably right about version-specific behaviour.
    doc_sources = [s for s in sources
                   if s.get("type") == "web" and is_documentation(s.get("url"))]
    _doc_urls = {s.get("url") for s in doc_sources}
    for s in doc_sources:
        s["type"] = "documentation"
    web_sources = [s for s in sources
                   if s.get("type") == "web" and s.get("url") not in _doc_urls]
    primary_sources = [s for s in sources
                       if s.get("type") in ("journal", "preprint")]
    textbook_sources = [s for s in sources if s.get("type") == "textbook"]
    confidence = compute_confidence(bool(wikipedia_data), len(web_sources),
                                    len(primary_sources), len(textbook_sources),
                                    len(doc_sources))

    # Report whether the web leg actually ran. Without this a concept grounded
    # only by Wikipedia looks the same whether the topic is obscure or the
    # search engine was CAPTCHA'd, and the hydrator cannot tell a thin subject
    # from a broken dependency.
    _ss = search_stats() or {}
    return {
        "sources": sources,
        "wikipedia": wikipedia_data,
        "combined_text": combined_text,
        "confidence": confidence,
        "search_degraded": bool(_ss.get("degraded", 0)),
        "search_stats": _ss,
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
