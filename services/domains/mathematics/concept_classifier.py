"""Classify mathematical concepts by READING them, when titles do not say enough.

WHY PATTERNS ARE NOT ENOUGH
---------------------------
Mathematics titles are thinner than programming ones. "Working with the Chain
Rule" could be the rule itself, its proof, or a set of exercises; "Eigenvalues"
names an object and says nothing about how the section treats it. The pattern
classifier answers where it is confident and returns UNKNOWN otherwise, which
is correct — a wrong kind teaches the concept the wrong way.

But UNKNOWN is not neutral. It costs the concept:

  * the per-kind teaching guidance, so a NOTATION concept and a PROOF concept
    are taught identically — and a learner is asked to *derive* a convention
  * its build-time aid, because worked-example mining only fires for the kinds
    that can use one

The standing NEVER_SOLVE rule still applies to UNKNOWN concepts, so the floor
is safe. This raises the ceiling.

WHY BUILD TIME AND WHY PER LESSON
---------------------------------
Classifying at teaching time would add a model call to every turn on a machine
where turn latency is already the acute defect. Per LESSON rather than per
CONCEPT because the model reads the section once and types all of that lesson's
concepts together — cheaper, and more consistent, since concepts from one
section get kinds that agree with each other.

Patterns run FIRST and their answer is kept when confident. This only fills
gaps, so a build with no model degrades to today's behaviour rather than losing
classification entirely.

Named `concept_classifier`, NOT `classify`: a submodule called `classify.py`
binds as a package attribute and SHADOWS the `classify` function the registry
contract requires. That happened in the computer-science package, where
`ext.classify(...)` returned a module object while the contract report happily
said nothing was missing.
"""
import logging

logger = logging.getLogger(__name__)

from services.domains.mathematics.concept_kind import (  # noqa: E402
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
    "DEFINITION": "what a mathematical object IS — true by definition, "
                  "nothing to prove",
    "NOTATION": "symbols and how they are written — pure convention, cannot "
                "be reasoned out",
    "THEOREM": "a claim that is true UNDER CONDITIONS, and provable",
    "PROOF": "why a theorem is true — the argument itself",
    "PROCEDURE": "a repeatable method the learner must eventually perform",
    "REPRESENTATION": "what something LOOKS like — a graph, a shape, a "
                      "geometric meaning",
    "APPLICATION": "modelling a real situation; the assumptions are the "
                   "teachable part",
    "ESTIMATION": "magnitude, plausibility, sanity-checking a result",
    "MISCONCEPTION": "a known, named error learners reliably make",
}


def _course_line(topic):
    """The COURSE the concepts belong to, stated before anything else.

    A concept title does not carry its own subject. "Vectors" is a data
    structure, a matrix column or a disease carrier depending only on the
    course around it, and this prompt used to show the model the lesson title
    and the concept names with no way to tell which. The registry already had
    to learn this the hard way — a course whose modules were "Mosquitoes and
    malaria" routed to mathematics on the keyword "vector".

    Empty string when unknown, so the prompt degrades to what it was rather
    than announcing an absence.
    """
    t = (topic or "").strip()
    return f"### COURSE: {t}\n\n" if t else ""

def _prompt(lesson_title, concept_titles, source_text, topic=None):
    kinds = "\n".join(f"- {k}: {v}" for k, v in _KIND_BRIEF.items())
    concepts = "\n".join(f"- {t}" for t in concept_titles)
    if not (source_text or "").strip():
        # Says the source is ABSENT rather than showing an empty header, which
        # would invite the model to read it as unreadable — absent-vs-degraded,
        # in a prompt.
        return (
            f"{_course_line(topic)}### SECTION: {lesson_title}\n"
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
        f"{_course_line(topic)}### SOURCE TEXT — section '{lesson_title}'\n"
        f"{(source_text or '')[:5000]}\n\n"
        f"### TASK\n"
        f"Classify each concept below by WHAT KIND OF MATHEMATICAL KNOWLEDGE "
        f"it is, based on the source text above. The kind decides how it gets "
        f"taught, so a wrong kind means the tutor teaches it the wrong way.\n\n"
        f"KINDS:\n{kinds}\n\n"
        f"CONCEPTS:\n{concepts}\n\n"
        f"Rules:\n"
        f"- Judge from the SOURCE TEXT, not from the title alone.\n"
        f"- DEFINITION is what a thing IS. THEOREM is a claim that is true "
        f"under conditions. PROOF is why that claim holds. These are taught "
        f"in three different ways and must not be confused.\n"
        f"- NOTATION is a CONVENTION — if the section explains what a symbol "
        f"means rather than why something is true, it is NOTATION.\n"
        f"- If the section is built around worked examples of a method, that "
        f"is PROCEDURE. If it is built around a graph or a picture, that is "
        f"REPRESENTATION.\n\n"
        f'Return STRICT JSON: {{"concepts": [{{"title": "...", "kind": "...", '
        f'"why": "<6 words>"}}]}}'
    )


def classify_course(course, book, llm_json_fn=None, status_callback=None,
                    topic=None):
    """Give every concept a kind, reading the source where patterns fall short.

    Mutates `course` in place, setting `concept_kind` on each concept. Returns
    a tally. Never raises: a classification failure must cost the guidance, not
    the build.
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
                               [c["title"] for c in needs_reading], text, topic=topic or course.get("title")),
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
            logger.warning(f"[MATH] concept classification failed for "
                           f"{lesson.get('title','')!r}: {e}")
            for c in needs_reading:
                c["concept_kind"] = UNKNOWN
                tally["unknown"] += 1

        if status_callback and i % 5 == 0:
            try:
                status_callback(f"MATH:CLASSIFY:{i}:{total}")
            except Exception:
                pass

    logger.info(f"[MATH] classified {tally['by_pattern']} by pattern, "
                f"{tally['by_reading']} by reading, {tally['unknown']} unknown "
                f"({tally['calls']} calls)")
    return tally
