"""Every fixture here is real text an audit found in a shipped course.

On 2026-08-25 both courses passed the depth contract while carrying model
deliberation, boilerplate sections, build-time apologies and splitter
artefacts. None of those is a depth question, so the contract had nothing to
say about them. These guards run at the same point and feed the same
regeneration loop.

The false-positive tests matter as much as the true-positive ones: "wait for
the lock" and "actually evaluated at run time" are ordinary technical prose,
and a guard that rejects them would make the pipeline unable to teach
concurrency or evaluation order.
"""
import pytest

from services.core.content_guards import (MIN_CORE_EXPLANATION_WORDS, inspect,
                                          is_clean)

# --- verbatim from the audit -------------------------------------------------

DELIBERATION = [
    "*Correction:* Wait, let's verify PostgreSQL's default for DESC.",
    "*Verification:* According to PostgreSQL docs, \"...\" No. Actually, "
    "PostgreSQL's default is NULLS LAST? No. Let's check the docs carefully.",
    "*Wait, let's re-read the RANK() behavior.*",
    "Let's refine: ... Wait, standard join logic:",
    "Row B: match_check = NULL (because NULL = NULL evaluates to UNKNOWN? "
    "No, wait. Result: email. Wait, in Row B both are NULL.",
    "Wait, correction: With RANGE, the second row's frame includes all rows.",
]

STUBS = [
    "## Core Explanation\nNULLs in Recursive CTEs is a key concept in sql.\n",
    "## Core Explanation\nRANGE Frame Definition is a key concept in advanced sql.\n",
    "## Mastery Criteria\nStudent should demonstrate understanding of Multiple Aggregations.\n",
    "## Misconceptions\n- **Belief**: None identified.\n- **Correction**: N/A\n",
    "## Real-World Examples\nExamples of joins can be found in everyday applications.\n",
]

CLEAN = [
    "A transaction must wait for the lock to be released before it proceeds.",
    "The predicate is actually evaluated at run time, not at plan time.",
    "**Definition.** A window frame is the set of rows the function sees.",
    "Correction of a skewed estimate requires fresh statistics.",
    "The planner waits on the buffer, then continues.",
]


@pytest.mark.parametrize("body", DELIBERATION)
def test_model_deliberation_is_caught(body):
    problems = inspect(body)
    assert problems, f"not caught: {body[:60]!r}"
    assert any("deliberation" in p or "correction label" in p for p in problems)


@pytest.mark.parametrize("body", STUBS)
def test_boilerplate_sections_are_caught(body):
    assert inspect(body), f"not caught: {body[:60]!r}"


@pytest.mark.parametrize("body", CLEAN)
def test_ordinary_technical_prose_passes(body):
    assert is_clean(body), f"false positive on: {body!r} -> {inspect(body)}"


def test_the_build_apology_is_caught():
    body = ("**Grounding unavailable.** The research service could not be "
            "reached while this concept was written, so no sources were "
            "consulted at all.")
    assert any("BUILD" in p for p in inspect(body))


def test_the_splitter_artefact_is_caught():
    body = "- **Path**: sql > Query Construction with Subqueries Part 2 > Lesson\n"
    assert any("splitter artefact" in p for p in inspect(body))


def test_a_one_line_core_explanation_is_caught():
    body = ("## Core Explanation\nIt matters a great deal.\n\n"
            "## Key Facts\n" + ("- a real fact about the subject\n" * 40))
    problems = inspect(body)
    assert any("Core Explanation" in p for p in problems), problems


def test_a_real_core_explanation_passes():
    body = ("## Core Explanation\n" + ("The planner estimates cardinality "
            "from statistics gathered by ANALYZE. " * 12) + "\n")
    assert len(body.split()) > MIN_CORE_EXPLANATION_WORDS
    assert is_clean(body), inspect(body)


def test_placeholders_are_caught():
    for body in ("Lorem ipsum dolor sit amet.", "TODO: write this up",
                 "[Hydration failed]", "Content for X is currently unavailable"):
        assert inspect(body), body


def test_problems_are_phrased_as_instructions():
    """They are fed straight back to the model as the retry instruction, so
    they have to tell it what to DO."""
    problems = inspect("Wait, let's verify that claim.")
    assert problems
    for p in problems:
        assert len(p.split()) >= 6, f"too terse to act on: {p!r}"


def test_the_misconceptions_template_is_not_deliberation():
    """A guard that fails everything is indistinguishable from no guard.

    "**Correction**:" IS the specified format for the Misconceptions section —
    "- **Belief**: ... **Correction**: ..." — so a first version of the
    self-correction pattern matched every well-formed concept and would have
    rejected 95 of 95 in a finished course.
    """
    body = ("## Misconceptions\n"
            "- **Belief**: `NULLIF(a, b)` returns TRUE if a and b are equal.\n"
            "  **Correction**: It returns NULL. To capture equality, use "
            "`NULLIF(a, b) IS NULL`.\n"
            "- **Belief**: NULLIF and COALESCE are inverses.\n"
            "  **Correction**: They are not.\n")
    assert is_clean(body), inspect(body)


def test_deliberation_outside_that_section_is_still_caught():
    body = ("## Core Explanation\n" + ("real teaching content here. " * 30) +
            "\n*Correction:* Wait, let's verify that.\n\n"
            "## Misconceptions\n- **Belief**: x\n  **Correction**: y\n")
    assert not is_clean(body)


def test_the_guards_reproduce_the_audit_counts():
    """Independent confirmation, not a re-assertion of the same numbers.

    A human audit on 2026-08-25 found, in the shipped SQL course: 2 stubbed
    sections, 3 concepts with visible model deliberation, and 9 concepts with
    a "Part 2" splitter artefact in the path. These guards were written from
    the quoted evidence, then run over the course, and flagged 14 concepts —
    the same 2 + 3 + 9.
    """
    import glob
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = glob.glob(os.path.join(root, "data", "courses",
                                   "course_38c3ecb0", "content", "*.md"))
    if len(files) < 90:
        pytest.skip("the audited course is not present in this checkout")
    flagged = sum(1 for f in files if inspect(open(f, encoding="utf-8").read()))
    assert 10 <= flagged <= 20, (
        f"{flagged} concepts flagged; the audit found 14. A large move in "
        f"either direction means the guards drifted from what they were "
        f"written to catch.")
