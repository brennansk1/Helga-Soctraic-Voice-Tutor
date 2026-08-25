"""Pass 3 — fix what the audit found, or withhold the concept.

The design turns on one measured fact: LLMs cannot reliably self-correct
without external feedback and often get WORSE trying, and merely challenging a
model makes it abandon correct answers. So nothing here says "this is wrong,
fix it" — every repair carries the defect, the evidence that settles it, and an
instruction to change the minimum, and every repair is re-checked by the same
external gates that raised the finding.

The tests below are mostly about the ways a repair can do HARM, because those
are the failures that reach a learner. A repair that silently returns a summary
in place of a lesson has done more damage than the sentence it fixed.
"""
import pytest

from services.core import course_repair as cr


LESSON = """# Sorting with NULLs

## Core Explanation
Under `ORDER BY x ASC`, PostgreSQL places NULLs first, treating them as
smaller than any other value. This surprises people coming from other engines,
and it matters whenever a nullable column is used for ordering in a report.
The behaviour can be overridden with `NULLS FIRST` and `NULLS LAST`.

## Worked Example
`SELECT x FROM t ORDER BY x ASC;` returns the NULL rows before the numbers.

## Misconceptions
- **Belief**: NULL sorts as zero. **Correction**: it has no position of its
  own and the engine decides where it goes.

## Analogies
A NULL is an unlabelled box: the sorter must be told where to put it.
"""

FALSE_CLAIM = {
    "check": "executable_claims", "severity": "blocking",
    "concept_uid": "con_1", "title": "Sorting with NULLs",
    "detail": "says NULLs sort FIRST under ASC; the engine puts them LAST",
    "quote": "PostgreSQL places NULLs first, treating them as smaller",
}
MISSING_SECTION = {
    "check": "tutor_sections", "severity": "serious", "concept_uid": "con_1",
    "title": "Sorting with NULLs",
    "detail": "## Analogies is missing — the tutor reads it when teaching",
    "quote": "",
}
NO_PASSAGE = {
    "check": "citations", "severity": "serious", "concept_uid": "con_1",
    "title": "Sorting with NULLs",
    "detail": "2 of 2 sources have no stored passage", "quote": "",
}


# --- what is worth spending model time on -----------------------------------

def test_text_problems_are_repairable():
    assert cr.repairable([FALSE_CLAIM, MISSING_SECTION]) == [
        FALSE_CLAIM, MISSING_SECTION]


def test_a_missing_passage_is_not_a_text_problem():
    """That is a research failure. Rewriting the lesson cannot create a source,
    and trying burns model time to change nothing."""
    assert cr.repairable([NO_PASSAGE]) == []


def test_the_two_sets_do_not_overlap():
    assert not (cr.REPAIRABLE_CHECKS & cr.NOT_REPAIRABLE)


# --- the prompt -------------------------------------------------------------

def test_the_prompt_carries_the_engine_answer_not_just_the_complaint():
    """The executable tier knows what the database DOES. Handing that over is
    the difference between applying a correction and guessing at one."""
    p = cr.build_prompt(LESSON, [FALSE_CLAIM], "Sorting with NULLs", "SQL",
                        evidence=cr.evidence_for([FALSE_CLAIM]))
    assert "the engine puts them LAST" in p
    assert "not open to interpretation" in p


def test_the_prompt_never_just_says_it_is_wrong():
    """A model told it is wrong abandons correct answers — the FlipFlop
    result. Every problem must arrive as a specific, checkable defect."""
    p = cr.build_prompt(LESSON, [FALSE_CLAIM], "Sorting with NULLs", "SQL")
    lowered = p.lower()
    assert "this is wrong" not in lowered
    assert "you made a mistake" not in lowered
    assert "change as little as possible" in cr.REPAIR_SYSTEM.lower()


def test_the_prompt_asks_for_minimal_change():
    p = cr.build_prompt(LESSON, [FALSE_CLAIM], "S", "SQL")
    assert "Fix ONLY the problems listed" in p
    assert "Leave everything else exactly as it is" in p


def test_blocking_problems_lead_the_prompt():
    """If it is truncated, it must be truncated on the minor findings."""
    p = cr.build_prompt(LESSON, [MISSING_SECTION, FALSE_CLAIM], "S", "SQL")
    assert p.index("the engine puts them LAST") < p.index("## Analogies is missing")


def test_evidence_is_only_drawn_from_checks_that_know_the_answer():
    assert cr.evidence_for([MISSING_SECTION, NO_PASSAGE]) == []
    assert cr.evidence_for([FALSE_CLAIM])


# --- guarding against a harmful "repair" ------------------------------------

def test_a_repair_that_came_back_as_a_stub_is_rejected():
    ok, why = cr.is_plausible_repair(LESSON, "# Sorting with NULLs\n\nNULLs sort last.")
    assert not ok and "truncated" in why


def test_a_repair_that_drops_most_of_the_lesson_is_rejected():
    """The failure that actually happens: a model asked to fix one sentence
    returns a summary, and two thirds of the lesson is gone. Long enough to
    clear the stub floor, short enough to be a summary."""
    summary = ("# Sorting with NULLs\n\n## Core Explanation\n"
               + "PostgreSQL decides where NULL values go when sorting a "
                 "column, and the default differs between ascending and "
                 "descending order, which surprises people. " * 2)
    assert len(summary.split()) > 40
    ok, why = cr.is_plausible_repair(LESSON, summary)
    assert not ok and "dropped" in why, why


def test_an_empty_repair_is_rejected():
    ok, why = cr.is_plausible_repair(LESSON, "")
    assert not ok


def test_a_repair_that_balloons_is_rejected():
    ok, why = cr.is_plausible_repair(LESSON, LESSON * 4)
    assert not ok and "doubled" in why


def test_a_genuine_correction_is_accepted():
    fixed = LESSON.replace("places NULLs first, treating them as\nsmaller",
                           "places NULLs last, treating them as\nlarger")
    ok, why = cr.is_plausible_repair(LESSON, fixed)
    assert ok, why


def test_a_code_fence_wrapper_is_stripped():
    """Models return whole documents fenced. Storing the fence would put
    ``` into the lesson."""
    assert cr.clean_output("```markdown\n# Title\n\nBody text.\n```") \
        == "# Title\n\nBody text."
    assert cr.clean_output("# Title\n\nBody text.") == "# Title\n\nBody text."
