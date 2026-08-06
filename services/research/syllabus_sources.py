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
import re
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


def _get_json(url, params, timeout=15, attempts=3):
    """GET returning parsed JSON, or None.

    Wikimedia rate-limits bursts and answers with a non-JSON body rather than a
    429, so `.json()` raises and the whole lookup silently returns nothing.
    Observed repeatedly while probing. Retry with backoff.
    """
    for attempt in range(attempts):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.debug(f"{url} attempt {attempt + 1} failed: {e}")
        time.sleep(0.6 * (attempt + 1))
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


# Category names that describe the ARTICLE rather than the subject. Wikipedia
# carries dozens of these on every page ("Articles with short description",
# "CS1 maint: url-status", "Webarchive template wayback links"); handing them
# to a syllabus search is the same failure as the `prop=links` experiment
# recorded in curriculum_research — noise the model cannot distinguish from
# signal.
_MAINTENANCE_CATEGORY = re.compile(
    r"^(articles?|pages?|wikipedia|cs1|category|all |use |webarchive|"
    r"short description|commons|wikidata|good articles|featured|"
    r"engvarb|harv|isbn|infobox|redirects?|template|portal)",
    re.I)


def discover_broader_subjects(topic, limit=3):
    """Parent disciplines for a narrow topic, from Wikipedia's categories.

    (Named `discover_` rather than `broader_subjects` only because
    `subject_outline` takes a parameter of that name — a function shadowed by
    an argument in the one place it is most likely to be needed is a trap.)

    A NARROW TOPIC HAS NO BOOK OF ITS OWN, and that is the case that produced
    the 42%-coverage course: "the pythagorean theorem" matches no Wikibook, so
    unguided generation was all there was. `subject_outline` has always
    accepted `broader_subjects` for exactly this, but nothing ever passed it,
    so the parameter — and the fix it represents — was dead.

    Categories are used rather than `prop=links` deliberately. Links come back
    alphabetically, which is how "Actinide chemistry" ended up adjacent to cell
    biology; categories are a curated classification, so
    "the pythagorean theorem" yields Euclidean geometry / Theorems about right
    triangles / Trigonometry — the discipline a real textbook is written about.

    Returns [] on any failure: broadening is an improvement to grounding, never
    a precondition for it.
    """
    data = _get_json(WIKIPEDIA_API, {
        "action": "query", "prop": "categories", "titles": topic,
        "cllimit": 50, "clshow": "!hidden", "redirects": 1, "format": "json",
    })
    if not data:
        return []
    out = []
    for page in ((data.get("query") or {}).get("pages") or {}).values():
        for cat in page.get("categories") or []:
            name = (cat.get("title") or "").split(":", 1)[-1].strip()
            if not name or _MAINTENANCE_CATEGORY.match(name):
                continue
            # "Theorems about right triangles" is a subject; "1970 births" and
            # "Articles containing proofs" are not. Length is a crude but
            # effective filter against category prose.
            if len(name) > 60 or any(ch.isdigit() for ch in name):
                continue
            if name.lower() != (topic or "").lower() and name not in out:
                out.append(name)
    return out[:limit]


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


def subject_outline(topic, broader_subjects=None, min_chapters=4):
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
        for candidate in candidates:
            chapters = _chapters_of(api, candidate)
            # A book with two chapters is a stub. Using it would narrow the
            # course to whatever those two happen to be, which is worse than
            # not using it at all.
            if len(chapters) >= min_chapters:
                outlines.append({
                    "source": label,
                    "book": candidate,
                    "url": f"https://en.{label.lower()}.org/wiki/"
                           + candidate.replace(" ", "_"),
                    "chapters": chapters,
                })
                break                       # best book per site is enough

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
