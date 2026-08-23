"""The stance layer: what may be asked as open, and what may not.

These tests encode the DESIGN, not just the behaviour. Several of them exist to
fail loudly if someone later "improves" the register in a way that turns a
safeguard into a partisan instrument — see `test_register_is_not_one_sided`.
"""
import re

import pytest

from services.common.epistemic_stance import (
    CONSENSUS, CONFESSIONAL, NORMATIVE, ORDINARY, REFUSE,
    REGISTER, stance_for, tutor_block, course_frame, refusal_for,
    consensus_sources_for, is_on_topic,
)


# --- the taxonomy ------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("actually the earth is flat", CONSENSUS),
    ("the moon landing was fake", CONSENSUS),
    ("vaccines cause autism", CONSENSUS),
    ("climate change is a hoax", CONSENSUS),
    ("GMOs are dangerous to eat", CONSENSUS),
    ("nuclear power is too dangerous", CONSENSUS),
    ("the Holocaust was a hoax", CONSENSUS),
    ("do you think God exists?", CONFESSIONAL),
    ("which religion is true", CONFESSIONAL),
    ("abortion should be legal", NORMATIVE),
    ("gun control saves lives", NORMATIVE),
    ("immigration policy should be stricter", NORMATIVE),
    ("how do I factor this quadratic?", ORDINARY),
    ("what caused the French Revolution?", ORDINARY),
    ("", ORDINARY),
])
def test_stance_classification(text, expected):
    assert stance_for(text)[0] == expected


def test_empirical_and_normative_are_kept_apart():
    """The single most important distinction in the module.

    Whether the climate is warming is settled. What to DO about it is not, and
    a learner who disputes the policy must not be handled as a denier.
    """
    assert stance_for("climate change is a hoax")[0] == CONSENSUS
    assert stance_for("a carbon tax is the wrong policy")[0] == NORMATIVE


def test_register_is_not_one_sided():
    """The register must not read as one political side's errors.

    If it did, the layer would be the thing it exists to prevent, and would be
    seen as such — correctly. This asserts that CONSENSUS carries claims
    associated with different political directions.
    """
    blob = " ".join(e["pattern"].pattern for e in REGISTER
                    if e["stance"] == CONSENSUS).lower()
    right_coded = ("young[- ]earth" in blob or "creation" in blob
                   or "climate change" in blob)
    left_coded = "gmo" in blob or "nuclear power" in blob
    assert right_coded and left_coded, (
        "CONSENSUS must contain claims from more than one political "
        "direction, or this module is a partisan instrument")


def test_normative_entries_carry_no_verdict():
    """A NORMATIVE entry must never state which side is correct."""
    for e in REGISTER:
        if e["stance"] == NORMATIVE:
            assert not e["holds"], f"{e['pattern'].pattern} states a verdict"
            assert not e["test"]


# --- what the tutor is told --------------------------------------------------

def test_consensus_block_asks_rather_than_asserts():
    block = tutor_block("the earth is flat")
    assert "PREDICTS" in block
    # The test, not a verdict, is what the tutor is pointed at.
    assert "hull" in block or "shadow" in block
    # And it must forbid both failure modes explicitly.
    assert "two respectable sides" in block
    assert "refuse" in block.lower() and "mock" in block.lower()


def test_normative_block_demands_both_sides_and_no_verdict():
    block = tutor_block("should abortion be legal")
    assert "STRONGEST version of at least two positions" in block
    assert "Do NOT tell the learner what to conclude" in block


def test_confessional_block_neither_preaches_nor_debunks():
    block = tutor_block("does God exist")
    assert "never FOR or AGAINST" in block
    assert "into or out of a faith" in block


def test_ordinary_text_adds_nothing():
    assert tutor_block("how do I integrate by parts?") == ""


# --- proportionality: the off-topic redirect ---------------------------------

