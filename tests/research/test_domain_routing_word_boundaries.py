"""'art' is inside 'partitioning'.

classify_domains matched with `w in blob` — a substring test — so every
window-function concept in an SQL course routed to the art archives, and the
course cited the Metropolitan Museum of Art for SQL window frames. Measured on
2026-08-25:

    classify_domains('Tie Interaction',
                     'Window Function Frame Semantics and Partitioning',
                     'advanced sql')  ->  ['art']

The same shape puts 'war' inside 'software' and 'logic' inside 'logical'.

A wrong route is not merely wasted: an irrelevant source is still a citation,
so it inflates grounding confidence and satisfies the depth contract's
any_source requirement while teaching nothing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "services", "research"))

from domain_sources import classify_domains  # noqa: E402


@pytest.mark.parametrize("concept,module,course", [
    ("Tie Interaction", "Window Function Frame Semantics and Partitioning", "advanced sql"),
    ("Partitioning", "", "advanced sql"),
    ("Query Optimization and Constraint Design", "", "advanced sql"),
    ("Software Architecture", "", "computer science"),
    ("Warehouse Fact Tables", "", "data engineering"),
    ("Logical Validation and Null Semantics", "", "advanced sql"),
    ("Set Operations and Data Reconciliation", "", "sql"),
])
def test_computing_never_routes_to_the_humanities(concept, module, course):
    got = classify_domains(concept, module, course)
    assert got == [], f"{concept!r} routed to {got}"


@pytest.mark.parametrize("text,want", [
    ("Impressionist brushwork", "art"),
    ("Graphic design in the Bauhaus", "art"),
    ("The French Revolution", "history"),
    ("Quantum entanglement", "science"),
    ("Kantian ethics", "philosophy"),
    ("Sociological method", "social"),
])
def test_the_real_signals_still_route(text, want):
    assert want in classify_domains(text)


def test_a_short_word_does_not_match_a_longer_one():
    """`\\bwar` matched WAR-ehouse. The suffix bound is what stops it."""
    assert classify_domains("Warehouse Fact Tables") == []
    assert "history" in classify_domains("the war of 1812")
    assert "history" in classify_domains("post-war reconstruction")


def test_stems_still_catch_their_families():
    """impressionis / sociolog / epistem are deliberate prefixes; anchoring at
    a word START must keep them working."""
    assert "art" in classify_domains("impressionism and its critics")
    assert "social" in classify_domains("sociological imagination")
    assert "philosophy" in classify_domains("epistemology of testimony")
