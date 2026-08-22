"""OpenStax as a structured mathematics source, not a wall of scraped text.

WHY THIS EXISTS SEPARATELY FROM THE GENERIC BOOK READER
-------------------------------------------------------
The generic path — download an EPUB, flatten the HTML, split into chapters —
loses the two things that make a mathematics textbook teachable:

1. THE MATHEMATICS. OpenStax serves MathML. Flattening it produces false
   statements: "3 2 = 9" for 3²=9, and a square root that vanishes without
   trace. See `mathml.py`; that conversion is not optional, it is the
   difference between teaching mathematics and teaching nonsense.

2. THE WORKED EXAMPLES. OpenStax marks them SEMANTICALLY —
   `[data-type=example]` wrapping an `.os-problem-container` and an
   `.os-solution-container`. Measured on Calculus Volume 1 §1.1: 11 complete
   worked examples on a single page, each with its problem and full solution
   already separated for us. Flattened to text they become indistinguishable
   from surrounding prose, and the single most valuable teaching asset in the
   book is gone.

This is the mathematics counterpart of `computer_science/devdocs.py`: where a
domain has a STRUCTURED source, use the structure. The generic crawl is the
fallback, not the default.

WHAT IT REFUSES
---------------
A page whose examples have no solution yields no worked example. An exercise
with the answer in the back of the book is not a worked example, and presenting
one as if it were would have the tutor promise a solution it cannot show.

ROBOTS: WHY CONTENT IS NOT FETCHED FROM THE ARCHIVE API
-------------------------------------------------------
OpenStax `robots.txt` disallows the path the content actually lives on:

    Disallow: /apps/archive
    Disallow: /contents
    User-agent: GPTBot
    Disallow: /books/

So this module reads METADATA ONLY — the release manifest and the CMS
catalogue, neither of which is disallowed — and gets the syllabus that way.
BOOK CONTENT comes from a locally supplied file, which is the channel OpenStax
distributes for offline use and the one an offline-first tutor should be using
anyway.

`parse_book_html` below applies the same MathML conversion and the same
worked-example mining to a local OpenStax EPUB, because the EPUB carries the
identical `os-*` markup. Nothing is lost by respecting the rule.

(Note for maintainers: `services/research/syllabus_sources.py` DOES fetch
`/apps/archive` directly, with no robots check. That predates this module and
is flagged, not imitated.)
"""
import logging
import re

logger = logging.getLogger(__name__)

RELEASE = "https://openstax.org/rex/release.json"
#: `cnx_id` is the field that carries the ARCHIVE uuid. Without it the API
#: returns a Wagtail page id, which no content endpoint accepts.
CMS = ("https://openstax.org/apps/cms/api/v2/pages/"
       "?type=books.Book&fields=title,cnx_id&limit=200")

#: OpenStax's own semantic markers. These are stable across their maths titles
#: and are the whole reason this module beats a generic crawl.
SEL_EXAMPLE = {"data-type": "example"}
CLS_PROBLEM = "os-problem-container"
CLS_SOLUTION = "os-solution-container"
CLS_FIGURE = "os-figure"
CLS_CAPTION = "os-caption"
CLS_NOTE = "os-note-body"

#: Front and back matter carry no teachable mathematics.
_SKIP_TITLES = re.compile(
    r"^\s*(preface|index|answer key|appendix|about the authors?|"
    r"acknowledg|review exercises?|chapter review|key (terms|equations|"
    r"concepts))", re.I)

MIN_SOLUTION_CHARS = 40
MAX_BLOCK_CHARS = 2000


def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _json(fetch, url):
    import json
    try:
        raw = fetch(url)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.debug(f"[MATH] openstax fetch failed {url}: {e}")
        return None


def _text(node):
    """Node text with MathML already converted to LaTeX."""
    if node is None:
        return ""
    return re.sub(r"[ \t]+", " ", node.get_text(" ")).strip()


def catalogue(fetch):
    """{title: uuid} for published OpenStax books, or {}."""
    data = _json(fetch, CMS)
    out = {}
    for item in (data or {}).get("items", []):
        title = (item.get("title") or "").strip()
        uid = item.get("cnx_id")
        if title and uid:
            out[title] = uid
    return out


def release(fetch):
    """(archive_path, {uuid: version}) or (None, {})."""
    data = _json(fetch, RELEASE)
    if not data:
        return None, {}
    archive = data.get("archiveUrl") or data.get("archive_url")
    versions = {}
    for uid, meta in (data.get("books") or {}).items():
        v = meta.get("defaultVersion") if isinstance(meta, dict) else None
        if v:
            versions[uid] = v
    return archive, versions