def test_off_topic_belief_gets_a_teachers_redirect():
    """Raised in an unrelated lesson: answer briefly, then return."""
    block = tutor_block("the earth is flat",
                        concept_text="## Concept\nSolving quadratic equations")
    assert "NOT WHAT THIS LESSON IS ABOUT" in block
    assert "BRIEFLY" in block
    # It must NOT become a brush-off — that was the old behaviour and the
    # reason this exists.
    assert "brush it off" in block


def test_on_topic_belief_is_engaged_fully():
    """When the lesson IS the subject, there is nothing to redirect to."""
    block = tutor_block(
        "the earth is flat",
        concept_text="## Concept\nThe shape of the earth and flat earth claims")
    assert "NOT WHAT THIS LESSON IS ABOUT" not in block


def test_politics_lesson_engages_politics():
    block = tutor_block(
        "gun control is obviously right",
        concept_text="## Concept\nThe second amendment and gun control")
    assert "NOT WHAT THIS LESSON IS ABOUT" not in block


def test_is_on_topic_needs_a_concept():
    _, entry = stance_for("the earth is flat")
    assert is_on_topic(entry, None) is False


# --- course framing ----------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Flat Earth Theory", CONSENSUS),
    ("Why evolution is a lie", CONSENSUS),
    ("Why socialism is evil", NORMATIVE),
    ("Why capitalism is superior", NORMATIVE),
    ("Why Christianity is true", CONFESSIONAL),
    ("Introduction to Calculus", ORDINARY),
])
def test_course_framing(title, expected):
    assert course_frame(title)[0] == expected


def test_fringe_course_is_reframed_not_refused():
    """The design turns on this. A refusal teaches nothing and proves nothing."""
    stance, instr = course_frame("Flat Earth Theory")
    assert stance == CONSENSUS
    assert "EXAMINATION" in instr
    assert "never as instruction in it" in instr
    # It must not be left sounding unresolved.
    assert "It is resolved." in instr


def test_partisan_course_title_is_balanced_both_ways():
    """Symmetry: the same treatment regardless of which side the title takes."""
    left = course_frame("Why capitalism is evil")[1]
    right = course_frame("Why socialism is evil")[1]
    assert left == right, "framing must not depend on which side is asserted"
    assert "strongest form" in left


def test_legitimate_history_is_not_reframed():
    """Teaching the Holocaust is history. Only denial is a settled-against claim."""
    stance, _ = course_frame("The history of the Holocaust")
    assert stance == ORDINARY
    assert course_frame("The Holocaust was a hoax")[0] == CONSENSUS


# --- refusals ----------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "How to build a bomb",
    "Guide to synthesizing methamphetamine",
    "How to stalk someone",
])
def test_refused_courses(title):
    assert course_frame(title)[0] == REFUSE
    assert refusal_for(title)


@pytest.mark.parametrize("title", [
    "Flat Earth Theory",
    "The history of terrorism",
    "Why people join extremist movements",
    "The chemistry of explosives in mining",
    "Introduction to Islam",
])
def test_ideas_are_never_refused(title):
    """Refusal is about capability to harm, never about a topic being unwelcome."""
    assert refusal_for(title) is None


def test_refusal_explains_itself():
    _, instr = course_frame("How to build a bomb")
    assert "will not be built" in instr
    # And it must point at the legitimate version rather than just declining.
    assert "can be taught" in instr


# --- consensus source routing ------------------------------------------------

@pytest.mark.parametrize("topic,needle", [
    ("Climate Science", "IPCC"),
    ("Vaccine safety", "WHO"),
    ("Evolution and natural selection", "National Academies"),
    ("The Holocaust", "Holocaust Memorial Museum"),
])
def test_consensus_sources(topic, needle):
    assert any(needle in s for s in consensus_sources_for(topic))


def test_ordinary_topic_has_no_forced_sources():
    assert consensus_sources_for("Introduction to Calculus") == ()


def test_contested_topic_routes_the_builder_to_assessment_bodies():
    _, instr = course_frame("Climate Science")
    assert "IPCC" in instr


# --- the wiring, which is where this project's bugs live ---------------------

