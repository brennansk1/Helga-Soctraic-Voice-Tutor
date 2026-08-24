"""`**Definition.**` is a definition.

The detector demanded the exact string "**Definition**", so a body opening
"**Definition.**" or "**Definition:**" — how anyone actually writes one — was
judged to contain no definition. Measured: a four-concept course, every
concept carrying a real formal definition, refused 4/4 at mastery 3.

Widening it must not lower the bar: prose with no definition still fails.
"""
import pytest

from services.core.depth_contract import _has

RECOGNISED = [
    "**Definition.** A query plan is the tree of physical operators chosen.",
    "**Definition:** A join strategy is an algorithm for combining relations.",
    "**Definition** A plan is a tree.",
    "**Definitions**\nA plan is a tree.",
    "## Definition\nA plan is a tree of operators.",
    "A query plan is defined as the tree of operators the planner chose.",
    "We define selectivity as the fraction of rows passing a predicate.",
    "Let n be the number of rows in the outer relation.",
]

REJECTED = [
    "A query plan is a tree of operators and you should read it.",
    "Query plans are useful. Read them bottom-up and look for slow nodes.",
    "This concept explains query plans in detail with several examples.",
    "",
]


@pytest.mark.parametrize("body", RECOGNISED)
def test_a_real_definition_is_recognised(body):
    assert _has("formal_definition", body), body[:50]


@pytest.mark.parametrize("body", REJECTED)
def test_prose_without_one_still_fails(body):
    assert not _has("formal_definition", body), body[:50]
