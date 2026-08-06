"""Phase-1 research: gather and ANALYSE material before any structure exists.

WHY THIS IS A SEPARATE PHASE
----------------------------
The research node used to run only during hydration — Phase 2, once the
skeleton already existed. So the single most consequential decision in the
whole pipeline, *what this course is made of*, was taken with no evidence at
all: one LLM call, from recall. The measured result was a course covering 42%
of its own subject that passed every structural detector, because it was
structurally clean and substantively hollow.

Research now runs in BOTH phases:

    Phase 1  what should this course contain?   <- this module
    Phase 2  what does this concept say?        <- research_server.research_concept

WHAT THIS IS NOT
----------------
It is not a scraper that hands a table of contents to the builder to copy. A
copied Wikibooks TOC is not a course: it has the wrong scope, the wrong level,
the wrong sequence for the learner in front of us, and it is somebody else's
pedagogical argument. This gathers *evidence* — several independent accounts of
how a subject is organised — and the builder's job is SYNTHESIS: reconcile
them, select against the preset, and sequence for this learner.

The output is deliberately called a "brief", not an outline.

LEVEL AWARENESS
---------------
The preset is not a post-hoc filter, it steers the search. "Quick Overview" and
"Graduate Seminar" on the same topic should not retrieve the same material, and
retrieving graduate material for a beginner then asking the model to simplify
it produces the worst of both.
"""

import logging
import re
import time

# Dual import shape. Inside the research container these modules sit flat on
# sys.path; imported from services/core they are a package. Getting this wrong
# is not a small mistake — the identical error hid in the research Dockerfile
# and crash-looped that service for weeks, and here it silently returns "no
# evidence", which looks exactly like a subject with no textbooks.
try:  # container (flat)
    from syllabus_sources import (
        WIKIBOOKS_API, WIKIVERSITY_API, _chapters_of, _get_json, _search_book,
        discover_broader_subjects, subject_outline, vocabulary_line,
    )
except ImportError:  # imported as a package
    from services.research.syllabus_sources import (
        WIKIBOOKS_API, WIKIVERSITY_API, _chapters_of, _get_json, _search_book,
        discover_broader_subjects, subject_outline, vocabulary_line,
    )

logger = logging.getLogger(__name__)

UA = {"User-Agent": "Helga/1.0 (Socratic Tutor)"}
IA_SEARCH = "https://archive.org/advancedsearch.php"

# How the level dial changes what we go looking for. These are search steers,
# not filters applied afterwards.
LEVEL_PROFILE = {
    1: {"name": "introductory",
        "hints": ["introduction", "for beginners", "basics"],
        "ia_terms": "introduction OR primer OR beginners"},
    2: {"name": "secondary",
        "hints": ["high school", "introductory"],
        "ia_terms": "textbook AND (introduction OR school)"},
    3: {"name": "undergraduate",
        "hints": ["undergraduate", "course", "textbook"],
        "ia_terms": "textbook AND (undergraduate OR college)"},
    4: {"name": "advanced undergraduate",
        "hints": ["advanced", "theory", "analysis"],
        "ia_terms": "textbook AND (advanced OR theory)"},
    5: {"name": "graduate",
        "hints": ["graduate", "advanced treatment", "rigorous"],
        "ia_terms": "(graduate OR advanced) AND (theory OR treatise)"},
}


def _level_profile(mastery):
    return LEVEL_PROFILE.get(max(1, min(5, int(mastery or 1))), LEVEL_PROFILE[3])


def _internet_archive_books(subject, mastery, limit=6):
    """Canonical texts on the subject, level-steered.

    Titles alone are useful evidence: what books EXIST on a subject, and what
    they are called, says a great deal about how the field partitions itself.
    """
    prof = _level_profile(mastery)
    q = (f'title:({subject}) AND mediatype:texts AND ({prof["ia_terms"]})')
    data = _get_json(IA_SEARCH, {
        "q": q, "fl[]": ["identifier", "title", "year", "subject"],
        "rows": limit, "page": 1, "output": "json",
    }, timeout=25)
    if not data:
        return []
    docs = (data.get("response") or {}).get("docs", [])
    out, seen = [], set()
    for d in docs:
        title = d.get("title")
        if not title:
            continue
        if isinstance(title, list):
            title = title[0]
        title = str(title)[:160]
        # The Archive holds many scans of the same work; listing "Histology: a
        # text and atlas" three times misrepresents how much evidence there is.
        fingerprint = re.sub(r"[^a-z0-9]+", "", title.lower())[:60]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append({"title": title, "year": d.get("year"),
                    "identifier": d.get("identifier")})
    return out


