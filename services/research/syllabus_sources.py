"""Real syllabus structure for a subject, from open textbooks.

THE PROBLEM THIS SOLVES
-----------------------
Course skeletons were invented by the LLM alone: "give me modules and concepts
for X". Nothing checked that the result resembled how the subject is actually
taught, and the measured consequence was a Pythagorean-theorem course covering
**42% of its own subject** — no triples, no distance formula, no converse. Every
structural detector we run reported that course as clean, because it *was*
structurally clean. It was just missing more than half the material.

An LLM asked to enumerate a syllabus produces what it can recall of one. A real
textbook's chapter list IS a syllabus, written by someone who teaches the
subject. Handing that to the model turns it from an AUTHOR of imagined structure
into an EDITOR of real structure, which is a much easier job to do well.

WHAT WORKS, AND WHAT LOOKS LIKE IT SHOULD BUT DOESN'T
-----------------------------------------------------
Measured 2026-08-04:

- **Wikipedia section headings — REJECTED as a skeleton.** They are encyclopedic,
  not pedagogical. `Cell biology` gives: History, Techniques, Pathology, Cell
  biologists, See also, References, External links. As a course that is close to
  useless. Kept only as a weak secondary signal for terminology.

- **Wikibooks CHAPTER SUBPAGES — the good source.** A Wikibooks book is stored as
  `Book/Chapter` pages, so the chapter list is the table of contents. Measured:
    Cell Biology                     -> 21 chapters (Cell division, Cell types,
                                        Cytosol, Endoplasmic Reticulum, Energy
                                        supply, Gene expression, Genetic material)
    Anatomy and Physiology of Animals -> 22 (Body Organisation, Cardiovascular
                                        System, Endocrine System, Nervous System)
    Introduction to Psychology        -> 45
    Linear Algebra                    -> 154
  That is a syllabus.

- Front and back matter has to be stripped or it becomes course content. Real
  entries seen: Authors, Glossary, Bibliography, Appendix, Acknowledgements.
"""

import logging
import os
import re
import threading
import time

import requests

logger = logging.getLogger(__name__)

UA = {"User-Agent": "Helga/1.0 (Socratic Tutor)"}

WIKIBOOKS_API = "https://en.wikibooks.org/w/api.php"
WIKIVERSITY_API = "https://en.wikiversity.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"

# Chapter titles that are apparatus, not subject matter. Every one of these was
# observed in a real book's chapter list.
_NON_CONTENT = {
    "authors", "glossary", "glossary by lesson", "bibliography", "appendix",
    "appendices", "acknowledgements", "acknowledgments", "index", "cover",
    "print version", "printable version", "references", "further reading",
    "about", "about this book", "contributors", "license", "licence",
    "table of contents", "contents", "introduction to the book", "preface",
    "front matter", "back matter", "resources", "meta", "see also",
    "external links", "notes", "sources", "credits", "faq", "wikibookians",
}

_NON_CONTENT_PATTERNS = (
    # "Appendix A", "Appendix B", "Appendix C" all leaked past an exact-match
    # set; so did "Chapter 1 - HS", which is a numbering artefact, not a topic.
    re.compile(r"^appendix\b", re.I),
    re.compile(r"^chapter\s*\d+\b", re.I),
    re.compile(r"^(part|unit|section|lesson)\s*\d+\s*$", re.I),
    re.compile(r"^\d+[\s.\-]*$"),                    # bare numbers
    re.compile(r"^(all\s+)?(pages|subpages)$", re.I),
    re.compile(r"^(test|sandbox|todo|draft)\b", re.I),
    re.compile(r"^(solutions?|answers?)\b", re.I),   # answer keys, not teaching
    re.compile(r"^(motivation|introduction)$", re.I),
    # Wikibooks books carry editorial pages inside the chapter namespace:
    # "How to edit this book" shipped into a cell-biology brief.
    re.compile(r"^how to\b", re.I),
    re.compile(r"\b(this book|this wikibook|editing)\b", re.I),
    re.compile(r"^(authors?|editors?|planning|development stages?)\b", re.I),
)


def _is_content_chapter(title):
    low = title.strip().lower()
    if not low or low in _NON_CONTENT:
        return False
    return not any(p.match(low) for p in _NON_CONTENT_PATTERNS)


