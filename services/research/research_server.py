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
from ranking import (domain_tier, build_search_queries,       # noqa: E402
                     confidence_from_sources, dedup_by_url)


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


# Mentions of the subject-defining word per 10,000 characters. A page that
# TEACHES a subject returns to its name repeatedly; one that merely mentions it
# does not. Calibrated against the observed failure: the Mirad grammar page
# scored far below this for "quantum", while genuine quantum textbook chapters
# clear it comfortably.
MIN_ANCHOR_DENSITY = 1.0


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

    # THE DISTINCTIVE TERM MUST BE THERE, AND RAW COUNTS DO NOT SCALE.
    #
    # "Mirad Grammar/Word Families" — a page about a CONSTRUCTED LANGUAGE —
    # was cited as a textbook on 8 of 22 concepts of a quantum computing
    # course, and the course still reported Source confidence 1.00. The page
    # is 208,000 characters, and a document that large contains almost any
    # ordinary word many times:
    #
    #     _is_relevant("Quantum State Notation Families", ...) -> True
    #     _is_relevant("Word Families", ...)                   -> True
    #
    # Two independent holes let it through:
    #
    #   * Half the words being present is satisfied by GENERIC vocabulary
    #     alone ("state", "notation", "families"). The one word that actually
    #     names the subject — "quantum" — was never required.
    #   * A raw count of >= 3 is trivial in a 200k-character page. The same
    #     threshold means completely different things at 3k and at 200k, so it
    #     grows MORE permissive exactly as pages get longer and more general.
    #
    # The fix is DENSITY, applied to half the query's terms.
    #
    # An earlier attempt used the longest term as a "subject anchor", on the
    # theory that longer words are the topical ones. That is not true and the
    # measurement said so: for "Quantum State Notation Families" the longest
    # words are "notation" and "families" (8) while the word that names the
    # subject, "quantum", is 7 — so the anchor landed on generic vocabulary
    # and the page passed anyway.
    #
    # Measured on the two pages, per 10,000 characters:
    #
    #                        quantum  qubit  entanglement  state
    #   Mirad grammar          0.00   0.00      0.00        0.91
    #   real quantum chapter   2.65   2.60      2.42       16.97
    #
    # The discriminator is not which term is longest — it is that a page
    # teaching a subject uses SEVERAL of its terms densely, while an unrelated
    # page scrapes past on one common word. So: count how many query terms
    # clear the density bar, and require at least half of them to do it. This
    # mirrors the presence rule above, with substance instead of mere presence.
    dense = sum(1 for t in terms
                if 10_000.0 * body.count(t) / max(len(body), 1)
                >= MIN_ANCHOR_DENSITY)
    if dense * 2 < len(terms):
        return False

    return max((body.count(t) for t in present), default=0) >= 3


def textbook_lookup(query, limit=2):
    """Open-textbook passages for a concept, newest-canon-first.

    Returns [] on any failure — grounding degrades, it never raises.
    """
    # Version the key. When the extraction or filtering logic changes, entries
    # cached under the old behaviour are wrong, not merely old — the relevance
    # filter shipped and "Pinyin/Cell (biology)" kept being served from cache.
    key = cache_key("textbook", f"v3|{query}|{limit}")
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


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
PRIMARY_MIN_ABSTRACT = 120        # shorter than this is a stub, not an abstract


def _clean_abstract(raw):
    """Plain text from a Crossref JATS / arXiv Atom abstract, or ''."""
    if not raw:
        return ""
    text = _WS.sub(" ", _TAG.sub(" ", raw)).strip()
    # Crossref abstracts routinely open with the literal word "Abstract".
    if text[:9].lower().startswith("abstract"):
        text = text[8:].lstrip(" :.-")
    return text


