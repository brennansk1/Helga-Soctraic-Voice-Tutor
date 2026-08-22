"""A.7 — what KIND of learner this is, from how they write.

The judge scores `adaptation` on adjusting to "THIS student's demonstrated
level AND BEHAVIOUR". The system tracks level exhaustively and behaviour
nowhere — so a confident bluffer writing 60 fluent words with no mechanism and
a silent struggler writing "idk" both score grade 1 and get the same next turn.
That is the definition of following a script.

Measured on our own benchmark: `silent_struggler` averages 8.3 words per
answer, `confident_bluffer` 63.2. A 55-word spread the tutor cannot see.
"""
from services.common.learner_behaviour import (
    AHEAD, BLUFFING, GIVING_UP, HEDGING, TERSE,
    classify, describe, prompt_line,
)


# ------------------------------------------------------------- the refusal
def test_one_answer_is_a_mood_not_a_behaviour():
    assert classify(["idk"]) is None


def test_no_answers_claims_nothing():
    assert classify([]) is None and classify(None) is None


def test_an_ordinary_answer_gets_no_label():
    """Most learners are just answering. A tutor told 'this one is bluffing'
    about a normal student opens by challenging honesty never in question."""
    answers = ["An eigenvector keeps its direction because the matrix only "
               "scales it along that axis, so the transform acts like a "
               "stretch rather than a rotation.",
               "So the eigenvalue is the amount of that stretch, since it "
               "multiplies the vector without turning it."]
    assert classify(answers, grades=[4, 4]) is None


# ------------------------------------------------------------- the patterns
def test_explicit_surrender_outranks_everything():
    assert classify(["something", "I don't know"], grades=[2, 1]) == GIVING_UP
    assert classify(["a", "idk"], grades=[1, 1]) == GIVING_UP


def test_asking_to_move_on_is_believed_when_they_are_right():
    """'Ignored the student's explicit request to move on' — the judge, twice."""
    assert classify(["yes that is clear", "I already know this, next"],
                    grades=[4, 4]) == AHEAD


def test_asking_to_move_on_is_NOT_believed_when_they_are_wrong():
    """A bluffer also asks to move on."""
    assert classify(["sure", "I know this already, next"],
                    grades=[4, 1]) != AHEAD


def test_fluent_confident_and_mechanism_free_while_wrong_is_a_bluff():
    # Length matched to the MEASURED confident_bluffer profile (63.2 words per
    # answer). Fluent, technical, and it never says why anything happens.
    answers = ["The eigendecomposition exhibits spectral invariance under "
               "unitary conjugation, foundational to the operator formalism "
               "and its representation-theoretic underpinnings, and the "
               "canonical apparatus subsumes the diagonalisation criterion "
               "within the broader functional calculus of self-adjoint "
               "operators, as any treatment of the spectral theorem in a "
               "separable Hilbert space will readily confirm throughout.",
               "Indeed the invariant subspace decomposition follows directly "
               "from the resolvent formalism and its analytic continuation, "
               "with the characteristic polynomial furnishing the requisite "
               "algebraic multiplicity data alongside the geometric "
               "multiplicity of each eigenspace, a standard result in the "
               "operator-theoretic literature and its many modern expositions "
               "of finite-dimensional spectral analysis for such matrices."]
    assert classify(answers, grades=[1, 1]) == BLUFFING


def test_a_long_answer_that_EXPLAINS_is_not_a_bluff():
    """Mechanism language is the whole distinction."""
    answers = ["The vector keeps its direction because the matrix scales it "
               "along that axis, which means the transform acts as a stretch "
               "rather than a rotation of that particular vector.",
               "So the eigenvalue is the scale factor, since it multiplies "
               "the vector without turning it, which is why direction holds."]
    assert classify(answers, grades=[1, 1]) != BLUFFING


def test_a_long_CORRECT_answer_is_never_a_bluff():
    answers = ["word " * 50, "word " * 50]
    assert classify(answers, grades=[4, 4]) != BLUFFING


def test_hedging_is_recognised_as_close_not_wrong():
    assert classify(["the vector changes", "maybe it stays the same length?"],
                    grades=[2, 2]) == HEDGING


def test_short_answers_are_terse_not_refusal():
    assert classify(["bigger", "the same"], grades=[2, 2]) == TERSE


def test_terse_matches_the_measured_silent_struggler():
    """8.3 words is the measured mean for that profile."""
    assert classify(["it gets longer", "not sure what you mean", "the arrow"],
                    grades=[2, 1, 2]) in (TERSE, HEDGING)


# -------------------------------------------------------------- the output
def test_every_behaviour_yields_an_instruction_not_an_observation():
    for b in (TERSE, GIVING_UP, BLUFFING, HEDGING, AHEAD):
        line = prompt_line(b)
        assert line.startswith("THIS LEARNER RIGHT NOW:")
        assert len(line) > 60, f"{b}: too thin to act on"


def test_the_bluffing_instruction_forbids_praising_fluency():
    assert "Do not praise the fluency" in prompt_line(BLUFFING)


def test_the_giving_up_instruction_stops_questioning():
    line = prompt_line(GIVING_UP)
    assert "Stop questioning" in line


def test_no_behaviour_yields_no_line():
    assert prompt_line(None) == ""
    assert describe(["only one"]) == ""


def test_malformed_input_never_raises():
    for bad in ([None, None], [1, 2], ["", ""]):
        assert classify(bad) is None or isinstance(classify(bad), str)
