"""Thin content: structurally complete, substantively empty.

The depth contract counts words and required elements. content_guards catches
an obvious stub. `hydration_qa`'s check_substance and check_hollowness measure
thinness as COURSE-LEVEL AVERAGES — they report that half a course is hollow
without naming one concept, so nothing can act on them. This is the per-concept
form.

THE CALIBRATION IS THE HARD PART, AND IT WENT WRONG TWICE.

First the thresholds were guessed: the density floor landed five times below
the thinnest concept in the corpus, so two of the three checks could not fail
anything and the run reported "0 findings" looking exactly like success.

Then they were set at p05/p95 of the corpus — which flags 5% of ACCEPTABLE
content by construction, and duly produced five findings, every one false on
inspection. They were dense technical prose that happened to use fewer code
spans than average.

They now sit outside the observed range of content we accept. A false "thin"
verdict sends good teaching to be rewritten, so the check is built to stay
silent on a good course and say so, rather than to find something.
"""
import pytest

from services.core.course_audit import audit_thinness


GENERIC = ("Window functions are a key concept in this subject. They are "
           "important to understand because they are widely used. Many "
           "practitioners find them useful in their daily work. It is "
           "essential to grasp the fundamentals before moving on to more "
           "advanced material. This topic builds on what came before.\n\n")

REAL_PARAGRAPH = (
    "The `ROW_NUMBER()` function assigns a unique sequential integer to each "
    "row within a partition, ordered by the `ORDER BY` clause inside the "
    "`OVER()` specification. Ties are broken arbitrarily by the engine.\n\n")

# Dense technical prose with few code spans — the shape that was falsely
# flagged five times. It must pass.
DENSE_PROSE = (
    "**Definition.** Table partitioning is a physical storage optimization "
    "where a single logical table is subdivided into smaller independent "
    "physical segments based on a user-defined partitioning function, such "
    "that each partition resides in a distinct tablespace.\n\n"
    "The distinction between logical partitioning and physical partitioning "
    "is critical. In PostgreSQL this is achieved via declarative "
    "partitioning, where the executor treats the partitions as a unified set "
    "during query execution, while in MySQL and InnoDB the server routes row "
    "insertions to specific partitions based on partition key evaluation.\n\n"
    "Constraint exclusion allows the planner to prove that a partition cannot "
    "contain matching rows and skip scanning it entirely, which is the "
    "mechanism behind partition pruning at plan time rather than at run "
    "time.\n\n")


def test_a_generic_stub_is_caught():
    findings, measures = audit_thinness(
        "## Core Explanation\n\n" + GENERIC * 6, "Window Functions")
    assert findings, f"generic filler passed as content: {measures}"


def test_the_same_paragraph_repeated_is_caught_on_its_own():
    """No amount of concreteness makes reading the same text again useful.

    This escaped an earlier two-axis rule: the repeated paragraph was full of
    real SQL identifiers, so it cleared the other two measures comfortably
    while being literally the same sentence eight times.
    """
    findings, measures = audit_thinness(
        "## Core Explanation\n\n" + REAL_PARAGRAPH * 8, "Row Number")
    assert findings, f"verbatim repetition passed: {measures}"
    assert "repeat" in findings[0] or "identical" in findings[0]


def test_dense_technical_prose_is_not_thin():
    """The false positive that shaped these thresholds."""
    findings, measures = audit_thinness(
        "## Core Explanation\n\n" + DENSE_PROSE, "Partitioning")
    assert findings == [], (
        f"dense prose flagged as thin — this is the failure mode that sends "
        f"good teaching to be rewritten: {measures}")


def test_short_bodies_are_left_to_the_depth_contract():
    """Density is not measurable on a fragment, and the contract owns length."""
    findings, measures = audit_thinness("## Core Explanation\n\nToo short.",
                                        "Anything")
    assert findings == [] and measures == {}


def test_measures_are_returned_even_when_nothing_is_flagged():
    """Silence must be inspectable, or a dead check looks like a clean one."""
    _, measures = audit_thinness("## Core Explanation\n\n" + DENSE_PROSE,
                                 "Partitioning")
    assert set(measures) >= {"concrete_density", "empty_sentence_share",
                             "self_repetition", "words"}


def test_thresholds_sit_outside_the_measured_corpus():
    """Guard the calibration itself.

    Measured over 178 concepts of SQL and Advanced SQL: density never below
    0.109, empty-sentence share never above 0.389. A threshold inside that
    range flags content we accept.
    """
    from services.core import course_audit as ca
    assert ca.MIN_CONCRETE_DENSITY < 0.109, "density floor is inside good content"
    assert ca.MAX_EMPTY_SENTENCE_SHARE > 0.389, "empty ceiling is inside good content"
