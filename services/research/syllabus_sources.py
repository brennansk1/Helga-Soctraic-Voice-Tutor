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

try:
    import ratelimit as _rl
except ImportError:
    from services.research import ratelimit as _rl

logger = logging.getLogger(__name__)

# Built from HELGA_CONTACT: Wikimedia's UA policy asks for contact info, and a
# fabricated address is worse than none.
UA = _rl.headers()

WIKIBOOKS_API = "https://en.wikibooks.org/w/api.php"
WIKIVERSITY_API = "https://en.wikiversity.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"

# OpenStax — peer-reviewed, openly licensed textbooks, most of them written to a
# named course (Prealgebra, Biology 2e, US History). Wikibooks is community
# writing of uneven completeness; OpenStax books are edited to a syllabus, which
# is exactly the artefact this module is trying to obtain. Two hops: the release
# manifest gives the current archive path and each book's pinned version, and the
# CMS gives titles. Both are stable for weeks and both go through _get_json, so
# they are cached and rate-limited like everything else.
OPENSTAX_RELEASE = "https://openstax.org/rex/release.json"
OPENSTAX_CMS = "https://openstax.org/apps/cms/api/v2/pages/"

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
    # Wikibooks pedagogical apparatus. Observed as real "chapters" of the
    # Linear Algebra book and backfilled into a course as if they were topics.
    "exploration", "automation", "laboratory", "computer algebra systems",
    "input", "output", "extra", "topic", "topics",
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


# The longest we will sit on a 429 before giving up on that source for
# this concept. A build makes hundreds of these calls; waiting out a
# multi-hour Retry-After would stall the whole course for one citation.
MAX_429_SLEEP_S = 10.0


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
            # A HOST UNDER A LONG BLOCK IS UNUSABLE, NOT SLOW.
            #
            # wait() sleeps out the whole Retry-After. With OpenAlex handing
            # back multi-hour 429s (capped at 300s here), three lookups spent
            # the concept's entire 75s research budget sleeping, and the
            # working provider — SearXNG, which answers in under a second —
            # was never reached. Measured: every NULL-semantics concept came
            # back with zero sources while a plain SearXNG query for the same
            # words returned twenty results.
            _blocked = _rl.blocked_for(url)
            if _blocked > _rl.MAX_BLOCK_WAIT_S:
                logger.info("%s is rate-limited for another %.0fs — skipping "
                            "it for this lookup rather than spending the "
                            "budget waiting", url, _blocked)
                _tally("throttled")
                return None
            _rl.wait(url)
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            _rl.note_response(url, r.status_code, r.headers)
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
                asked = float(r.headers.get("Retry-After", 0) or 0) or 1.5 * (attempt + 1)
                # LOG WHAT WE ACTUALLY DO.
                #
                # This printed the server's Retry-After and then slept the
                # capped value, so the log read "waiting 52740.0s" for a 10s
                # sleep. Reading that line during a build cost a wrong
                # diagnosis: it looks like the provider is out of action for
                # fourteen hours when it is out for ten seconds.
                wait = min(asked, MAX_429_SLEEP_S)
                if asked > wait:
                    logger.info(f"{url}: 429, asked for {asked:.0f}s — "
                                f"waiting {wait:.1f}s and moving on")
                else:
                    logger.info(f"{url}: 429, waiting {wait:.1f}s")
                time.sleep(wait)
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
        "action": "parse",
        "page": _resolve_wikipedia_title(subject).replace(" ", "_"),
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


def _title_match_bonus(topic, book):
    """Extra weight when the book IS the subject, not merely adjacent to it.

    MEASURED FAILURE: for topic "Linear Algebra", OpenStax *College Algebra*
    scored 4.75 against Wikibooks *Linear Algebra*'s 4.67 and won -- one shared
    token ("algebra") plus a level-marker bonus ("College") outweighed being the
    actual subject. Chapters from College Algebra (Exponential and Logarithmic
    Functions, Analytic Geometry, Probability) were then pulled into a linear
    algebra course.

    Topic fit alone cannot fix this: both books genuinely score on "algebra".
    What distinguishes them is that one title CONTAINS the whole topic and the
    other only overlaps it.
    """
    t, b = _normalise_for_match(topic), _normalise_for_match(book)
    if not t or not b:
        return 0.0
    if t == b:
        return 6.0            # the book is the subject
    if t in b or b in t:
        return 3.0            # "Linear Algebra" vs "Linear Algebra/Advanced"
    return 0.0


