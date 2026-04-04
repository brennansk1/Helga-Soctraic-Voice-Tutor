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


def domain_tier(url):
    domain = urlparse(url).netloc.lower()
    if domain in BLOCKED:
        return -1
    if domain in TIER_1 or domain.endswith(".edu") or domain.endswith(".gov"):
        return 1
    if domain in TIER_2 or domain.startswith("docs."):
        return 2
    return 3


def cache_key(prefix, text):
    return f"{prefix}:{hashlib.md5(text.encode()).hexdigest()}"


# --- Wikipedia lookup ---
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
    wiki_result = wiki_lookup(title)
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

    # 2. Generate search queries
    queries = [
        f"{title} {module_title} explained",
        f"{title} definition examples",
    ]
    if mastery >= 3:
        queries.append(f"{title} in-depth analysis")
    if mastery >= 4:
        queries.append(f"{title} academic overview research")

    # 3. Search via SearXNG
    async with aiohttp.ClientSession() as session:
        all_search_results = []
        for q in queries:
            results = await searxng_search(session, q)
            all_search_results.extend(results)

        # De-duplicate by URL
        seen_urls = set()
        unique_results = []
        for r in all_search_results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                unique_results.append(r)

        # Sort by tier, take top 5
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
    confidence = 0.0
    if wikipedia_data:
        confidence += 0.4
    web_sources = [s for s in sources if s["type"] == "web"]
    confidence += min(len(web_sources) * 0.2, 0.6)
    confidence = min(confidence, 1.0)

    return {
        "sources": sources,
        "wikipedia": wikipedia_data,
        "combined_text": combined_text,
        "confidence": round(confidence, 2),
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
