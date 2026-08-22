"""Mining worked examples from PLAIN TEXT, and attaching them at build time.

WHY A TEXT MINER WHEN `openstax.parse_book_html` EXISTS
------------------------------------------------------
`parse_book_html` reads OpenStax's semantic markup — `[data-type=example]`
wrapping problem and solution containers — and is near-perfect where it
applies. It applies only where the source is that markup.

The generic pipeline hands this domain a `Chapter` carrying **plain text**
(`services/research/book_reader.Chapter` has `title`, `text`, `order` and
nothing else). So a maths course built from any ordinary textbook — the normal
case — has no markup to read, and would get no worked examples at all.

Mathematics textbooks label worked examples in the text itself, because human
readers need to find them too: "EXAMPLE 3.4 ... Solution ...". That label is
the signal, and it is far more reliable than the code-fence heuristics the
computer-science domain has to use.

WHY THIS IS THE DOMAIN'S PRIMARY ASSET
--------------------------------------
For programming, the build-time asset is a code snippet. For mathematics it is
a complete worked solution, because the teaching move that replaces "now you
try" is *show the whole solution and ask what licenses one step*. Without a
worked example attached, a PROCEDURE concept has nothing to teach from but the
model's recollection — which is exactly the failure this pipeline exists to
avoid.

WHAT IT REFUSES
---------------
An "Example" with no solution, a solution shorter than a sentence, and anything
with no mathematics in it. A worked example the tutor cannot show in full is
worse than none: the turn promises a solution and then cannot deliver it.
"""
import logging
import re

logger = logging.getLogger(__name__)

from services.domains.mathematics import teaching_moves as tm  # noqa: E402
from services.domains.mathematics.concept_kind import (  # noqa: E402
    AIDED_KINDS_ORDER, UNKNOWN,
)

#: "EXAMPLE 3.4", "Example 12", "Worked Example 2" — the label a textbook uses
#: so a human can find it.
_EXAMPLE_HEAD = re.compile(
    r"(?:^|\n)\s*(?:worked\s+)?example\s*(\d+(?:\.\d+)?)?\s*[.:—-]?\s*",
    re.I)

#: The solution's own label. Without one there is no boundary between the
#: problem and its answer, and showing the wrong half teaches nothing.
_SOLUTION_HEAD = re.compile(
    r"(?:^|\n)\s*(?:solution|answer|working)\s*[.:—-]?\s*", re.I)

#: Where the example ends: the next example, an exercise block, or a heading.
_END = re.compile(
    r"(?:^|\n)\s*(?:example\b|exercises?\b|try it\b|checkpoint\b|"
    r"key (?:terms|concepts|equations)\b|section \d)", re.I)

MIN_SOLUTION = 40
MAX_BLOCK = 1600
MAX_PER_CHAPTER = 8


def _has_math(text):
    return bool(re.search(r"\$[^$]{2,}\$|\\frac|\\sqrt|\\int|\\sum"
                          r"|[=<>≤≥]|\d\s*[+\-*/^]\s*\d", text or ""))


def examples_in_text(text, limit=MAX_PER_CHAPTER):
    """Worked examples found in plain chapter text, in order.

    Each is {problem, solution, steps} — the same shape
    `openstax.parse_book_html` produces, so everything downstream is identical
    whichever source the course came from.
    """
    body = text or ""
    out = []
    for head in _EXAMPLE_HEAD.finditer(body):
        start = head.end()

        # BOUND THE SEARCH TO THIS EXAMPLE. Looking for "Solution" anywhere
        # after the header finds the NEXT example's solution when this one has
        # none — so an exercise the book leaves to the reader silently absorbs
        # its neighbour's answer, and the tutor shows a solution belonging to a
        # different problem.
        stop = _END.search(body, start)
        window_end = stop.start() if stop else min(len(body),
                                                   start + 4 * MAX_BLOCK)
        window = body[start:window_end]

        sol = _SOLUTION_HEAD.search(window)
        if not sol:
            continue                      # an example with no solution shown
        problem = window[:sol.start()].strip()
        solution = window[sol.end():].strip()[:MAX_BLOCK]

        if len(solution) < MIN_SOLUTION or not problem:
            continue
        if not _has_math(problem + solution):
            continue

        out.append({
            "problem": re.sub(r"\s+", " ", problem)[:600],
            "solution": re.sub(r"\s+", " ", solution)[:MAX_BLOCK],
            "steps": _steps(solution),
        })
        if len(out) >= limit:
            break
    return out


_STEP_SPLIT = re.compile(r"(?:^|\s)(?:Step\s+\d+[.:)]|\(\d+\)|\d+[.)])\s+")


def _steps(solution):
    """The solution's steps, if it is written as steps.

    A step list is what makes a worked example teachable one move at a time:
    the tutor shows the whole solution and asks why ONE step is licensed,
    rather than asking about an undifferentiated block.
    """
    parts = [p.strip() for p in _STEP_SPLIT.split(solution or "") if p.strip()]
    return parts[:12] if len(parts) >= 2 else []


#: Notes the book flags as errors — the source of an ERROR_HUNT.
_NOTE = re.compile(
    r"(?:^|\n)\s*(?:common (?:mistake|error)|caution|warning|"
    r"watch out|misconception)\s*[.:—-]?\s*(.{40,600}?)(?=\n\s*\n|$)",
    re.I | re.S)