def _normalise_for_match(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


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
    return _title_match_bonus(topic, book) + score


def _openstax_release():
    """(archive_path, {uuid: version}) for the live release, retired books
    dropped. A retired edition still answers, and teaching from an edition
    OpenStax has withdrawn is a quiet way to teach superseded material."""
    data = _get_json(OPENSTAX_RELEASE, None, timeout=20)
    if not isinstance(data, dict):
        return None, {}
    books = {}
    for uid, meta in (data.get("books") or {}).items():
        meta = meta or {}
        if meta.get("retired"):
            continue
        if meta.get("defaultVersion"):
            books[uid] = meta["defaultVersion"]
    return data.get("archiveUrl"), books


def _openstax_catalogue():
    """{uuid: title} for published OpenStax books."""
    data = _get_json(OPENSTAX_CMS, {"type": "books.Book", "limit": 200,
                                    "fields": "title,cnx_id"}, timeout=20)
    if not isinstance(data, dict):
        return {}
    return {b["cnx_id"]: b["title"] for b in (data.get("items") or [])
            if isinstance(b, dict) and b.get("cnx_id") and b.get("title")}


def _openstax_chapters(archive, uid, version, cap=60, title=None):
    """Chapter titles for one book.

    ROBOTS — WHY THIS NO LONGER FETCHES FROM OPENSTAX
    -------------------------------------------------
    This used to GET `openstax.org{archive}/contents/{uid}@{version}.json`.
    OpenStax's robots.txt disallows BOTH `/apps/archive` AND `/contents` for
    the WILDCARD user-agent — not merely for GPTBot — so that request was a
    violation on every call. Nothing caught it because the rule lived in a
    comment instead of in code.

    LibreTexts republishes the same OpenStax books and permits reading content
    pages, and the chapter names are already in its sitemap. So the outline is
    obtainable without the violation and without any page fetch at all.

    `title` is the book's name and is what LibreTexts is asked for. The other
    arguments are kept because callers pass them and they still identify the
    edition in logs.

    The positional number is stripped ("05: Integration" -> "Integration") for
    the same reason the old markup path preferred the `os-text` span: a number
    left in place ends up inside a concept title downstream.
    """
    if not title:
        return []
    try:
        try:  # container (flat layout: modules live at /app)
            from libretexts import chapters_for
        except ImportError:  # imported as a package
            from services.research.libretexts import chapters_for
    except ImportError:  # container (flat)
        try:
            from libretexts import chapters_for
        except ImportError:
            logger.info("[OPENSTAX] libretexts reader unavailable; no outline")
            return []
    try:
        chapters = chapters_for(title)[:cap]
    except Exception as e:
        logger.info(f"[OPENSTAX] chapter lookup failed for {title!r}: {e}")
        return []
    if chapters:
        logger.info(f"[OPENSTAX] {title!r}: {len(chapters)} chapters via "
                    f"LibreTexts (the archive endpoint is robots-disallowed)")
    return chapters


def _openstax_chapters_DISALLOWED(archive, uid, version, cap=60):
    """RETAINED AS A RECORD OF WHAT MUST NOT BE DONE. Never call this.

    Kept so the next person who wants chapter data can see that the obvious
    endpoint was already tried and is not permitted, rather than re-adding it
    from a stale example — which is exactly how it got here.
    """
    raise RuntimeError(
        "openstax.org/apps/archive + /contents are disallowed by robots.txt "
        "for ALL agents. Use _openstax_chapters, which reads LibreTexts.")


def _openstax_outline(topic, broader_subjects=None, mastery=None, min_chapters=4):
    """Best-matching OpenStax book for the topic, scored like the others."""
    archive, versions = _openstax_release()
    if not archive or not versions:
        return None
    catalogue = _openstax_catalogue()
    if not catalogue:
        return None

    # Score on TITLE first and fetch chapters only for the top few: each book
    # tree is a large document, and the catalogue is 129 books.
    # SUBJECT FIT IS A GATE, NOT A SCORE COMPONENT. Scoring alone picked
    # "Introduction to Anthropology" for the Pythagorean theorem: it has no
    # topical overlap at all, but "Introduction to..." matched the level marker
    # and that was enough to make it positive. Level fit may only ever ORDER
    # books that are already about the right subject — it must never qualify one
    # that is not. This is the "geography textbook for a maths class" failure.
    wanted = set(_topic_terms(topic))
    for b in (broader_subjects or []):
        wanted |= set(_topic_terms(b))
    prelim = []
    for uid, title in catalogue.items():
        if uid not in versions:
            continue
        if not (wanted & set(_topic_terms(title))):
            continue
        score = _relevance(topic, title, [], subjects=broader_subjects,
                           mastery=mastery)
        prelim.append((score, uid, title))
    if not prelim:
        logger.debug(f"OpenStax: no book overlaps {topic!r} or {broader_subjects}")
        return None
    prelim.sort(key=lambda x: x[0], reverse=True)

    scored = []
    for _, uid, title in prelim[:3]:
        # `title` is now the lookup key — see `_openstax_chapters`. Omitting it
        # returns [] silently, which would drop OpenStax from every outline.
        chapters = _openstax_chapters(archive, uid, versions[uid], title=title)
        if len(chapters) >= min_chapters:
            scored.append((_relevance(topic, title, chapters,
                                      subjects=broader_subjects, mastery=mastery),
                           uid, title, chapters))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    score, uid, title, chapters = scored[0]
    if len(scored) > 1:
        logger.info(
            f"subject_outline({topic!r}) on OpenStax: chose {title!r} "
            f"(relevance {score:.2f}) over "
            + ", ".join(f"{t!r} ({sc:.2f})" for sc, _, t, _ in scored[1:]))
    return {
        "source": "OpenStax",
        "book": title,
        "url": f"https://openstax.org/details/books/{uid}",
        "chapters": chapters,
        "relevance": round(score, 3),
    }


# Category names that describe the ARTICLE rather than the subject. Every one
# of these was observed on a real page.
_ADMIN_CAT = re.compile(
    r"^(articles?|all |wikipedia|cs1|use \w+ english|short description|"
    r"webarchive|pages |commons |good articles|featured |mathematics articles|"
    r"unprintworthy|redirects|harv|engvarb|wikidata)", re.I)


def _plausible_article(title, expect_terms):
    """Does this article look like it is about the expected subject?

    Blind first-hit resolution picks the LEXICALLY closest article, not the
    topically right one. Measured:

        "Learning to be a Dungeon Master" -> an Ace Books NOVEL
                                             (categories: Ace Books books,
                                              Action novels)
        "Competitive Yo-Yo"               -> the toy's history
                                             (categories: 1930s fads and trends)

    So a resolution has to be checked, not trusted. Categories are the check:
    they are human-curated and they say what a page is ABOUT, which a title
    match does not.
    """
    if not expect_terms:
        return True
    cats = " ".join(wikipedia_parent_subjects(title, limit=8)).lower()
    if not cats:
        return True          # unknown, not disproven — do not reject on silence
    return any(t.lower() in cats for t in expect_terms if t)


def _resolve_wikipedia_title(topic, candidates=None, expect_terms=None):
    """The real article title for a topic, or the topic unchanged.

    Both Wikipedia lookups here did EXACT-TITLE matching, so a topic phrased the
    way a learner would phrase it returned nothing:

        "Dungeon Mastering"   -> 0 parents, 0 sections
        "Dungeon Master"      -> 1 parent,  2 sections
        "Dungeons & Dragons"  -> 2 parents, 26 sections

    A search for "Dungeon Mastering" returns "Dungeon Master" as its first hit,
    so the information was always one call away. This matters most for subjects
    with no textbook, where Wikipedia is the last line of evidence rather than a
    supplement — the case where returning nothing is most expensive.
    """
    if not topic:
        return topic

    # CANDIDATES FIRST, when a caller has better knowledge than a keyword match.
    # A model knows "Dungeon Mastering" means running a role-playing session and
    # will offer "Game master" or "Dungeon Master (Dungeons & Dragons)"; a search
    # engine offers whatever shares the most letters. The model PROPOSES and this
    # code DISPOSES — the proposal is checked against categories before use,
    # because asking a model to confirm its own suggestion is the self-referential
    # move this codebase keeps having to undo.
    for cand in (candidates or []):
        if not cand:
            continue
        hits = _search_book(WIKIPEDIA_API, cand, limit=1)
        if hits and _plausible_article(hits[0], expect_terms):
            if hits[0].strip().lower() != topic.strip().lower():
                logger.info(f"wikipedia: {topic!r} -> {hits[0]!r} (suggested)")
            return hits[0]

    for hit in _search_book(WIKIPEDIA_API, topic, limit=3):
        if _plausible_article(hit, expect_terms):
            if hit.strip().lower() != topic.strip().lower():
                logger.info(f"wikipedia: resolved {topic!r} -> {hit!r}")
            return hit

    logger.info(f"wikipedia: no article plausibly about {topic!r}")
    return topic


def wikipedia_parent_subjects(topic, limit=3):
    """Discipline names for a topic, from Wikipedia's own categories.

    A fallback for when the LLM cannot be reached to name the parent discipline.
    That lookup gates the whole grounding chain, and losing it turns a narrow
    topic into an unguided build -- so it needs a path that does not depend on
    the model being loaded. Categories are curated by people, cached here, and
    already rate-limited.

    "Pythagorean theorem" -> Euclidean geometry, Theorems about right triangles.
    """
    data = _get_json(WIKIPEDIA_API, {
        "action": "query", "prop": "categories",
        "titles": _resolve_wikipedia_title(topic),
        "cllimit": 30, "clshow": "!hidden", "format": "json",
        "redirects": 1,
    }, timeout=15)
    if not isinstance(data, dict):
        return []
    pages = ((data.get("query") or {}).get("pages") or {})
    out = []
    for page in pages.values():
        for cat in (page.get("categories") or []):
            name = (cat.get("title") or "").replace("Category:", "").strip()
            if not name or _ADMIN_CAT.match(name):
                continue
            # "Theorems about right triangles" is a fine discipline hint;
            # "1990 births" is not. Length is a crude but effective filter.
            if 3 <= len(name) <= 50 and name not in out:
                out.append(name)
    return out[:limit]


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

    # OpenStax last: it is the highest-quality source but the most expensive
    # lookup (release manifest + catalogue + a large book tree), so it runs once
    # per subject and is worth every bit of the caching in front of it.
    try:
        os_outline = _openstax_outline(topic, broader_subjects=broader_subjects,
                                       mastery=mastery, min_chapters=min_chapters)
        if os_outline:
            outlines.append(os_outline)
    except Exception as e:
        logger.warning(f"subject_outline({topic!r}): OpenStax failed: {e}")

    outlines.sort(key=lambda o: o.get("relevance", 0), reverse=True)
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

    vocab = vocabulary_line(outline.get("vocabulary"))
    if vocab:
        parts.append("\n" + vocab)
    return "\n".join(parts)


# BOTH BRANCHES BUILT THIS, UNDER DIFFERENT NAMES.
#
# `wikipedia_parent_subjects` above and main's `discover_broader_subjects` are
# the same idea, found independently from the same failure: a narrow topic has
# no book of its own, which is what produced the 42%-coverage Pythagorean
# course. Main's name is what `curriculum_research` imports, so it is kept as
# an alias rather than renaming a function this module already uses.
discover_broader_subjects = wikipedia_parent_subjects


def vocabulary_line(vocabulary, limit=12):
    """The terminology line, rendered in ONE place.

    `subject_outline` has always returned `vocabulary` — Wikipedia section
    headings, kept as a weak terminology signal — and `format_for_prompt` was
    the only thing that rendered it. Nothing called `format_for_prompt`, so the
    field was computed on every build and read by nobody. Both formatters now
    share this, so collecting it and using it cannot drift apart again.
    """
    terms = [t for t in (vocabulary or []) if t][:limit]
    if not terms:
        return ""
    return "Terminology seen in reference material: " + ", ".join(terms)