# Disk cache for every MediaWiki lookup.
#
# _get_json is the single chokepoint: _search_book, _chapters_of,
# _wikipedia_sections and the Wikiversity course shapes all route through it.
# Caching here therefore covers the whole research surface in one place.
#
# WHY IT MATTERS BEYOND SPEED. Wikimedia throttles bursts, and a throttled reply
# is indistinguishable from "no such book" at this layer. That ambiguity is what
# made a build go UNGUIDED: the third candidate query in a burst returned empty
# and was read as absence of evidence. Serving repeat lookups from disk removes
# most of the burst, so the ambiguity mostly stops arising.
#
# SUCCESSES ONLY. Caching a throttled miss would make a transient failure
# permanent for the whole TTL -- the exact confusion this is meant to prevent.
# Open textbooks change on the scale of months, so 7 days is conservative.
try:
    from diskcache import Cache as _DiskCache
    _HTTP_CACHE = _DiskCache(
        os.path.join(os.getenv("DATA_ROOT", "/app/data"), "cache", "syllabus_http"))
    _HTTP_TTL = 7 * 24 * 3600
except Exception:                      # diskcache absent (unit tests)
    _HTTP_CACHE = None
    _HTTP_TTL = 0


def _cache_on():
    return (_HTTP_CACHE is not None
            and os.getenv("HELGA_RESEARCH_CACHE", "1").lower()
            not in ("0", "false", "no"))


_FETCH_STATS = threading.local()


def fetch_stats_reset():
    """Begin recording lookup outcomes on this thread."""
    _FETCH_STATS.d = {"ok": 0, "cached": 0, "failed": 0, "throttled": 0}


def fetch_stats():
    """Lookup outcomes since the last reset on this thread, else None."""
    d = getattr(_FETCH_STATS, "d", None)
    return dict(d) if d is not None else None


def _tally(key):
    d = getattr(_FETCH_STATS, "d", None)
    if d is not None:
        d[key] = d.get(key, 0) + 1


def _get_json(url, params, timeout=15, attempts=3):
    """GET returning parsed JSON, or None. Cached on success.

    Wikimedia rate-limits bursts and answers with a non-JSON body rather than a
    429, so `.json()` raises and the whole lookup silently returns nothing.
    Observed repeatedly while probing. Retry with backoff, and honour
    Retry-After when the server does send a 429.

    Returning None for BOTH "no such book" and "three attempts all died" is the
    absent-vs-zero confusion again, and it is the one that actually hurt: a
    throttled reply read as *absence of evidence* is what let a build go
    unguided while reporting success. This layer cannot resolve the ambiguity --
    a caller asking for one page has no idea a burst is in progress -- so it
    records the outcome instead, and `curriculum_brief` reads the tally to tell
    "we looked and found nothing" apart from "we could not look."
    """
    ck = None
    if _cache_on():
        ck = "wiki:" + url + ":" + repr(sorted((params or {}).items()))
        hit = _HTTP_CACHE.get(ck)
        if hit is not None:
            _tally("cached")
            return hit

    throttled = False
    for attempt in range(attempts):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                if _cache_on() and ck:
                    try:
                        _HTTP_CACHE.set(ck, data, expire=_HTTP_TTL)
                    except Exception as e:
                        logger.debug(f"cache write failed: {e}")
                _tally("ok")
                return data
            if r.status_code == 429:
                # The server told us how long to wait; guessing is worse.
                throttled = True
                wait = float(r.headers.get("Retry-After", 0) or 0) or 1.5 * (attempt + 1)
                logger.info(f"{url}: 429, waiting {wait:.1f}s")
                time.sleep(min(wait, 10))
                continue
        except Exception as e:
            logger.debug(f"{url} attempt {attempt + 1} failed: {e}")
        time.sleep(0.6 * (attempt + 1))
    _tally("throttled" if throttled else "failed")
    return None


def _search_book(api, query, limit=3):
    """Find candidate book/page titles on a MediaWiki site."""
    data = _get_json(api, {
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": limit, "srnamespace": 0, "format": "json",
    })
    if not data:
        return []
    return [h["title"] for h in (data.get("query") or {}).get("search", [])
            if h.get("title")]


