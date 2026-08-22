"""Classify concepts by READING them, when their titles do not say enough.

WHY PATTERNS ARE NOT ENOUGH
---------------------------
`concept_kind.classify` matches titles against patterns, and on a real dbt
course 25 of 40 lessons came back UNKNOWN. That is not a weak pattern list; it
is the wrong instrument. "Using defer in dbt" does not tell you whether it is a
procedure, a mechanism or reference material — you have to read the page.

An unclassified concept is not neutral. It gets:

  * no teaching guidance, so the tutor treats syntax and mechanism identically
    and asks a student to *derive* a `ref()` call
  * no code aid, because `code_examples` only fires for code-shaped kinds

So UNKNOWN is a silent downgrade of both halves of the product.

WHY BUILD TIME AND WHY PER LESSON
---------------------------------
Classifying at teaching time would add a model call to every turn on a machine
where turn latency is already the acute defect. Classifying per CONCEPT would
cost one call each; per LESSON the model reads the chapter once and types all
of that lesson's concepts together, which is both cheaper and more consistent —
concepts from one chapter get kinds that agree with each other.

The pattern classifier still runs first and its answer is kept when it is
confident. This only fills the gaps, so a build with no model degrades to
today's behaviour rather than losing classification entirely.
"""
import logging

logger = logging.getLogger(__name__)

from services.domains.computer_science.concept_kind import (  # noqa: E402
    classify as pattern_classify, UNKNOWN, RANK, GUIDANCE,
)

KINDS = [k for k in RANK if k != UNKNOWN]

SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": KINDS},
                    "why": {"type": "string"},
                },
                "required": ["title", "kind"],
            },
        }
    },
    "required": ["concepts"],
}

_KIND_BRIEF = {
    "ORIENTATION": "what a thing IS and why it exists",
    "TOOLING": "installing, configuring, running a command",
    "SYNTAX": "the literal form of the language — what to type and where",
    "PROCEDURE": "a repeatable how-to the learner must be able to perform",
    "MECHANISM": "how or why something works underneath",
    "DEBUGGING": "reading an error and finding the cause",
    "CONVENTION": "true by decision, with no derivation",
    "REFERENCE": "lookup material — flags, parameters, endpoints",
}


def _prompt(lesson_title, concept_titles, chapter_text):
    kinds = "\n".join(f"- {k}: {v}" for k, v in _KIND_BRIEF.items())
    concepts = "\n".join(f"- {t}" for t in concept_titles)
    return (
        f"### SOURCE TEXT — lesson '{lesson_title}'\n"
        f"{(chapter_text or '')[:5000]}\n\n"
        f"### TASK\n"
        f"Classify each concept below by WHAT KIND OF KNOWLEDGE it is, based on "
        f"the source text above. The kind decides how it gets taught, so a "
        f"wrong kind means the tutor teaches it the wrong way.\n\n"
        f"KINDS:\n{kinds}\n\n"
        f"CONCEPTS:\n{concepts}\n\n"
        f"Rules:\n"
        f"- Judge from the SOURCE TEXT, not from the concept's title alone.\n"
        f"- SYNTAX is what to type. MECHANISM is why it works. A concept about "
        f"writing a model is PROCEDURE; a concept about how the DAG is built "
        f"from refs is MECHANISM. They are taught differently and must not be "
        f"confused.\n"
        f"- If the source text shows commands or config, that leans TOOLING or "
        f"PROCEDURE; if it explains a cause, that leans MECHANISM.\n\n"
        f'Return STRICT JSON: {{"concepts": [{{"title": "...", "kind": "...", '
        f'"why": "<6 words>"}}]}}'
    )


def classify_course(course, book, llm_json_fn=None, status_callback=None):
    """Give every concept a kind, reading the source where patterns fall short.

    Mutates `course` in place, setting `concept_kind` on each concept. Returns a
    tally. Never raises: a classification failure must cost the guidance, not
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
        ch = None
        try:
            order = lesson.get("book_chapter")
            ch = book.chapter(order) if (book and order is not None) else None
        except Exception:
            ch = None
        text = getattr(ch, "text", "") or ""

        # Patterns first — free, and right often enough to matter.
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
        if not llm_json_fn or not text:
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
            logger.warning(f"[CS] concept classification failed for "
                           f"{lesson.get('title','')!r}: {e}")
            for c in needs_reading:
                c["concept_kind"] = UNKNOWN
                tally["unknown"] += 1

        if status_callback and i % 5 == 0:
            try:
                status_callback(f"CS:CLASSIFY:{i}:{total}")
            except Exception:
                pass

    logger.info(f"[CS] classified {tally['by_pattern']} by pattern, "
                f"{tally['by_reading']} by reading, {tally['unknown']} unknown "
                f"({tally['calls']} calls)")
    return tally