def notes_in_text(text, limit=6):
    """Flagged caution/mistake notes, which is where real errors live."""
    return [re.sub(r"\s+", " ", m.group(1)).strip()
            for m in _NOTE.finditer(text or "")][:limit]


_TITLE_WORD = re.compile(r"[A-Za-z]{4,}")

#: Words that appear in half the titles in any maths course and so carry no
#: matching signal.
_STOP = {"function", "functions", "value", "values", "using", "with", "from",
         "properties", "expression", "expressions", "applying", "finding",
         "evaluating", "identifying", "understanding", "conditions", "rules",
         "rule", "form", "forms", "the", "and", "for"}


def _best_for(concept, moves):
    """The move that is ABOUT this concept, or the next one available.

    WHY MATCHING IS NEEDED AT ALL.
    The first version took `best_move(moves)` and popped in order, so within a
    lesson each concept got whichever example came next. Measured on a real
    build: "Applying the Squeeze Theorem" was taught with a factoring limit,
    "Integration by parts" with the antiderivative of 1/x, and "Definite
    integrals and power rule" with the one example that IS integration by
    parts. Systematically off by one, and every pairing wrong in a way a
    learner would notice before the tutor did.
    """
    if not moves:
        return None
    words = {w.lower() for w in _TITLE_WORD.findall(concept.get("title") or "")}
    words -= _STOP
    if words:
        best, score = None, 0
        for m in moves:
            blob = ((m.get("first") or "") + " " + (m.get("second") or "")).lower()
            hits = sum(1 for w in words if w in blob)
            if hits > score:
                best, score = m, hits
        if best is not None:
            return best
    # No shared vocabulary: order is as good a guess as any, and a worked
    # example from the same lesson is still on-topic.
    return tm.best_move(moves)


def attach_to_course(course, book, status_callback=None):
    """Attach a worked example and a teaching move to each aided concept.

    The registry contract's optional `attach_to_course` hook, mirroring what
    the computer-science domain does with code snippets. Mutates `course` in
    place and returns a tally. Never raises: an asset failure must cost the
    asset, not the build.

    MINED ONCE, AT BUILD TIME. A doc- or book-sourced lesson stores a chapter
    reference, not the chapter, so mining at teaching time would mean reopening
    the book mid-turn on a machine where turn latency is already the acute
    defect.
    """
    tally = {"examples": 0, "moves": 0, "skipped": 0, "chapters": 0}
    aided = set(AIDED_KINDS_ORDER)
    lessons = [l for m in (course.get("modules") or [])
               for u in (m.get("units") or [])
               for l in (u.get("lessons") or [])]

    seen = set()
    for i, lesson in enumerate(lessons, 1):
        text = ""
        try:
            order = lesson.get("book_chapter")
            chapter = book.chapter(order) if (book and order is not None) else None
            text = getattr(chapter, "text", "") or ""
        except Exception:
            text = ""
        if not text:
            text = lesson.get("source_text") or ""
        if not text:
            continue

        tally["chapters"] += 1
        try:
            examples = examples_in_text(text)
            notes = notes_in_text(text)
            moves = tm.from_examples(examples, notes=notes)
        except Exception as e:
            logger.warning(f"[MATH] mining failed for "
                           f"{lesson.get('title','')!r}: {e}")
            continue

        for concept in (lesson.get("concepts") or []):
            kind = concept.get("concept_kind") or UNKNOWN
            if kind not in aided:
                tally["skipped"] += 1
                continue
            move = _best_for(concept, moves)
            if not move:
                tally["skipped"] += 1
                continue

            # Course-wide de-duplication: the same worked example attached to
            # three concepts teaches the third two nothing.
            fingerprint = (move.get("first", "")[:120],
                           move.get("second", "")[:120])
            if fingerprint in seen:
                tally["skipped"] += 1
                continue
            seen.add(fingerprint)

            # THE FIELD NAME IS `teaching_pair`, NOT `teaching_move`.
            #
            # `fsm_logic._domain_teaching` reads `teaching_pair`; the computer
            # science domain writes `teaching_pair`. Writing anything else
            # attaches material the tutor never reads — which is the defect
            # this repository has now hit nine times, and this module hit it
            # while sitting beside the document describing it.
            #
            # `teaching_move` is also already taken: services/common/
            # teaching_move.py is an unrelated (reverted) A.6 mechanism.
            concept["teaching_pair"] = {
                "kind": move["kind"],
                "first": move.get("first", "")[:900],
                "second": move.get("second", "")[:900],
            }
            tally["moves"] += 1
            if move.get("steps"):
                concept["worked_steps"] = move["steps"][:12]
                tally["examples"] += 1
            moves = [m for m in moves if m is not move]

        if status_callback and i % 5 == 0:
            try:
                status_callback(f"MATH:EXAMPLES:{i}:{len(lessons)}")
            except Exception:
                pass

    logger.info(f"[MATH] attached {tally['moves']} teaching move(s), "
                f"{tally['examples']} stepped example(s), "
                f"skipped {tally['skipped']}")
    return tally