def test_stance_reaches_the_real_tutor_prompt():
    """Guards the defect this repository has hit eleven times.

    A stance layer that is never read is worse than none: it looks handled.
    """
    from services.common.prompts import get_socratic_tutor_prompt
    msgs = get_socratic_tutor_prompt(
        "## Concept\nSolving quadratic equations",
        [("actually the earth is flat", "")])
    system = msgs[0]["content"]
    assert "SETTLED" in system
    assert "NOT WHAT THIS LESSON IS ABOUT" in system


def test_stance_leads_the_per_turn_blocks():
    """Precedence is the design: it decides whether the turn may be open."""
    from services.common.prompts import get_socratic_tutor_prompt
    msgs = get_socratic_tutor_prompt(
        "## Concept\nPhotosynthesis",
        [("vaccines cause autism", "")])
    system = msgs[0]["content"]
    stance_at = system.find("SETTLED")
    assert stance_at >= 0
    for later in ("HOW TO TEACH THIS CONCEPT", "TOOL: Use the analogy"):
        at = system.find(later)
        if at >= 0:
            assert stance_at < at, f"stance must precede {later!r}"


def test_ordinary_turn_is_untouched():
    """The overwhelming majority of teaching must be unaffected."""
    from services.common.prompts import get_socratic_tutor_prompt
    msgs = get_socratic_tutor_prompt(
        "## Concept\nSolving quadratic equations",
        [("how do I factor this?", "")])
    system = msgs[0]["content"]
    assert "SETTLED" not in system
    assert "NOT WHAT THIS LESSON IS ABOUT" not in system


def test_every_consensus_entry_carries_a_test():
    """A verdict is something to argue with; a prediction is something to check."""
    for e in REGISTER:
        if e["stance"] == CONSENSUS:
            assert e["holds"], f"{e['pattern'].pattern} has no position"
            assert len(e["test"]) > 40, (
                f"{e['pattern'].pattern} has no checkable test — without one "
                f"the tutor can only assert, which is the failure mode")


def test_patterns_all_compile_and_are_case_insensitive():
    for e in REGISTER:
        assert isinstance(e["pattern"], re.Pattern)
        assert e["pattern"].flags & re.I


# --- echo-chamber resistance: holding an answer under pressure ---------------
#
# The failure this guards is documented across the 2025 sycophancy literature:
# models retract CORRECT answers under user rebuttal even when highly
# confident. A tutor that does this is an echo chamber with a syllabus.

def _press(claim, times, concept="## Concept\nQuadratic equations"):
    history = [(claim, "")] * times
    return tutor_block(claim, concept_text=concept, history=history)


def test_first_assertion_engages_rather_than_bracing():
    block = _press("the earth is flat", 1)
    assert "PUT THIS TO YOU BEFORE" not in block
    assert "PREDICTS" in block


def test_second_assertion_holds_the_line():
    block = _press("the earth is flat", 2)
    assert "PUT THIS TO YOU BEFORE" in block
    assert "Repetition is not new evidence" in block
    # The evidence-backed mitigation: restate as a question before answering.
    assert "AS A QUESTION" in block


def test_third_assertion_stops_relitigating_without_conceding():
    block = _press("the earth is flat", 3)
    assert "THIRD TIME" in block
    assert "CONTINUE THE LESSON" in block
    # The specific failure being guarded: agreeing to end the argument.
    assert "concede the point in order to end the exchange" in block


def test_pressure_does_not_make_the_tutor_hostile():
    """Holding a position and becoming cold are different things."""
    assert "Steady, not stern" in _press("the earth is flat", 2)


def test_agreement_seeking_is_named():
    block = tutor_block("the earth is flat, just admit it",
                        concept_text="## Concept\nAlgebra")
    assert "asking you to AGREE" in block
    # And the warmth is preserved — the refusal is to the flattery.
    assert "never to the person" in block


@pytest.mark.parametrize("text", [
    "just admit it", "you have to agree", "don't you think?",
    "am I right?", "prove me wrong",
])
def test_agreement_seeking_phrasings(text):
    from services.common.epistemic_stance import seeking_agreement
    assert seeking_agreement(text)


