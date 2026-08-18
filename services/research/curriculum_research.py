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
import os
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
        fetch_stats, fetch_stats_reset, subject_outline,
    )
except ImportError:  # imported as a package
    from services.research.syllabus_sources import (
        WIKIBOOKS_API, WIKIVERSITY_API, _chapters_of, _get_json, _search_book,
        fetch_stats, fetch_stats_reset, subject_outline,
    )

try:
    import ratelimit as _rl
except ImportError:
    from services.research import ratelimit as _rl

logger = logging.getLogger(__name__)

UA = _rl.headers()   # contact info from HELGA_CONTACT, never fabricated
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


# Disk cache for briefs. Keyed on the inputs that change the answer.
#
# ONLY SUCCESSES ARE CACHED. A miss here is ambiguous — it may mean "this
# subject has no open textbook" or "Wikimedia throttled us this second" — and
# caching the second kind would make a transient failure permanent for the
# whole TTL. That is the same absent-vs-zero confusion that made a 6-title
# brief look like evidence, so it is worth the occasional re-query.
try:
    from diskcache import Cache as _DiskCache
    _BRIEF_CACHE = _DiskCache(
        os.path.join(os.getenv("DATA_ROOT", "/app/data"), "cache", "curriculum"))
    _BRIEF_TTL = 7 * 24 * 3600
except Exception:            # diskcache absent (unit tests) — degrade to no cache
    _BRIEF_CACHE = None
    _BRIEF_TTL = 0


def curriculum_brief(topic, mastery=3, scope=3, starting_from=1,
                     preset_label=None, broader_subjects=None, use_cache=True):
    """Evidence for what a course on `topic` should contain, at this level.

    Returns a dict the builder turns into a prompt. Never raises: an empty
    brief means the builder generates unguided, and says so.
    """
    # `use_cache=False` bypasses the disk cache entirely. Needed by anything
    # asserting on the LIVE source behaviour — a cached success would otherwise
    # satisfy a call whose sources were deliberately made to fail, which is
    # exactly how a cache turns a real regression into a green test.
    _cache_on = (use_cache
                 and os.getenv("HELGA_BRIEF_CACHE", "1").lower() not in ("0", "false", "no"))
    _ck = None
    if _BRIEF_CACHE is not None and _cache_on:
        _ck = f"brief:{topic}|{mastery}|{scope}|{starting_from}|{tuple(broader_subjects or ())}"
        _hit = _BRIEF_CACHE.get(_ck)
        if _hit:
            logger.info(f"curriculum_brief({topic!r}): cache hit "
                        f"({_hit.get('chapter_count', 0)} chapters)")
            return _hit

    fetch_stats_reset()
    prof = _level_profile(mastery)
    brief = {
        "topic": topic,
        "level": prof["name"],
        "preset": preset_label,
        "mastery": mastery, "scope": scope, "starting_from": starting_from,
        "syllabi": [], "courses": [], "canonical_texts": [], "found": False,
    }

    # 1. Open-textbook syllabi — the strongest structural evidence.
    #
    # `broader_subjects` exists precisely for the narrow-topic case that
    # produced the 42%-coverage course: "the pythagorean theorem" matches no
    # Wikibook, but Geometry has 31+ chapters. It was NOT being passed, so the
    # builder compensated with an outer loop that re-ran this whole sweep once
    # per candidate — three full Wikibooks+Wikiversity+Wikipedia+Archive passes.
    # Wikimedia throttles bursts, so the THIRD candidate (the one that works)
    # came back empty and the build went unguided. One call that broadens
    # internally is both correct and ~3x fewer requests.
    try:
        outline = subject_outline(topic, broader_subjects=broader_subjects,
                                  mastery=mastery)
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

    # "We looked and there is nothing" vs "we could not look" are different
    # facts, and only the first justifies building unguided. Lookups that never
    # completed are reported so the builder can say which one happened; a brief
    # assembled through a throttling storm is NOT evidence of absence.
    _st = fetch_stats() or {}
    brief["lookup_stats"] = _st
    brief["degraded"] = bool(_st.get("failed", 0) or _st.get("throttled", 0))
    if brief["degraded"]:
        logger.warning(
            f"curriculum_brief({topic!r}): DEGRADED — {_st.get('failed', 0)} "
            f"failed and {_st.get('throttled', 0)} throttled lookups. "
            f"found={brief['found']} is not trustworthy as evidence of absence.")

    if not brief["found"] and brief["canonical_texts"]:
        logger.warning(
            f"curriculum_brief({topic!r}): {len(brief['canonical_texts'])} book "
            f"titles but NO chapter structure — treating as no evidence")
    if (brief["found"] and not brief["degraded"]
            and _BRIEF_CACHE is not None and _cache_on and _ck):
        try:
            _BRIEF_CACHE.set(_ck, brief, expire=_BRIEF_TTL)
        except Exception as e:
            logger.debug(f"brief cache write failed: {e}")
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