def _wikiversity_course_shapes(subject, limit=3):
    """Wikiversity hosts COURSES, not just articles — the closest public
    analogue to what we are building, including how they sequence."""
    shapes = []
    for candidate in _search_book(WIKIVERSITY_API, subject, limit=limit):
        chapters = _chapters_of(WIKIVERSITY_API, candidate)
        if len(chapters) >= 3:
            shapes.append({"course": candidate, "sections": chapters})
    return shapes


# _related_subjects() WAS HERE AND HAS BEEN REMOVED.
#
# The idea was sound — spot what a syllabus ASSUMES but never teaches — but the
# signal was not. Wikipedia's `prop=links` returns page links alphabetically,
# so "cell biology" produced: Abiogenesis, Acrosome, Actinide chemistry,
# Adaptation, Adenosine triphosphate, Aerobiology. Actinide chemistry is not
# adjacent to cell biology; it is the letter A.
#
# Noise in a prompt is worse than absence: the model has no way to tell a bad
# signal from a good one, and would have dutifully worked "Actinide chemistry"
# into a biology syllabus. Prerequisite detection needs a real signal (article
# categories, or the sources' own "assumed knowledge" sections) and is left
# undone rather than faked.


def curriculum_brief(topic, mastery=3, scope=3, starting_from=1,
                     preset_label=None, broader_subjects=None):
    """Evidence for what a course on `topic` should contain, at this level.

    Returns a dict the builder turns into a prompt. Never raises: an empty
    brief means the builder generates unguided, and says so.

    `broader_subjects` are parent disciplines to search when the topic itself
    has no textbook of its own. Pass them if you already know them (the
    skeleton builder asks the model); otherwise they are discovered from
    Wikipedia's categories, but only after the narrow search has actually
    failed — a topic that has its own book should be described by that book,
    not by its discipline's.
    """
    prof = _level_profile(mastery)
    brief = {
        "topic": topic,
        "level": prof["name"],
        "preset": preset_label,
        "mastery": mastery, "scope": scope, "starting_from": starting_from,
        "syllabi": [], "courses": [], "canonical_texts": [],
        "vocabulary": [], "broadened_to": [], "found": False,
    }

    # 1. Open-textbook syllabi — the strongest structural evidence.
    try:
        outline = subject_outline(topic)
        # THE 42% CASE. subject_outline has always taken `broader_subjects` —
        # its docstring names it as the fix for the Pythagorean-theorem course
        # that covered 42% of its own subject — and this call never passed it,
        # so a narrow topic still got nothing and generation still fell back to
        # unguided. Broaden only on a miss, so the parameter costs one extra
        # request in exactly the case it exists for.
        if not outline.get("outlines"):
            wider = [b for b in (broader_subjects or []) if b]
            if not wider:
                try:
                    wider = discover_broader_subjects(topic)
                except Exception as e:
                    logger.debug(f"curriculum_brief: broadening failed: {e}")
            if wider:
                logger.info(
                    f"curriculum_brief({topic!r}): no book of its own — "
                    f"broadening to {wider}")
                brief["broadened_to"] = wider
                outline = subject_outline(topic, broader_subjects=wider)
        brief["syllabi"] = outline.get("outlines", [])
        brief["vocabulary"] = outline.get("vocabulary", [])
    except Exception as e:
        logger.warning(f"curriculum_brief: syllabi failed: {e}")

    time.sleep(0.4)   # Wikimedia rate-limits bursts; pacing beats retrying

    # 2. How other people sequence a COURSE on this.
    #    subject_outline already consults Wikiversity, so anything it found is
    #    dropped here — listing the same source twice under two headings makes
    #    one account look like two independent ones, which is exactly the
    #    signal the "appears in MOST of them" instruction relies on.
    try:
        already = {s.get("book") for s in brief["syllabi"]}
        brief["courses"] = [c for c in _wikiversity_course_shapes(topic)
                            if c["course"] not in already]
    except Exception as e:
        logger.debug(f"curriculum_brief: course shapes failed: {e}")

    # 3. What books exist at this level — evidence of how the field partitions.
    try:
        brief["canonical_texts"] = _internet_archive_books(topic, mastery)
    except Exception as e:
        logger.debug(f"curriculum_brief: canonical texts failed: {e}")

    # `found` means STRUCTURAL evidence, not merely any evidence.
    #
    # Book titles are useful colour — they show how a field partitions itself —
    # but they cannot tell you what a course should contain. A brief holding
    # six titles and zero chapters guided nothing, while reporting "6 sources"
    # and looking like a success. That is the same failure as scoring a missing
    # value as zero: it turns absent evidence into apparent evidence.
    brief["structural_sources"] = len(brief["syllabi"]) + len(brief["courses"])
    brief["chapter_count"] = (
        sum(len(x["chapters"]) for x in brief["syllabi"])
        + sum(len(c["sections"]) for c in brief["courses"]))
    brief["found"] = brief["chapter_count"] > 0
    if not brief["found"] and brief["canonical_texts"]:
        logger.warning(
            f"curriculum_brief({topic!r}): {len(brief['canonical_texts'])} book "
            f"titles but NO chapter structure — treating as no evidence")
    return brief