def primary_source_lookup(query, limit=2):
    """Find PRIMARY literature (DOI / arXiv) for a query, WITH its abstract.

    The depth contract requires a primary source at mastery >= 4, on the
    principle that an "advanced undergraduate" course citing only Wikipedia is
    not advanced. But with SearXNG down, research returned Wikipedia and nothing
    else, so `primary_source` was UNSATISFIABLE and the Advanced Undergraduate
    and Graduate presets promised a level the system could not deliver.

    Crossref and arXiv are free, keyless, and need no SearXNG. Crossref is
    queried first because it spans every discipline — arXiv would silently
    exclude the humanities.

    WHY THE ABSTRACT IS NOW MANDATORY. This used to return title + URL only,
    and the pipeline appended exactly that to the prompt: a heading and a link.
    The model read no sentence of the paper, yet the paper was rendered to the
    learner as a citation and counted 0.25 toward grounding confidence. That is
    a citation for text that never reached the model — the course claimed
    evidence it had not used. So a result without a usable abstract is not
    returned at all: Crossref is asked for more rows than needed and the ones
    with abstracts are kept, and arXiv (which always carries a summary)
    backfills the rest.

    Returns [] rather than raising: a missing primary source must degrade the
    course's recorded grounding, never abort its creation.
    """
    # v2: entries cached under the old shape have no "text", so every one of
    # them would now be discarded as uncitable. Version the key rather than
    # serving a week of results that cannot be used.
    key = cache_key("primary", f"v3|{query}|{limit}")
    cached = cache.get(key)
    if cached is not None:
        return cached or []

    out = []
    # 1. Crossref — all disciplines, returns a DOI. Ask for 3x and filter:
    #    abstract coverage is real but patchy, and a hit without one is unusable.
    try:
        r = requests.get(
            "https://api.crossref.org/works",
            params={"query": query, "rows": max(limit * 3, 6),
                    "select": "DOI,title,type,issued,abstract"},
            headers={"User-Agent": "Helga/1.0 (Socratic Tutor; mailto:noreply@localhost)"},
            timeout=15)
        if r.status_code == 200:
            for item in (r.json().get("message", {}).get("items") or []):
                if len(out) >= limit:
                    break
                doi = item.get("DOI")
                abstract = _clean_abstract(item.get("abstract"))
                if not doi or len(abstract) < PRIMARY_MIN_ABSTRACT:
                    continue
                titles = item.get("title") or []
                title_str = titles[0] if (titles and titles[0]) else "Untitled"
                out.append({
                    "url": f"https://doi.org/{doi}",
                    "title": str(title_str)[:200],
                    "type": "journal",
                    "source": "Crossref",
                    "domain_tier": 1,
                    "text": abstract[:2500],
                })
    except Exception as e:
        logger.debug(f"crossref lookup failed for {query!r}: {e}")

    # 2. arXiv — preprints, STEM-leaning. HTTPS: the http endpoint 301s.
    #    Parsed per <entry> rather than with three independent findall()s: the
    #    old code paired ids[i] with titles[i+1] to skip the feed title, which
    #    silently mismatches title and link the moment an entry lacks one.
    if len(out) < limit:
        try:
            r = requests.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": f"all:{query}",
                        "max_results": (limit - len(out)) * 2},
                headers={"User-Agent": "Helga/1.0 (Socratic Tutor)"},
                timeout=15)
            if r.status_code == 200:
                for entry in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
                    if len(out) >= limit:
                        break
                    m_id = re.search(r"<id>\s*(http[^<]*abs[^<]*)</id>", entry)
                    m_ti = re.search(r"<title>(.*?)</title>", entry, re.S)
                    m_su = re.search(r"<summary>(.*?)</summary>", entry, re.S)
                    abstract = _clean_abstract(m_su.group(1) if m_su else "")
                    if not m_id or len(abstract) < PRIMARY_MIN_ABSTRACT:
                        continue
                    out.append({
                        "url": m_id.group(1).strip(),
                        "title": _WS.sub(
                            " ", (m_ti.group(1) if m_ti else "arXiv preprint")
                        ).strip()[:200],
                        "type": "preprint",
                        "source": "arXiv",
                        "domain_tier": 1,
                        "text": abstract[:2500],
                    })
        except Exception as e:
            logger.debug(f"arxiv lookup failed for {query!r}: {e}")

    cache.set(key, out, expire=CACHE_TTL_SEARCH)
    return out


def wiki_lookup(title):
    """Wikipedia page for an EXACT title, or None.

    The miss is cached as "" and read back with `or None`, the same shape
    wiki_search_title uses. It stored `None`, which diskcache cannot tell apart
    from a cache miss (`cache.get` returns None for both), so the negative
    cache never once served a hit: the cascade tries up to three candidates per
    concept and every failing one was re-fetched from Wikipedia on every
    concept of every build, forever. That is the majority of lookups — a
    pedagogical concept title usually has no article — so the dead branch was
    costing a network round trip on nearly every attempt.
    """
    key = cache_key("wiki", title)
    cached = cache.get(key)
    if cached is not None:
        return cached or None

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
        return None          # transient failure: do not poison the cache

    cache.set(key, "", expire=CACHE_TTL_SEARCH)
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


