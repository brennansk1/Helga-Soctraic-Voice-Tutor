"""Classify historical concepts by READING them, when titles do not say enough.

WHY PATTERNS ARE NOT ENOUGH HERE
--------------------------------
History titles are thin in a way that matters more than in the other domains,
because the kinds they fail to distinguish need OPPOSITE treatment.

"Appeasement" could be a `CONTESTED` question (historians argue fiercely about
Chamberlain's judgement), a `CHRONOLOGY` (the sequence from the Rhineland to
Prague), or a `FACT` (the Munich Agreement was signed on 30 September 1938).
The first must present two positions and refuse to resolve; the last must be
stated outright and never asked for. Getting that wrong is not a missed
opportunity — it is teaching the subject backwards.

WHAT UNKNOWN COSTS
------------------
An unclassified concept gets the standing rule (`NEVER_QUIZ`) but no per-kind
guidance and no mined material, because `source_mining` only fires for kinds
that can use it. The floor is safe; the ceiling is lost.

WHY BUILD TIME AND WHY PER LESSON
---------------------------------
Classifying at teaching time would add a model call to every turn on a machine
where turn latency is already the acute defect. Per LESSON rather than per
CONCEPT because the model reads the section once and types all of that
lesson's concepts together — cheaper, and more consistent, since concepts from
one section get kinds that agree with each other.

Patterns run FIRST and their answer is kept when confident, so a build with no
model degrades to today's behaviour rather than losing classification.

Named `concept_classifier`, NOT `classify`: a submodule called `classify.py`
binds as a package attribute and SHADOWS the `classify` function the registry
contract requires. That happened in the computer-science package, where
`ext.classify(...)` returned a module object while the contract report said
nothing was missing.
"""
import logging

logger = logging.getLogger(__name__)

from services.domains.history.concept_kind import (  # noqa: E402
    classify as pattern_classify, UNKNOWN, RANK,
)

KINDS = [k for k in RANK if k != UNKNOWN]
#: What the model may ANSWER — the kinds plus an explicit escape. The enum used
#: to be `KINDS` alone, so a model told "answer UNKNOWN if unsure" had no legal
#: way to say it and was forced to pick a kind: the schema manufacturing the
#: wrong-guidance-is-worse-than-none failure the prompt was trying to avoid.
ANSWERABLE = KINDS + [UNKNOWN]

SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": ANSWERABLE},
                    "why": {"type": "string"},
                },
                "required": ["title", "kind"],
            },
        }
    },
    "required": ["concepts"],
}

_KIND_BRIEF = {
    "FACT": "a contingent fact — a date, name, place or number. True because "
            "it happened; cannot be reasoned out",
    "CHRONOLOGY": "an ordered sequence where the ORDER carries the meaning",
    "CONTESTED": "historians genuinely disagree, and the disagreement is live",
    "CAUSATION": "why something happened; the weighting of causes is where "
                 "the argument is",
    "SOURCE": "a document and its provenance — who wrote it, when, for whom",
    "CONTEXT": "what else was happening at the time, and how it changes the "
               "reading",
    "SIGNIFICANCE": "why something matters, which is itself a judgement",
    "CONTINUITY": "what changed and what stayed the same",
    "MISCONCEPTION": "a known, named error learners reliably hold",
}