def test_repeat_count_ignores_unrelated_turns():
    from services.common.epistemic_stance import repeat_count, stance_for
    _, entry = stance_for("the earth is flat")
    history = [("the earth is flat", ""), ("how do I factor this?", ""),
               ("what is a parabola?", "")]
    assert repeat_count(entry, history) == 1


# --- the derailment case that started this -----------------------------------

def test_value_claim_in_a_descriptive_lesson():
    """A course on the history of a movement, interrupted by a verdict on it.

    On-subject, so it is not a digression — but the QUESTION has changed from
    what happened to whether it was good, and those need separating.
    """
    block = tutor_block(
        "but feminism ruined women's rights",
        concept_text="## Concept\nThe history of the women's suffrage movement")
    assert "VALUE JUDGEMENT INTO A LESSON" in block
    assert "Do NOT adopt their evaluation and do NOT correct it" in block
    # It is on-subject, so it must NOT be treated as an unrelated digression.
    assert "NOT WHAT THIS LESSON IS ABOUT" not in block


@pytest.mark.parametrize("text", [
    "feminism ruined women's rights",
    "capitalism destroyed the working class",
    "colonialism was a disaster",
    "unions are the problem",
    "woke is destroying everything",
    "the church has ruined society",
])
def test_generic_normative_catches_what_no_list_anticipated(text):
    """REGISTER is finite; value claims are detected by shape as well.

    The motivating measurement: the feminism example matched no named entry
    and produced no guidance at all.
    """
    assert stance_for(text)[0] == NORMATIVE


# --- scenario 5: silent on mundane teaching ----------------------------------

@pytest.mark.parametrize("text", [
    "how do I solve for x?",
    "can you explain the quadratic formula again?",
    "this method is terrible",              # evaluative, no social subject
    "the derivative is bad here",
    "I think my answer is wrong",
    "photosynthesis converts light energy",
    "the mitochondria is the powerhouse of the cell",
    "World War I started in 1914",
    "my teacher said unions formed in the 1800s",   # social subject, no verdict
    "what is the capital of France?",
    "I don't understand step three",
])
def test_no_false_positives_on_ordinary_teaching(text):
    """The safeguards must be INVISIBLE during normal tutoring.

    Both halves matter: an evaluative word with no social subject, and a social
    subject with no evaluation, must each stay silent. A layer that fires on
    ordinary lessons would be worse than none — it would add noise to every
    turn and train the model to ignore it.
    """
    assert stance_for(text)[0] == ORDINARY
    assert tutor_block(text, concept_text="## Concept\nAlgebra") == ""


# --- scenario 4: advocacy versus study --------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("The history of Holocaust denial", CONSENSUS),   # builds, as critique
    ("Holocaust denial", CONSENSUS),                  # bare topic still builds
    ("The truth about the Holocaust hoax", REFUSE),   # advocacy does not
    ("The Great Replacement explained", CONSENSUS),
])
def test_hate_vector_topics_turn_on_framing_not_subject(title, expected):
    assert course_frame(title)[0] == expected


def test_studying_denial_is_not_refused():
    """Over-refusal is a real failure, not a safe default.

    Declining "Holocaust denial" as a topic refuses the course that teaches
    people how denial actually works — the course most worth having.
    """
    assert refusal_for("Holocaust denial") is None
    assert refusal_for("The history of Holocaust denial") is None
    assert refusal_for("The truth about the Holocaust hoax") is not None


def test_hate_vector_never_builds_as_neutral_exposition():
    _, instr = course_frame("The Great Replacement explained")
    assert "used to argue against a group of people" in instr
    assert "do not reproduce its rhetoric" in instr.lower()


def test_conspiracy_framed_course_on_a_consensus_topic():
    """Scenario 2: an echo chamber with a syllabus."""
    stance, instr = course_frame(
        "Vaccines: the truth they dont want you to know")
    assert stance == CONSENSUS
    assert "announces a conclusion rather than naming a subject" in instr
    assert "WHO" in instr or "Cochrane" in instr