#: Which OpenStax book teaches which subject. Explicit, because the automatic
#: matcher picks "Algebra 1" for "linear algebra" — a high-school book for a
#: university subject, which is worse than returning nothing.
#: ORDER IS SPECIFIC-FIRST, and matching is on WORD BOUNDARIES. Both are
#: needed. Substring matching sent "Precalculus" to *Calculus Volume 1* —
#: a calculus text for a precalculus student — which is the same defect the CS
#: domain hit when a bare "api" inside "therapist" routed a therapy course to
#: computer science. Word boundaries fix that one (\bcalculus\b does not match
#: inside "precalculus"); ordering is still needed for genuine prefixes like
#: "business statistics" containing "statistics".
BOOK_FOR = (
    ("precalculus", ("Precalculus 2e", "Precalculus")),
    ("business statistics", ("Introductory Business Statistics 2e",)),
    ("calculus", ("Calculus Volume 1", "Calculus Volume 2", "Calculus Volume 3")),
    ("trigonometry", ("Algebra and Trigonometry 2e", "Algebra and Trigonometry")),
    ("college algebra", ("College Algebra 2e", "College Algebra")),
    ("intermediate algebra", ("Intermediate Algebra 2e", "Intermediate Algebra")),
    ("elementary algebra", ("Elementary Algebra 2e", "Elementary Algebra")),
    ("prealgebra", ("Prealgebra 2e", "Prealgebra")),
    ("statistics", ("Statistics", "Introductory Statistics 2e")),
    ("probability", ("Statistics", "Introductory Statistics 2e")),
    ("contemporary mathematics", ("Contemporary Mathematics",)),
    ("algebra", ("College Algebra 2e", "College Algebra")),
)

#: Subjects OpenStax genuinely does not cover. Named so the caller falls back
#: to the researched path rather than being handed a wrong-level book.
NOT_COVERED = ("linear algebra", "differential equations", "abstract algebra",
               "real analysis", "topology", "number theory", "discrete math",
               "complex analysis", "numerical analysis")


def book_for(subject):
    """The OpenStax title that teaches `subject`, or None.

    None is a real answer: OpenStax has no linear algebra book, and returning
    "Algebra 1" for "linear algebra" — which the generic relevance matcher
    does — builds a university course from a high-school text.
    """
    s = (subject or "").strip().lower()
    if not s:
        return None
    for bad in NOT_COVERED:
        if re.search(r"\b" + re.escape(bad) + r"\b", s):
            logger.info(f"[MATH] OpenStax does not cover {subject!r}")
            return None
    for key, titles in BOOK_FOR:
        if re.search(r"\b" + re.escape(key) + r"\b", s):
            return titles
    return None


def resolve(subject, fetch):
    """(archive, uuid, version, title) for `subject`, or None."""
    titles = book_for(subject)
    if not titles:
        return None
    archive, versions = release(fetch)
    if not archive:
        return None
    cat = catalogue(fetch)
    for want in titles:
        for title, uid in cat.items():
            if title.strip().lower() == want.lower() and uid in versions:
                return archive, uid, versions[uid], title
    return None


def syllabus(subject, fetch):
    """The chapter/section outline for a subject, or None.

    Metadata only — the CMS catalogue and release manifest, both permitted.
    This is the SYLLABUS: how the subject is really organised, which is the
    thing an LLM invents badly and a textbook already knows.
    """
    r = resolve(subject, fetch)
    if not r:
        return None
    archive, uid, version, title = r
    return {"book": title, "uuid": uid, "version": version,
            "archive": archive,
            "note": "content must come from a local copy; /apps/archive is "
                    "disallowed by robots.txt"}


def parse_book_html(html):
    """One OpenStax page or chapter of LOCAL book HTML as teachable material.

    Returns {text, examples, figures, notes, math_count}. The input is HTML
    from a locally supplied EPUB — the same `os-*` markup the website serves,
    so nothing is lost by not crawling.
    """
    from services.domains.mathematics.mathml import replace_math

    soup = _soup(html)
    if soup is None:
        return None

    # BEFORE anything reads text. Every extraction below depends on the
    # mathematics already being LaTeX rather than flattened digits.
    converted = replace_math(soup)

    examples = []
    for node in soup.find_all(attrs=SEL_EXAMPLE):
        problem = node.find(class_=CLS_PROBLEM)
        solution = node.find(class_=CLS_SOLUTION)
        if problem is None or solution is None:
            continue                      # an exercise, not a worked example
        p, sol = _text(problem), _text(solution)
        if len(sol) < MIN_SOLUTION_CHARS:
            continue                      # "See Answer Key" is not a solution
        examples.append({
            "problem": p[:MAX_BLOCK_CHARS],
            "solution": sol[:MAX_BLOCK_CHARS],
            "steps": _steps(solution),
        })

    figures = []
    for fig in soup.find_all(class_=CLS_FIGURE):
        cap = fig.find(class_=CLS_CAPTION)
        img = fig.find("img")
        alt = img.get("alt") if img else None
        caption = _text(cap)
        if caption:                       # an uncaptioned figure is decoration
            figures.append({"caption": caption[:400], "alt": (alt or "")[:300]})

    notes = [_text(n)[:MAX_BLOCK_CHARS] for n in soup.find_all(class_=CLS_NOTE)]

    return {
        "text": _text(soup),
        "examples": examples,
        "figures": figures,
        "notes": [n for n in notes if n],
        "math_count": converted,
    }


_STEP_SPLIT = re.compile(r"(?:^|\n)\s*(?:Step\s+\d+[.:)]|\d+[.)])\s+", re.I)


def _steps(solution_node):
    """The solution's steps, if it is written as steps.

    A step list is what makes a worked example teachable one move at a time —
    the tutor can show the whole solution and ask why ONE step is licensed,
    rather than asking about the solution as an undifferentiated block.
    """
    text = _text(solution_node)
    parts = [p.strip() for p in _STEP_SPLIT.split(text) if p.strip()]
    return parts[:12] if len(parts) >= 2 else []