def _prompt(lesson_title, concept_titles, source_text):
    kinds = "\n".join(f"- {k}: {v}" for k, v in _KIND_BRIEF.items())
    concepts = "\n".join(f"- {t}" for t in concept_titles)
    if not (source_text or "").strip():
        # Says the source is ABSENT rather than showing an empty header, which
        # would invite the model to read it as unreadable — absent-vs-degraded,
        # in a prompt.
        return (
            f"### SECTION: {lesson_title}\n"
            f"(No source text available — classify from the concept names and "
            f"the section they sit in.)\n\n"
            f"### TASK\n"
            f"Classify each concept below by WHAT KIND OF KNOWLEDGE it is. "
            f"The kind decides how it gets taught.\n\n"
            f"KINDS:\n{kinds}\n\n"
            f"CONCEPTS:\n{concepts}\n\n"
            f"Answer UNKNOWN for anything you cannot place confidently: a "
            f"wrong kind gives the tutor actively wrong teaching "
            f"instructions, which is worse than giving it none.\n\n"
            f'Return STRICT JSON: {{"concepts": [{{"title": "...", '
            f'"kind": "...", "why": "<6 words>"}}]}}')
    return (
        f"### SOURCE TEXT — section '{lesson_title}'\n"
        f"{(source_text or '')[:5000]}\n\n"
        f"### TASK\n"
        f"Classify each concept below by WHAT KIND OF HISTORICAL KNOWLEDGE it "
        f"is, based on the source text above. The kind decides how it gets "
        f"taught, and two of these kinds need OPPOSITE treatment — a FACT must "
        f"be stated outright and never asked for, while a CONTESTED question "
        f"must be left open.\n\n"
        f"KINDS:\n{kinds}\n\n"
        f"CONCEPTS:\n{concepts}\n\n"
        f"Rules:\n"
        f"- Judge from the SOURCE TEXT, not the title alone.\n"
        f"- FACT is the most important to get right. If the concept IS a date, "
        f"a name, a place or a number, it is FACT whatever else it touches.\n"
        f"- CONTESTED requires that historians actually disagree — the text "
        f"should show competing positions, ideally attributed. Do not mark "
        f"something contested merely because it is complicated.\n"
        f"- CAUSATION explains why; CHRONOLOGY establishes when and in what "
        f"order. A section about the sequence of events is CHRONOLOGY even "
        f"when that sequence is causal.\n\n"
        f'Return STRICT JSON: {{"concepts": [{{"title": "...", "kind": "...", '
        f'"why": "<6 words>"}}]}}'
    )


def classify_course(course, book, llm_json_fn=None, status_callback=None):
    """Give every concept a kind, reading the source where patterns fall short.

    Mutates `course` in place, setting `concept_kind`. Returns a tally. Never
    raises: a classification failure must cost the guidance, not the build.
    """
    tally = {"by_pattern": 0, "by_reading": 0, "unknown": 0, "calls": 0}
    lessons = [l for m in (course.get("modules") or [])
               for u in (m.get("units") or [])
               for l in (u.get("lessons") or [])]
    total = len(lessons)

    for i, lesson in enumerate(lessons, 1):
        concepts = [c for c in (lesson.get("concepts") or [])
                    if (c.get("title") or "").strip()]
        if not concepts:
            continue

        text = ""
        try:
            order = lesson.get("book_chapter")
            chapter = book.chapter(order) if (book and order is not None) else None
            text = getattr(chapter, "text", "") or ""
        except Exception:
            text = ""
        if not text:
            text = lesson.get("source_text") or ""

        needs_reading = []
        for c in concepts:
            kind = pattern_classify(c["title"], text[:600],
                                    c.get("learning_objectives"))
            if kind != UNKNOWN:
                c["concept_kind"] = kind
                tally["by_pattern"] += 1
            else:
                needs_reading.append(c)

        if not needs_reading:
            continue
        # NO SOURCE TEXT IS NOT A REASON TO GIVE UP. This bailed to UNKNOWN
        # whenever `text` was empty — every concept of a typed-topic course,
        # which has no book — so the classifier built for exactly the titles
        # patterns cannot read never ran on the courses that needed it most.
        if not llm_json_fn:
            for c in needs_reading:
                c["concept_kind"] = UNKNOWN
                tally["unknown"] += 1
            continue

        try:
            raw = llm_json_fn(
                prompt=_prompt(lesson.get("title", ""),
                               [c["title"] for c in needs_reading], text),
                schema=SCHEMA, expected_type="dict", max_tokens=600)
            tally["calls"] += 1
            got = {}
            for item in (raw or {}).get("concepts", []):
                t = (item.get("title") or "").strip().lower()
                k = (item.get("kind") or "").strip().upper()
                if t and k in KINDS:
                    got[t] = k
            for c in needs_reading:
                k = got.get(c["title"].strip().lower())
                if k:
                    c["concept_kind"] = k
                    tally["by_reading"] += 1
                else:
                    c["concept_kind"] = UNKNOWN
                    tally["unknown"] += 1
        except Exception as e:
            logger.warning(f"[HIST] concept classification failed for "
                           f"{lesson.get('title','')!r}: {e}")
            for c in needs_reading:
                c["concept_kind"] = UNKNOWN
                tally["unknown"] += 1

        if status_callback and i % 5 == 0:
            try:
                status_callback(f"HIST:CLASSIFY:{i}:{total}")
            except Exception:
                pass

    logger.info(f"[HIST] classified {tally['by_pattern']} by pattern, "
                f"{tally['by_reading']} by reading, {tally['unknown']} unknown "
                f"({tally['calls']} calls)")
    return tally