def _chapters_of(api, book_title, cap=60):
    """Top-level chapters of a MediaWiki book, in the site's own order.

    A Wikibooks book lives at `Book/Chapter`, so `apprefix=Book/` IS the table
    of contents. Only depth-1 subpages count: `Book/Chapter/Section` is detail
    inside a chapter, and flattening it would turn one chapter into twenty
    modules.
    """
    data = _get_json(api, {
        "action": "query", "list": "allpages", "apprefix": f"{book_title}/",
        "apnamespace": 0, "aplimit": 500, "format": "json",
    })
    if not data:
        return []
    pages = (data.get("query") or {}).get("allpages", [])

    out = []
    for p in pages:
        title = p.get("title", "")
        if title.count("/") != 1:
            continue
        chapter = title.split("/", 1)[1].strip()
        if _is_content_chapter(chapter):
            out.append(chapter)
    return out[:cap]


def _wikipedia_sections(subject):
    """Weak secondary signal — terminology, NOT structure.

    Section headings are encyclopedic: `Cell biology` yields History,
    Techniques, Pathology, See also. Useful for vocabulary, useless as a
    syllabus, and treated as such by the caller.
    """
    data = _get_json(WIKIPEDIA_API, {
        "action": "parse", "page": subject.replace(" ", "_"),
        "prop": "sections", "format": "json",
    })
    if not data:
        return []
    secs = (data.get("parse") or {}).get("sections", [])
    return [x["line"] for x in secs
            if x.get("line") and _is_content_chapter(x["line"])]


_RELEVANCE_STOP = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "introduction", "basics", "fundamentals", "principles", "theorem", "theory",
}

# Level markers that actually appear in open-textbook titles, mapped to the
# mastery scale (1 Quick Overview .. 5 Graduate). A "Primary Mathematics" is a
# genuinely different book from "High School Mathematics Extensions", and
# picking the wrong one is how a high-school course ends up teaching arithmetic.
_LEVEL_MARKERS = {
    1: ("primary", "elementary", "basic", "beginner", "kids", "children",
        "for dummies", "simple"),
    2: ("high school", "secondary", "intro", "introductory", "gcse", "a-level"),
    3: ("college", "undergraduate", "university", "general"),
    4: ("advanced", "honors", "honours", "upper", "intermediate"),
    5: ("graduate", "postgraduate", "research", "advanced topics", "phd"),
}


def _topic_terms(topic):
    """Content words from a topic, for relevance scoring."""
    return {w for w in re.findall(r"[a-z]+", (topic or "").lower())
            if len(w) > 2 and w not in _RELEVANCE_STOP}


def _level_of_title(book):
    """Which mastery level does this book's title advertise? None if silent."""
    t = (book or "").lower()
    for level, markers in _LEVEL_MARKERS.items():
        if any(m in t for m in markers):
            return level
    return None


def _relevance(topic, book, chapters, subjects=None, mastery=None):
    """How well does this book serve THIS topic at THIS level?

    WHY THIS IS NOT JUST KEYWORD OVERLAP
    ------------------------------------
    Three failure modes, all observed:

      * WRONG BOOK, RIGHT LENGTH. Candidates were pooled across
        [topic] + broader_subjects and the first with enough chapters won, so
        'The Pythagorean Theorem' selected *Primary Mathematics* -- 24 chapters
        of generic arithmetic.

      * WRONG SUBJECT. Topic terms are sparse after stopwords ('The Pythagorean
        Theorem' reduces to {'pythagorean'}), so a geography book and a maths
        book can both score zero and the tiebreak becomes arbitrary. The
        DISCIPLINE is the signal that separates them, and it is already known --
        the builder derives it to broaden the search.

      * WRONG LEVEL. 'Primary Mathematics' and 'High School Mathematics
        Extensions' are both real maths books; only one suits a given course.

    So the score combines topic fit, discipline fit and level fit. Chapters
    weigh more than titles throughout: a broad title can have apt contents
    (Geometry does teach Pythagoras) and a narrow one can not.
    """
    chapter_blob = " ".join(chapters or []).lower()
    title = (book or "").lower()
    score = 0.0

    # 1. TOPIC FIT — does it actually teach this thing?
    terms = _topic_terms(topic)
    if terms:
        score += (sum(1 for t in terms if t in chapter_blob) / len(terms)) * 3.0
        score += (sum(1 for t in terms if t in title) / len(terms)) * 1.5

    # 2. DISCIPLINE FIT — is it even the right field? This is what stops a
    #    geography text being chosen for a maths course.
    subj_terms = set()
    for sub in (subjects or []):
        subj_terms |= _topic_terms(sub)
    if subj_terms:
        score += (sum(1 for t in subj_terms if t in title) / len(subj_terms)) * 2.0
        score += (sum(1 for t in subj_terms if t in chapter_blob) / len(subj_terms)) * 1.0

    # 3. LEVEL FIT — right field, right topic, wrong audience is still wrong.
    if mastery:
        advertised = _level_of_title(book)
        if advertised is not None:
            gap = abs(advertised - int(mastery))
            score += {0: 1.5, 1: 0.5}.get(gap, -1.0 * (gap - 1))
        # a title that says nothing about level is neutral, not penalised

    # 4. SHAPE SANITY — a course-shaped book, not a two-chapter stub or a
    #    200-heading encyclopedia dump.
    n = len(chapters or [])
    if n > 120:
        score -= 1.0
    return score