# --- Subject selection -------------------------------------------------------
#
# THE DEFECT THIS REPLACES: `subject = (module_title or course_title or title)`.
# On the hydration path `module_title` is ALWAYS non-empty, so the two largest
# contributors to combined_text — two textbook extracts at 6000 chars each, and
# the primary literature — were keyed on the MODULE. They were disk-cached on
# the module, so concept 1 and concept 12 of a module received byte-identical
# grounding, and `_is_relevant` judged relevance against the module title
# rather than the concept. Only SearXNG was concept-specific, so with SearXNG
# down a 12-concept module had ONE grounding blob repeated twelve times.
#
# The original reasoning was sound and is kept: generated concept titles are
# pedagogical TASKS ("Identify the Right Angle"), and a task matches no
# textbook and no paper. The error was making the module the KEY instead of the
# FALLBACK. So the concept is tried first, then the concept in its module's
# context, then the module, then the course — and the cache key follows the
# query, so distinct concepts cannot collapse onto one entry.


def _lookup_subjects(title, module_title, course_title, broaden=False):
    """Subjects to search for one concept, most specific first."""
    ordered = [title]
    if title and module_title:
        # A task title plus its module reads as a topic to a search index:
        # "Identify the Right Angle" alone matches nothing, "Identify the Right
        # Angle Right Triangles" retrieves right-triangle material.
        ordered.append(f"{title} {module_title}")
    ordered += [module_title, course_title]

    if broaden:
        # See api_research_concept: broadening must WIDEN the net, never swap
        # it, so the narrow subjects stay at the front of the list.
        try:
            try:      # container (flat)
                from syllabus_sources import discover_broader_subjects
            except ImportError:
                from services.research.syllabus_sources import (
                    discover_broader_subjects)
            ordered += discover_broader_subjects(
                module_title or course_title or title)
        except Exception as e:
            logger.debug(f"broadening subjects failed: {e}")

    seen, out = set(), []
    for s in ordered:
        s = (s or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _gather(fn, subjects, limit):
    """Run a lookup over `subjects` until `limit` distinct results are held.

    Falls back down the specificity ladder AND tops up from it: a concept that
    yields one textbook of its own gets its second from the module rather than
    being replaced by the module's two.
    """
    out, seen = [], set()
    for subject in subjects:
        if len(out) >= limit:
            break
        try:
            found = fn(subject, limit=limit - len(out)) or []
        except Exception as e:
            logger.debug(f"lookup failed for {subject!r}: {e}")
            continue
        for r in found:
            url = r.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(r)
            if len(out) >= limit:
                break
    return out


# --- Assembling what the model actually reads --------------------------------
#
# THE RULE: **a source may only be cited if its text reached the model.**
#
# Three ways that was violated, all of which produced citations for text the
# course never used:
#   - primary literature contributed a heading and a URL and nothing else, yet
#     rendered as a citation and scored 0.25 (fixed in primary_source_lookup by
#     requiring an abstract);
#   - domain sources were appended to `sources` unconditionally but to the
#     prompt only `if ds.get("text")`, and an empty `url` rendered as `[Title]()`;
#   - combined_text was truncated to 3000 words AFTER assembly, so the tail was
#     always cut — and the tail was the web extracts, i.e. the only
#     concept-specific text in the whole blob — while `sources` still listed
#     them.
#
# So text is now budgeted BEFORE assembly, per source, and the citation list is
# built from what survived. Kinds are interleaved rather than concatenated so
# that running out of budget degrades every kind evenly instead of deleting
# whichever kind happens to be last.

WORD_BUDGET = 3000
MIN_USEFUL_WORDS = 40             # below this an extract teaches nothing
KIND_WORD_CAP = {
    "wikipedia": 700,
    "textbook": 800,
    "web": 400,
    "journal": 300, "preprint": 300,
    "primary_document": 150, "artefact": 150, "structured_fact": 80,
}
DEFAULT_WORD_CAP = 200

# Order within a round: the most teachable material first, so that when the
# budget runs out mid-round it is the catalogue metadata that goes.
_KIND_ORDER = ("wikipedia", "textbook", "web", "journal", "preprint",
               "primary_document", "artefact", "structured_fact")


def _interleave(entries):
    """Round-robin the entries by kind, best kind first within each round."""
    groups = {}
    for e in entries:
        groups.setdefault(e["kind"], []).append(e)
    order = ([k for k in _KIND_ORDER if k in groups]
             + [k for k in groups if k not in _KIND_ORDER])
    out = []
    while any(groups[k] for k in order):
        for k in order:
            if groups[k]:
                out.append(groups[k].pop(0))
    return out


def _assemble(entries, budget=WORD_BUDGET):
    """(combined_text, cited) — cited holds ONLY entries whose text got in."""
    parts, cited, dropped = [], [], 0
    remaining = budget
    for e in _interleave(entries):
        words = (e.get("text") or "").split()
        if not words or not e.get("url"):
            # No text, or nothing to link to. Either way it cannot honestly be
            # cited, and an empty url renders as "[Title]()" to the learner.
            dropped += 1
            continue
        if remaining < MIN_USEFUL_WORDS:
            dropped += 1
            continue
        take = words[:min(KIND_WORD_CAP.get(e["kind"], DEFAULT_WORD_CAP),
                          remaining)]
        parts.append(f"## Source: {e['label']} - {e['title']}\n{' '.join(take)}")
        remaining -= len(take)
        cited.append(e)
    if dropped:
        logger.debug(f"grounding: {dropped} source(s) not cited (no text, no "
                     f"url, or past the {budget}-word budget)")
    return "\n\n".join(parts), cited


def _citation(entry):
    """The public source record for an entry that made it into the prompt."""
    out = {"url": entry["url"], "title": entry["title"],
           "domain_tier": entry.get("tier", 1), "type": entry["kind"]}
    if entry.get("source"):
        out["source"] = entry["source"]
    return out


# --- Full research pipeline for one concept ---
async def _research_concept_async(title, module_title, course_title, mastery=1,
                                  broaden=False):
    entries = []

    subjects = _lookup_subjects(title, module_title, course_title, broaden)
    # Broadening widens the net: more subjects (above) and a higher ceiling on
    # each kind, so a concept that found one textbook can find a second.
    n_textbook = 3 if broaden else 2
    n_primary = 3 if broaden else (2 if (mastery or 1) >= 4 else 1)

    # Textbook and literature lookups walk at most this much of the ladder, and
    # skip the "concept + module" rung. Each rung is a real round trip and the
    # hydrator's HTTP timeout is 15 s: concept-specific grounding costs one
    # UNCACHED lookup per concept where module-keyed grounding cost one per
    # module, and that is the trade being made deliberately — but it should not
    # also pay for a rung that rarely pays out. The combined phrase earns its
    # place in Wikipedia search and SearXNG, which rank loose phrasing; a
    # MediaWiki book search or a Crossref query on it mostly returns the same
    # thing the concept alone did, and `_is_relevant` then judges the hit
    # against every word of the combination, which is stricter still.
    _combo = f"{title} {module_title}".strip() if (title and module_title) else None
    ladder = [s for s in subjects if s != _combo][:4 if broaden else 3]

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
    for candidate in subjects:
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
            if candidate != (title or "").strip():
                logger.info(
                    f"wiki: '{title}' had no page; grounded via '{candidate}'")
            break
    if wiki_result:
        entries.append({
            "kind": "wikipedia", "label": "Wikipedia",
            "title": wiki_result["title"], "url": wiki_result["url"],
            "text": wiki_result["text"], "tier": 1,
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
    for ps in _gather(primary_source_lookup, ladder, n_primary):
        entries.append({
            "kind": ps["type"], "label": ps.get("source", "literature"),
            "title": ps["title"], "url": ps["url"],
            "text": ps.get("text", ""), "tier": 1,
            "source": ps.get("source"),
        })

    # 2a-bis. OPEN TEXTBOOKS — the shape of source a COURSE actually wants.
    #
    # Papers report the frontier; a course teaches the canon. You do not build
    # introductory biology from a 2024 Nature letter, you build it from a
    # textbook. Wikibooks and Wikiversity are exactly that: content already
    # selected, sequenced and explained for a learner.
    for tb in _gather(textbook_lookup, ladder, n_textbook):
        entries.append({
            "kind": "textbook", "label": tb["source"], "title": tb["title"],
            "url": tb["url"], "text": tb["text"], "tier": 1,
            "source": tb["source"],
        })

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
        # The concept first here too: a museum or an archive is one of the few
        # sources that CAN answer a narrow question ("Name the Short Sides"
        # cannot be looked up, but "Impressionist brushwork" can), and this was
        # the one lookup already receiving the concept title.
        for ds in fetch_domain_sources(title or (subjects[0] if subjects else ""), _domains):
            entries.append({
                "kind": ds["type"], "label": ds["source"], "title": ds["title"],
                "url": ds.get("url", ""), "text": ds.get("text", ""),
                "tier": 1, "source": ds["source"],
            })
    except Exception as e:
        logger.debug(f"domain sources failed: {e}")

    # 2b. Generate search queries (mastery-aware). SearXNG is the only
    #     concept-specific source that always was, so the concept queries stay
    #     first; broadening APPENDS module/course queries rather than replacing
    #     them, because a retry that loses the narrow hits is not a broader
    #     search, it is a different one.
    queries = build_search_queries(title, module_title, mastery)
    if broaden:
        for extra in subjects[2:4]:
            queries.extend(build_search_queries(extra, course_title, mastery)[:1])

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
        for result in top_results[:4 if broaden else 3]:
            text = await extract_page(session, result["url"])
            if text:
                entries.append({
                    "kind": "web",
                    "label": urlparse(result["url"]).netloc or "web",
                    "title": result["title"], "url": result["url"],
                    "text": text, "tier": result["tier"],
                })

    # Assemble under the word budget, then cite ONLY what got in.
    combined_text, cited = _assemble(entries)
    sources = [_citation(e) for e in cited]

    # Confidence is computed from the SOURCE DICTS, not from hand-counted
    # kinds. Counting by hand is what made this function blind to three of the
    # seven kinds it produces — see ranking.SOURCE_KIND_WEIGHTS for the full
    # history. Scoring the cited list rather than everything fetched also makes
    # the number mean what it says: how well grounded the text the model read
    # actually was.
    confidence = confidence_from_sources(sources)

    wikipedia_data = next(
        (wiki_result for e in cited if e["kind"] == "wikipedia"), None)

    return {
        "sources": sources,
        "wikipedia": wikipedia_data,
        "combined_text": combined_text,
        "confidence": confidence,
        # Diagnostics: which subject actually grounded this concept is the
        # first thing anyone asks when a module's concepts look identical.
        "subjects_tried": subjects,
        "broadened": bool(broaden),
    }


def research_concept_sync(title, module_title, course_title, mastery=1,
                          broaden=False):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _research_concept_async(title, module_title, course_title, mastery,
                                    broaden)
        )
    finally:
        loop.close()


# --- Flask routes ---

@app.route("/api/research_concept", methods=["POST"])
def api_research_concept():
    """Ground one concept.

    `broaden` IS NOW READ. It was accepted and ignored: the caller retries a
    below-floor concept with `broaden: True` and an unchanged module_title, and
    since every substantial lookup was keyed on the module, the retry hit cache
    and returned byte-identical sources and an identical confidence — which
    then failed the caller's strict `>` comparison, deterministically, after up
    to 20 seconds. Every concept that lacked a textbook, primary or web hit paid
    that cost on every build (Wikipedia alone scores 0.40 against a 0.50 floor).

    Implemented rather than removed, because the caller does not ask whether
    the server supports it: it retries whenever confidence < floor, so deleting
    the parameter would leave the wasted round trip in place and only remove
    the chance of it helping. Broadening now genuinely widens the search — more
    subjects (module, course, and the discipline from Wikipedia's categories)
    and a higher ceiling per source kind — while KEEPING the narrow queries, so
    a retry can only ever add to what the first pass found.
    """
    data = request.get_json(force=True)
    title = data.get("title", "")
    module_title = data.get("module_title", "")
    course_title = data.get("course_title", "")
    mastery = data.get("mastery", 1)
    broaden = bool(data.get("broaden", False))

    if not title:
        return jsonify({"error": "title required"}), 400

    result = research_concept_sync(title, module_title, course_title, mastery,
                                   broaden)
    return jsonify(result)


@app.route("/api/research_batch", methods=["POST"])
def api_research_batch():
    """Ground several concepts in one request.

    NO CALLER USES THIS. The hydrator issues N single POSTs instead, which is
    N HTTP round trips and N event loops for work that shares almost all of its
    lookups — every concept in a module falls back to the same module and
    course subjects. Wiring it up is a change to course_builder.py, which is
    not this service's to make; the endpoint is kept correct and ready.

    What is fixed here: it built a fresh event loop per concept, and it dropped
    concepts whose research raised, returning a short dict with no indication
    which ones were missing.
    """
    data = request.get_json(force=True)
    concepts = data.get("concepts", [])
    course_title = data.get("course_title", "")
    mastery = data.get("mastery", 1)
    broaden = bool(data.get("broaden", False))

    async def _run_all():
        out = {}
        for concept in concepts:
            title = concept.get("title", "")
            if not title:
                continue
            uid = concept.get("uid", title)
            try:
                out[uid] = await _research_concept_async(
                    title, concept.get("module_title", ""), course_title,
                    mastery, broaden)
            except Exception as e:
                logger.warning(f"batch research failed for {title!r}: {e}")
                out[uid] = {"sources": [], "wikipedia": None,
                            "combined_text": "", "confidence": 0.0,
                            "error": str(e)[:200]}
        return out

    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(_run_all())
    finally:
        loop.close()

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
