"""A4.1a — the dialogue contract.

The point of this being code rather than prompt text: prompt-only enforcement
measured 0/5 in this repository, while a correction round that NAMES the
offender measured 5/5. So every violation carries the measurement ("143 words;
the limit is 60") rather than a wish ("be concise").

The judge scores socratic at 2.10/5 and flags the same two things every time --
lecturing instead of questioning, and answering something other than what the
student said. Those are the `length` and `reference` rules.
"""
import pytest

from services.common import dialogue_contract as dc

TERMS = {"eigenvalue", "eigenvector", "determinant", "matrix"}


def _long(n):
    return " ".join(["word"] * n) + "?"


# ----------------------------------------------------------------- length
def test_a_short_turn_ending_in_a_question_passes():
    assert dc.check("You said the vector flips. What does that tell you?",
                    learner_said="the vector flips") == []


def test_a_lecture_is_caught_with_its_measurement():
    v = dc.check(_long(120), learner_said="word", is_opening=True)
    lengths = [x for x in v if x.rule == "length"]
    assert lengths, "a 120-word turn is a lecture"
    assert "120 words" in lengths[0].detail
    assert "60" in lengths[0].instruction, (
        "the correction must state the limit, not just say 'be concise'")


def test_the_limit_is_a_boundary_not_a_suggestion():
    assert not [x for x in dc.check(_long(60), is_opening=True)
                if x.rule == "length"]
    assert [x for x in dc.check(_long(61), is_opening=True)
            if x.rule == "length"]


def test_a_diagram_does_not_count_toward_the_word_cap():
    """A figure is not a lecture; counting its JSON would punish drawing."""
    aid = '```aid\n{"kind":"plot","title":"' + " ".join(["x"] * 200) + '"}\n```'
    turn = "Where does the vector land?" + aid
    assert dc.word_count(turn) < 10
    assert not [x for x in dc.check(turn, is_opening=True) if x.rule == "length"]


# --------------------------------------------------------------- question
def test_a_turn_that_does_not_ask_anything_is_caught():
    v = dc.check("An eigenvalue scales its eigenvector.", is_opening=True)
    assert any(x.rule == "question" for x in v)


def test_a_question_before_a_diagram_still_counts_as_ending_in_one():
    turn = 'Where does it land?\n```aid\n{"kind":"plot"}\n```'
    assert dc.ends_with_question(turn)


# -------------------------------------------------------------- reference
def test_ignoring_what_the_learner_said_is_caught():
    """The judge's other standing complaint: answering something else."""
    v = dc.check("Consider instead the determinant. What is it?",
                 learner_said="I thought eigenvectors had to be unit length",
                 concept_terms=TERMS)
    assert any(x.rule == "reference" for x in v)


def test_engaging_with_the_learner_passes():
    v = dc.check("You said unit length — why would that be required?",
                 learner_said="I thought eigenvectors had to be unit length")
    assert not any(x.rule == "reference" for x in v)


def test_the_opening_turn_is_exempt():
    """There is nothing to reference before the learner has spoken."""
    v = dc.check("What do you already know about eigenvalues?",
                 learner_said="", is_opening=True)
    assert not any(x.rule == "reference" for x in v)


def test_reference_matching_is_not_substring_matching():
    """'war' in 'aware' has bitten this codebase three times.

    A contract that fires on a coincidence is worse than no contract, so
    overlap is on whole content words.
    """
    v = dc.check("Are you aware of the warranty?", learner_said="war",
                 concept_terms=set())
    assert any(x.rule == "reference" for x in v), (
        "'war' inside 'aware'/'warranty' must not count as engagement")


def test_stopword_overlap_is_not_engagement():
    v = dc.check("The and of it is that? ", learner_said="the mediator problem")
    assert any(x.rule == "reference" for x in v)


# ----------------------------------------------------------- one new idea
def test_two_new_technical_terms_at_once_is_caught():
    v = dc.check("Consider the eigenvalue and the determinant together. Why?",
                 learner_said="consider", concept_terms=TERMS, already_seen=set())
    assert any(x.rule == "one_new_idea" for x in v)


def test_one_new_term_is_allowed():
    v = dc.check("What does the eigenvalue tell you about consider?",
                 learner_said="consider", concept_terms=TERMS, already_seen=set())
    assert not any(x.rule == "one_new_idea" for x in v)


def test_terms_already_used_are_not_new():
    v = dc.check("So the eigenvalue and eigenvector — which scales which?",
                 learner_said="eigenvalue eigenvector", concept_terms=TERMS,
                 already_seen={"eigenvalue", "eigenvector"})
    assert not any(x.rule == "one_new_idea" for x in v)


def test_ordinary_english_is_not_a_technical_idea():
    """Only the subject's own vocabulary counts."""
    v = dc.check("Suppose instead we imagine a peculiar orchard? ",
                 learner_said="suppose", concept_terms=TERMS)
    assert not any(x.rule == "one_new_idea" for x in v)


