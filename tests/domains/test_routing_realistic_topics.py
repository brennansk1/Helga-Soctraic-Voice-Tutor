"""Routing on topics a learner would actually TYPE, not subject names.

The keyword lists were written as taxonomies — "history", "mathematics",
"thermodynamics" — and a person types "The Roman Republic" or "the pythagorean
theorem". A topic that misses its domain silently loses the whole teaching
layer: no per-kind guidance, no mined material, just generic tutoring that
looks fine and teaches worse.

Keyword-only here on purpose. `domain_for` also takes an LLM matcher, and
these must land without spending a model call.
"""
import pytest

from services.domains import registry

EXPECTED = [
    ("computer_science", "SQL for data analysis"),
    ("computer_science", "dbt and analytics engineering"),
    ("computer_science", "Power BI dashboards"),
    ("computer_science", "n8n automation workflows"),
    ("mathematics", "The Pythagorean theorem"),
    ("mathematics", "Calculus derivatives and integrals"),
    ("history", "The French Revolution"),
    # These four routed to (generic) before the keyword list grew past eras.
    ("history", "The Roman Republic"),
    ("history", "The Great Depression"),
    ("history", "Women's suffrage"),
    ("history", "The Treaty of Versailles"),
    ("science", "Photosynthesis"),
    ("science", "Organic chemistry basics"),
    ("science", "Newton's laws of motion"),
]

#: Words that LOOK like they belong to a domain and must not route there.
#: Every one of these has cost a mis-taught course in this project already.
MUST_NOT = [
    ("Roman numerals", "history"),
    ("Roman architecture", "history"),
    ("Brute force negotiation tactics", "science"),
    ("Managing your workforce", "science"),
    ("Vectors: mosquitoes and malaria", "mathematics"),
]


@pytest.mark.parametrize("expected,topic", EXPECTED)
def test_realistic_topic_routes_to_its_domain(expected, topic):
    assert registry.domain_for(topic) == expected, (
        f"{topic!r} lost its teaching layer")


@pytest.mark.parametrize("topic,forbidden", MUST_NOT)
def test_ambiguous_word_does_not_capture_a_foreign_topic(topic, forbidden):
    assert registry.domain_for(topic) != forbidden