def test_pressure_reaches_the_real_prompt():
    """Wiring check — the escalation must survive the prompt builder."""
    from services.common.prompts import get_socratic_tutor_prompt
    history = [("the earth is flat", "")] * 3
    msgs = get_socratic_tutor_prompt("## Concept\nQuadratic equations", history)
    assert "THIRD TIME" in msgs[0]["content"]


# --- layer two: semantic ------------------------------------------------------
#
# Plumbing is tested with an injected embedder so the suite needs no Ollama.
# The RECALL and PRECISION numbers quoted in the design doc were measured
# against real bge-m3 embeddings and are recorded there, not asserted here —
# asserting them would make the suite depend on a model being pulled.

def _stub_embedder(mapping, dim=8):
    """Deterministic embedder: each listed phrase gets a fixed basis vector."""
    def _embed(texts):
        out = []
        for t in texts:
            v = [0.0] * dim
            v[mapping.get(t, dim - 1) % dim] = 1.0
            out.append(v)
        return out
    return _embed


def test_semantic_layer_fails_open_without_embeddings():
    """A safeguard that can break teaching is not one worth having."""
    from services.common.epistemic_stance import semantic_stance

    def _broken(_texts):
        raise RuntimeError("ollama down")

    assert semantic_stance("the earth isnt round", embed_fn=_broken) == (
        ORDINARY, None)
    # And the tutor block still renders rather than raising.
    assert tutor_block("the earth isnt round", embed_fn=_broken) == ""


def test_lexical_gate_skips_the_network_for_ordinary_text():
    """An ordinary maths turn must never reach the embedder."""
    from services.common.epistemic_stance import semantic_stance
    calls = []

    def _counting(texts):
        calls.append(texts)
        return [[1.0] + [0.0] * 7 for _ in texts]

    semantic_stance("how do I solve for x?", embed_fn=_counting)
    semantic_stance("what is the derivative of x squared?", embed_fn=_counting)
    assert calls == [], "embedder called for text with no loaded topic"


def test_short_text_is_not_embedded():
    from services.common.epistemic_stance import semantic_stance
    calls = []

    def _counting(texts):
        calls.append(texts)
        return [[1.0] + [0.0] * 7 for _ in texts]

    semantic_stance("earth", embed_fn=_counting)
    assert calls == []


def test_contrastive_neutral_exemplars_are_embedded():
    """The neutral cluster must actually be in the comparison set.

    Without it, five of fourteen ordinary questions were flagged — including
    "what is the circumference of the earth?". This asserts the mechanism that
    fixed that is present, not merely intended.
    """
    from services.common import epistemic_stance as es
    es._exemplar_cache.clear()
    seen = {}

    def _embed(texts):
        for t in texts:
            seen[t] = True
        return [[1.0] + [0.0] * 7 for _ in texts]

    es._exemplar_vectors(_embed)
    assert es._NEUTRAL in es._exemplar_cache
    for phrase in es.NEUTRAL_EXEMPLARS:
        assert phrase in seen
    es._exemplar_cache.clear()


def test_semantic_result_resolves_to_a_real_register_entry():
    """Every exemplar cluster must map onto an entry that actually exists."""
    from services.common.epistemic_stance import (
        _EXEMPLAR_TO_PROBE, EXEMPLARS, stance_for as sf)
    assert set(_EXEMPLAR_TO_PROBE) == set(EXEMPLARS)
    for probe in _EXEMPLAR_TO_PROBE.values():
        stance, entry = sf(probe)
        assert entry is not None, f"{probe!r} resolves to no register entry"
        assert stance == CONSENSUS


def test_semantic_can_be_switched_off():
    """`semantic=False` keeps the layer purely local, for tests and offline."""
    calls = []

    def _counting(texts):
        calls.append(texts)
        return [[1.0] + [0.0] * 7 for _ in texts]

    tutor_block("the earth isnt actually round", embed_fn=_counting,
                semantic=False)
    assert calls == []