# ------------------------------------------------------------- the output
def test_the_correction_names_every_offender():
    v = dc.check(_long(90).replace("?", "."), learner_said="mediators",
                 concept_terms=TERMS)
    note = dc.correction_note(v)
    assert "90 words" in note and "did not end with a question" in note
    for viol in v:
        assert viol.instruction in note


def test_no_violations_means_no_correction():
    assert dc.correction_note([]) == ""


def test_an_empty_turn_yields_no_violations():
    """Nothing to regenerate against; the caller has a bigger problem."""
    assert dc.check("") == [] and dc.check(None) == []


# ---------------------------------------------------------- is_better
def test_a_retry_that_fixes_nothing_is_not_an_improvement():
    """The rule that stops this being prompt-only enforcement in disguise."""
    bad = _long(200)
    also_bad = _long(180)
    assert not dc.is_better(also_bad, bad, is_opening=True), (
        "both break the same single rule; newer is not better")


def test_a_retry_that_fixes_a_rule_is_an_improvement():
    bad = "An eigenvalue scales its eigenvector."          # no question
    good = "You mentioned scaling — by how much?"
    assert dc.is_better(good, bad, learner_said="scaling")


def test_a_retry_that_trades_one_violation_for_another_is_not_better():
    kw = {"learner_said": "mediators", "concept_terms": TERMS}
    too_long = _long(90)                                   # length only
    ignores = "Consider the determinant instead."          # question+reference
    assert not dc.is_better(ignores, too_long, **kw)


# ------------------------------- A.1: claims about the student must be true
#
# The measured failure on the maths run, three times in fifteen dialogues:
# the tutor apologised for confusion that never happened, asserted a
# calculation error the student had not made, and let a student's false claim
# stand. History reaches the model correctly paired and untruncated, so it HAS
# the transcript and misremembers it.
#
# `reference` cannot catch this. It asks whether the turn overlaps the
# learner's words at all, so an invented attribution passes on one shared
# noun. This asks whether the attribution is SUPPORTED.

def test_an_invented_attribution_is_caught():
    v = dc.check("You said the derivative is negative — why?",
                 learner_said="I think eigenvectors keep their direction",
                 concept_terms=TERMS)
    assert any(x.rule == "grounded_claim" for x in v)


def test_a_supported_attribution_passes():
    v = dc.check("You said eigenvectors keep their direction — always?",
                 learner_said="I think eigenvectors keep their direction")
    assert not any(x.rule == "grounded_claim" for x in v)


def test_apologising_for_confusion_that_never_happened_is_caught():
    """Verbatim from the judge: 'apologizing for confusion that never existed'."""
    v = dc.check("Sorry for the confusion earlier — shall we restart?",
                 learner_said="", is_opening=True)
    assert any(x.rule == "grounded_claim" for x in v)


def test_an_attribution_on_the_opening_turn_is_always_ungrounded():
    """They have not said anything yet, so nothing can be attributed."""
    v = dc.check("As you noted, the matrix scales it. What else?",
                 learner_said="", is_opening=True)
    viol = [x for x in v if x.rule == "grounded_claim"]
    assert viol and "first turn" in viol[0].instruction


def test_a_question_is_not_an_attribution():
    """"What do you think?" claims nothing and must not trip the rule."""
    v = dc.check("What do you think happens to the vector?",
                 learner_said="the matrix scales it")
    assert not any(x.rule == "grounded_claim" for x in v)


def test_a_suggestion_is_not_an_attribution():
    v = dc.check("You might consider what stays fixed — which vector does?",
                 learner_said="the matrix scales it")
    assert not any(x.rule == "grounded_claim" for x in v)


def test_a_direct_quote_grounds_the_claim():
    v = dc.check('You said "eigenvectors keep direction" — under what condition?',
                 learner_said="eigenvectors keep direction I think")
    assert not any(x.rule == "grounded_claim" for x in v)


def test_grounding_can_look_further_back_than_the_last_message():
    """A tutor may fairly refer to something said two turns ago."""
    v = dc.check("Earlier you mentioned the rubber sheet — does it still hold?",
                 learner_said="not sure",
                 recent_learner=["it is like a rubber sheet stretching"])
    assert not any(x.rule == "grounded_claim" for x in v)


def test_grounding_is_not_substring_matching():
    """Fifth time this class of bug would have bitten; it does not."""
    v = dc.check("You said you were aware of the warranty — were you?",
                 learner_said="war")
    assert any(x.rule == "grounded_claim" for x in v)


def test_the_correction_tells_the_model_what_to_do_about_it():
    v = dc.check("Your error was in the second step — see it?",
                 learner_said="eigenvectors keep their direction")
    viol = [x for x in v if x.rule == "grounded_claim"][0]
    assert "quote their exact words" in viol.instruction
    assert "eigenvectors keep their direction" in viol.instruction