def subject_outline(topic, broader_subjects=None, min_chapters=4, mastery=None):
    """How this subject is actually organised, from open textbooks.

    Returns:
        {
          "topic":     the query,
          "outlines":  [{source, book, chapters:[...]}, ...]   strongest first,
          "vocabulary": [...],       encyclopedic headings, terminology only
          "found":     bool,
        }

    Never raises. An empty result means the skeleton builder falls back to
    unguided generation — degraded, but it says so rather than pretending.
    """
    # A NARROW TOPIC HAS NO BOOK OF ITS OWN, and that is the case that produced
    # the 42%-coverage course. "the pythagorean theorem" matches no Wikibook,
    # so unguided generation was all there was. Broadening to the discipline
    # finds real syllabi: Geometry has 61 chapters, Trigonometry 114.
    queries = [topic] + [b for b in (broader_subjects or []) if b]

    outlines = []
    for api, label in ((WIKIBOOKS_API, "Wikibooks"),
                       (WIKIVERSITY_API, "Wikiversity")):
        candidates = []
        for q in queries:
            candidates.extend(_search_book(api, q, limit=3))
        # Score several viable candidates and keep the most RELEVANT, rather
        # than the first that happens to be long enough. Capped because each
        # candidate costs an HTTP round trip; briefs are cached upstream, so a
        # rebuild of the same subject pays this once.
        scored = []
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if len(scored) >= 4:
                break
            chapters = _chapters_of(api, candidate)
            # A book with two chapters is a stub. Using it would narrow the
            # course to whatever those two happen to be, which is worse than
            # not using it at all.
            if len(chapters) >= min_chapters:
                scored.append((_relevance(topic, candidate, chapters,
                                          subjects=broader_subjects,
                                          mastery=mastery),
                               candidate, chapters))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            score, candidate, chapters = scored[0]
            if len(scored) > 1:
                logger.info(
                    f"subject_outline({topic!r}) on {label}: chose {candidate!r} "
                    f"(relevance {score:.2f}) over "
                    + ", ".join(f"{c!r} ({sc:.2f})" for sc, c, _ in scored[1:]))
            outlines.append({
                "source": label,
                "book": candidate,
                "url": f"https://en.{label.lower()}.org/wiki/"
                       + candidate.replace(" ", "_"),
                "chapters": chapters,
                "relevance": round(score, 3),
            })

    return {
        "topic": topic,
        "outlines": outlines,
        "vocabulary": _wikipedia_sections(topic)[:20],
        "found": bool(outlines),
    }


def format_for_prompt(outline, max_chapters=40):
    """Render an outline as evidence for the skeleton builder.

    Deliberately labelled as *evidence to adapt*, not a template to copy: the
    learner's scope and mastery sliders still decide how much of it becomes a
    course, and a 154-chapter book is not a 5-module course.
    """
    if not outline or not outline.get("found"):
        return ""

    parts = ["HOW THIS SUBJECT IS ACTUALLY TAUGHT",
             "(chapter lists from open textbooks — real syllabi, not invented)"]
    for o in outline["outlines"]:
        chapters = o["chapters"][:max_chapters]
        parts.append(f"\n{o['source']} — \"{o['book']}\" ({len(o['chapters'])} chapters):")
        parts.extend(f"  - {c}" for c in chapters)
        if len(o["chapters"]) > max_chapters:
            parts.append(f"  ... and {len(o['chapters']) - max_chapters} more")

    if outline.get("vocabulary"):
        parts.append("\nTerminology seen in reference material: "
                     + ", ".join(outline["vocabulary"][:12]))
    return "\n".join(parts)
