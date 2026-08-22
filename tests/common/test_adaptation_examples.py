"""A.9 — demonstrating adaptation rather than describing it.

Eight prompt-level interventions were measured on 2026-08-21. `adaptation`
stalled at 2.30 against a 3.5 gate, and seven turn-level features tested across
214 dialogues failed to predict the score — six pointing the wrong way.

So the tutor cannot be TOLD in rules what an adaptive turn is, because nobody
has managed to state it as a rule. What was never tried is showing it.
"""
import re

from services.common.adaptation_examples import (
    ADAPTATION_EXEMPLARS, exemplars_for,
)


def test_the_exemplars_hold_the_situation_fixed_and_vary_the_learner():
    """A single 'good turn' example teaches style. Adaptation is a RELATION
    between learner and response, so only a contrast can carry it."""
    text = ADAPTATION_EXEMPLARS
    assert "STUDENT A" in text and "STUDENT B" in text
    assert text.count("TUTOR REPLIED") == 2
    assert "The question was identical" in text


def test_the_two_replies_are_genuinely_different_moves():
    """A harder question for the one who has it; a smaller one for the one who
    does not. If both replies were the same move the exemplar teaches nothing."""
    a = ADAPTATION_EXEMPLARS.split("STUDENT B")[0]
    b = ADAPTATION_EXEMPLARS.split("STUDENT B")[1]
    assert "harder" in a.lower() or "more than" in a.lower()
    assert "smaller" in b.lower() or "how much do two years pay" in b.lower()


def test_the_exemplar_concept_is_NOT_in_any_benchmark_topic():
    """Using our own high-scoring benchmark transcripts as exemplars and then
    measuring on that benchmark is teaching to the test, and the resulting
    number would mean nothing."""
    import sys, os
    sys.path.insert(0, os.path.join(
        os.path.dirname(__file__), "../../tools"))
    import bench_domains as bd
    concepts = " ".join(t["concept"].lower()
                        for d in bd.DOMAINS.values() for t in d["topics"])
    assert "interest" not in concepts, (
        "the exemplar topic now collides with a benchmark topic")


def test_no_benchmark_topic_text_is_reproduced():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tools"))
    import bench_domains as bd
    ex_words = set(re.findall(r"[a-z]{5,}", ADAPTATION_EXEMPLARS.lower()))
    for d in bd.DOMAINS.values():
        for t in d["topics"]:
            ctx = set(re.findall(r"[a-z]{5,}", t["context"].lower()))
            overlap = ex_words & ctx
            assert len(overlap) < 6, (t["concept"], sorted(overlap))


def test_it_is_bounded_because_it_rides_in_every_turn():
    assert len(ADAPTATION_EXEMPLARS) < 1200


def test_exactly_two_exemplars_not_a_gallery():
    """They compete for attention with the concept document, the aid grammar,
    the turn state and the contract."""
    assert ADAPTATION_EXEMPLARS.count("STUDENT ") == 2


def test_it_is_dropped_at_high_bloom():
    assert exemplars_for(bloom_level=5) == ""
    assert exemplars_for(bloom_level=6) == ""
    assert exemplars_for(bloom_level=2) != ""


def test_a_bad_bloom_value_does_not_raise():
    for bad in (None, "x", object()):
        assert isinstance(exemplars_for(bloom_level=bad), str)
