"""The classifier must be told WHICH COURSE it is classifying for.

A concept title does not carry its own subject. "Vectors" is a data structure,
a matrix column, or a disease carrier, decided entirely by the course around
it. The classifier prompt showed the model a lesson title and a list of concept
names and no course, so it had to guess — and the registry already learned this
the hard way, routing a course whose modules were "Mosquitoes and malaria" to
mathematics on the keyword "vector".

Also pinned here because a prompt argument that is accepted and then dropped is
this project's most repeated defect: `aid_policy` was accepted and never
forwarded, and `context_trigger` reached the model in the learner's own slot.
The forwarding is asserted at the call boundary, not just the signature.
"""
import importlib
import inspect

import pytest

DOMAINS = ["computer_science", "mathematics", "history", "science"]


@pytest.mark.parametrize("domain", DOMAINS)
@pytest.mark.parametrize("source", ["", "some real source text"])
def test_topic_appears_in_the_prompt(domain, source):
    m = importlib.import_module(f"services.domains.{domain}.concept_classifier")
    p = m._prompt("A Lesson", ["A Concept"], source, topic="Advanced SQL")
    assert "Advanced SQL" in p


@pytest.mark.parametrize("domain", DOMAINS)
def test_absent_topic_degrades_rather_than_announcing_itself(domain):
    """No topic must render to nothing — not to an empty header, which reads
    to the model as a course that could not be determined."""
    m = importlib.import_module(f"services.domains.{domain}.concept_classifier")
    p = m._prompt("A Lesson", ["A Concept"], "", topic=None)
    assert "### COURSE" not in p


@pytest.mark.parametrize("domain", DOMAINS)
def test_classify_course_accepts_topic(domain):
    m = importlib.import_module(f"services.domains.{domain}.concept_classifier")
    assert "topic" in inspect.signature(m.classify_course).parameters


def test_the_builder_actually_forwards_it():
    """Accepting the argument is not the same as being given it."""
    import services.core.course_builder as cb
    src = inspect.getsource(cb.SkeletonBuilder._classify_concepts_by_domain)
    assert "topic=topic" in src, (
        "classify_concepts is called without the course topic; the prompt "
        "would be built with no course again")
