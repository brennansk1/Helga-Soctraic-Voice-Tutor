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
#: What the model may ANSWER — the kinds plus an explicit escape.
#:
#: The schema enum used to be `KINDS` alone, so a model told "answer UNKNOWN if
#: unsure" had no legal way to say it and was forced to pick a kind. That is
#: precisely the wrong-guidance-is-worse-than-none failure, manufactured by the
#: schema.
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
    "ORIENTATION": "what a thing IS and why it exists",
    "TOOLING": "installing, configuring, running a command",
    "SYNTAX": "the literal form of the language — what to type and where",
    "PROCEDURE": "a repeatable how-to the learner must be able to perform",
    "MECHANISM": "how or why something works underneath",
    "DEBUGGING": "reading an error and finding the cause",
    "CONVENTION": "true by decision, with no derivation",
    "REFERENCE": "lookup material — flags, parameters, endpoints",
    # THESE TWO WERE MISSING WHILE STILL BEING LEGAL ANSWERS.
    #
    # `ANSWERABLE` is built from `RANK`, which gained both kinds when the
    # domain learned to teach operated software. The brief did not, so the
    # schema offered the model two enum values it had never been told the
    # meaning of. Measured on a real SQL build: it answered TOOL_BOUNDARY for
    # "Index Scan Types", "Set Operation Efficiency" and "Adjacency List
    # Traversal" — pure MECHANISM — because the word "Efficiency" looks like a
    # trade-off. TOOL_BOUNDARY's guidance says "Do NOT answer it", so every one
    # of those would have had the tutor REFUSE to teach core material.
    #
    # Both briefs therefore lead with what DISQUALIFIES a concept.
    "TOOL_OPERATION": "operating a NAMED VENDOR PRODUCT's interface — where a "
                      "setting lives in Power BI, Tableau, n8n. Only when a "
                      "specific product's UI is the subject. NOT any "
                      "procedure that happens to involve software",
    "TOOL_BOUNDARY": "choosing WHICH TOOL OR LAYER a capability belongs in — "
                     "dbt or the BI tool, SQL or the visual builder. Requires "
                     "TWO DIFFERENT TOOLS OR LAYERS to choose between. "
                     "Comparing two features of the SAME language "
                     "(ROWS vs RANGE, UNION vs UNION ALL) is MECHANISM, NOT "
                     "this. A concept about which is faster is MECHANISM",
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

def _prompt(lesson_title, concept_titles, chapter_text, topic=None):
    kinds = "\n".join(f"- {k}: {v}" for k, v in _KIND_BRIEF.items())
    concepts = "\n".join(f"- {t}" for t in concept_titles)
    if not (chapter_text or "").strip():
        # TITLE-ONLY, and it says so. Showing an empty SOURCE TEXT header
        # invites the model to treat the source as unreadable rather than
        # absent — the absent-vs-degraded confusion, in a prompt.
        return (
            f"{_course_line(topic)}### LESSON: {lesson_title}\n"
            f"(No source text available — classify from the concept names and "
            f"the lesson they sit in.)\n\n"
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
        f"{_course_line(topic)}### SOURCE TEXT — lesson '{lesson_title}'\n"
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


def classify_course(course, book, llm_json_fn=None, status_callback=None,
                    topic=None):
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
        # NO SOURCE TEXT IS NOT A REASON TO GIVE UP.
        #
        # This bailed to UNKNOWN whenever `text` was empty — which is EVERY
        # concept of a typed-topic course, because that path has no book. So
        # the LLM classifier, the thing built precisely for titles the
        # patterns cannot read, never ran on the courses that needed it most.
        #
        # Patterns still run first: they are free and exact when they hit.
        # This is the tail, and a model that has never seen the chapter still
        # knows what "Slowly changing dimensions" or "Row-level security" IS.
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