def format_brief(brief, max_items=30):
    """Render the brief as evidence, framed as material to SYNTHESISE.

    The framing is the important part. Handing a model a table of contents
    invites it to copy one, and a copied TOC is somebody else's course at
    somebody else's level. The instruction is to reconcile several accounts and
    build for THIS learner.
    """
    if not brief or not brief.get("found"):
        return ""

    L = [f"RESEARCH BRIEF — {brief['topic']} (target level: {brief['level']})",
         "Independent accounts of how this subject is organised. They will not "
         "agree, and they are not templates.", ""]

    for s in brief.get("syllabi", []):
        ch = s["chapters"][:max_items]
        L.append(f"[Syllabus] {s['source']} — \"{s['book']}\" ({len(s['chapters'])} chapters):")
        L.append("  " + "; ".join(ch))
        L.append("")

    for c in brief.get("courses", []):
        L.append(f"[Course] Wikiversity — \"{c['course']}\" sequences it as:")
        L.append("  " + "; ".join(c["sections"][:max_items]))
        L.append("")

    if brief.get("canonical_texts"):
        L.append(f"[Texts] Books written at the {brief['level']} level:")
        for t in brief["canonical_texts"][:8]:
            yr = f" ({t['year']})" if t.get("year") else ""
            L.append(f"  - {t['title']}{yr}")
        L.append("")

    # The brief has always collected `vocabulary` and never rendered it: the
    # only formatter that did was syllabus_sources.format_for_prompt, which
    # nothing called. Terminology is weak evidence for STRUCTURE — encyclopedic
    # headings are not a syllabus — but it is real evidence for the words a
    # subject uses, which is what stops a generated module list drifting into
    # paraphrase.
    vocab = vocabulary_line(brief.get("vocabulary"))
    if vocab:
        L += [f"[Terms] {vocab}", ""]

    L += [
        "HOW TO USE THIS — SYNTHESISE, DO NOT COPY.",
        "1. Reconcile the accounts. Topics that appear in MOST of them are "
        "load-bearing and belong in the course. A topic in only one is that "
        "author's emphasis, not the canon.",
        f"2. Cut to the requested scope ({brief['scope']}/5). A 60-chapter book "
        "is not a course; choose what carries the subject and drop the rest "
        "deliberately.",
        f"3. Pitch at {brief['level']} and start from the learner's stated "
        "background — do not inherit a source's level.",
        "4. Re-sequence for learning, not for reference. Source order is often "
        "alphabetical or historical; yours must be pedagogical.",
        "5. The result must be YOUR structure, defensible on its own terms — "
        "not any one source's table of contents rewritten.",
    ]
    return "\n".join(L)
