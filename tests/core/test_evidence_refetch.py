"""When content fails for want of evidence, go and get evidence.

The depth-retry loop re-prompted the model with the SAME research material and
a hint naming what was missing. If what was missing was a primary source or
specific detail, that asks a model to produce from evidence it does not have,
and the only way to comply is to invent.

Research otherwise ran at most twice per concept: once at hydration start, and
once more only if research CONFIDENCE fell below the floor. Neither trigger has
anything to do with the content being wrong — a concept could fail its contract
three times without one additional lookup.

The counterweight is that refetching is expensive on this hardware, so it is
bounded: first retry only, and only for failures more material can actually
fix. A missing heading is not one of those.
"""
import pytest

from services.core.course_builder import _needs_more_evidence


@pytest.mark.parametrize("problem", [
    "missing required element: primary_source",
    "missing required element: worked_example",
    "the concept cites no source for its central claim",
    "missing required element: named_result",
    "meets its structure but teaches little: almost nothing specific to this "
    "concept (97 concrete tokens in 909 words)",
    "a factual claim contradicts what PostgreSQL actually does: NULLs sort LAST",
])
def test_evidence_shaped_failures_trigger_a_refetch(problem):
    assert _needs_more_evidence([problem]), f"would not refetch for: {problem}"


@pytest.mark.parametrize("problem", [
    "## Analogies is missing — the tutor reads it when teaching",
    "the text contains your own deliberation — remove it",
    "the curriculum path contains a splitter artefact like \"Part 2\"",
    "## Core Explanation is only 22 words",
    "the body runs to 1,840 words, over the cap for this level",
])
def test_form_failures_do_not_trigger_a_refetch(problem):
    """The model has everything it needs; more sources change nothing and
    cost minutes on this hardware."""
    assert not _needs_more_evidence([problem]), f"wasted a refetch on: {problem}"


def test_no_problems_means_no_refetch():
    assert not _needs_more_evidence([])
    assert not _needs_more_evidence(None)


def test_a_mixed_list_refetches_if_any_problem_needs_it():
    assert _needs_more_evidence([
        "## Analogies is missing — the tutor reads it when teaching",
        "missing required element: primary_source",
    ])
