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


# ---------------------------------------------- A.3: do not ask it twice
#
# The most frequent complaint in the judge's own words, across every domain:
# "repeats the exact same dictionary analogy and question verbatim",
# "repeats the definition of zero-based indexing verbatim", "the tutor's final
# turn repeats the exact same question about the third item's index".
#
# MathTutorBench reports the same thing from the other side: tutoring gets
# harder in longer dialogues, "where simpler questioning strategies begin to
# fail" — a model out of moves repeats the one it already made.

PREV = ["Where does the vector land after the transform?"]


def test_a_verbatim_repeat_is_caught():
    assert dc.repeats_earlier_turn(PREV[0], PREV) is not None


def test_a_reworded_repeat_is_caught():
    """The judge counts "in slightly different words" as repetition too."""
    assert dc.repeats_earlier_turn(
        "So where does that vector land after the transform?", PREV) is not None


def test_a_genuinely_new_question_is_not_caught():
    """The rule must not punish staying on topic."""
    assert dc.repeats_earlier_turn(
        "What stays fixed when the matrix acts?", PREV) is None


def test_reusing_the_concept_vocabulary_is_not_repetition():
    """Every turn of an eigenvalue lesson says 'eigenvalue'. That is normal."""
    prev = ["An eigenvalue scales its eigenvector — what does that mean here?"]
    assert dc.repeats_earlier_turn(
        "Can an eigenvalue ever be negative for a real matrix?", prev) is None


def test_the_same_question_wrapped_in_new_framing_is_still_a_repeat():
    """New words around an identical question still reads as being asked
    the same thing twice."""
    prev = ["Think about a list of 1024 items. How many halvings to reach one?"]
    turn = ("Let us try a different angle, using a dictionary instead. "
            "How many halvings to reach one?")
    assert dc.repeats_earlier_turn(turn, prev) is not None


def test_it_searches_all_earlier_turns_not_just_the_last():
    prev = ["Where does the vector land after the transform?",
            "What does the scale factor tell you?"]
    assert dc.repeats_earlier_turn(
        "Where does the vector land after the transform?", prev) is not None


def test_the_aid_fence_is_not_compared():
    """Two turns sharing a diagram are not therefore the same question."""
    aid = '```aid\n{"kind":"plot","title":"the very same long title here"}\n```'
    prev = ["What is the slope at that point?" + aid]
    turn = "Why does the curve flatten near the origin?" + aid
    assert dc.repeats_earlier_turn(turn, prev) is None


def test_no_previous_turns_means_nothing_to_repeat():
    assert dc.repeats_earlier_turn("Anything at all?", []) is None
    assert dc.repeats_earlier_turn("Anything at all?", None) is None
    assert dc.repeats_earlier_turn("", PREV) is None


def test_similarity_is_not_substring_matching():
    """Fifth place this bug class would have landed."""
    assert dc._similarity("war", "aware warranty") < 0.5


# ------------------- A.5: a question with framing, not a lecture with a question
#
# The contract already forced <=60 words and ends-with-a-question, compliance
# hit ~100%, and `socratic` did not move. Measuring real transcripts showed
# why: the mean tutor turn carries 2.53 declarative sentences before its
# question and 45% carry three or more. A turn that explains for four sentences
# then asks something satisfied every rule we had — and is exactly what the
# judge calls a mini-lecture.

LECTURE = ("An eigenvalue is a scalar. It scales its eigenvector. "
           "The vector keeps its direction. This is why they matter. "
           "What happens when it is negative?")


def test_a_mini_lecture_is_counted():
    assert len(dc.statements(LECTURE)) == 4
    assert len(dc.statements(LECTURE)) > dc.MAX_STATEMENTS


def test_brief_framing_before_a_question_is_allowed():
    """Two sentences of setup is Socratic form, not exposition."""
    turn = "You said it stretches. The matrix acts on it. Does direction change?"
    assert len(dc.statements(turn)) <= dc.MAX_STATEMENTS


def test_a_bare_question_carries_no_statements():
    assert dc.statements("Does the direction change?") == []


def test_the_aid_fence_is_not_exposition():
    """A diagram's JSON is not sentences the student reads."""
    turn = ('What changes here?\n```aid\n{"kind":"plot","title":"A. B. C. D."}\n```')
    assert dc.statements(turn) == []


def test_a_numeric_fragment_is_not_a_sentence():
    """"3." is a list marker, not a sentence of explanation."""
    assert dc.statements("3. 42. Why?") == []


def test_word_count_and_statement_count_are_different_measures():
    """The whole point: this catches turns the word cap does not.

    A four-sentence lecture can sit well under 60 words.
    """
    assert dc.word_count(LECTURE) < dc.MAX_WORDS
    assert len(dc.statements(LECTURE)) > dc.MAX_STATEMENTS


# ------------------- A.8: say what they missed, not just "Correct."
#
# Found by reading our own transcripts. Comparing dialogues the judge scored
# `adaptation` 5 against ones it scored 2, the difference is not length, not
# question form, and not the teaching move. It is DIAGNOSTIC SPECIFICITY.
#
#   5/5: "You correctly identified that you gave value, but missed that your
#         friend gave none."
#   2/5: "Correct. The eigenvalue lambda=3 describes the stretch. However..."
#        — three turns in a row opening with a bare "Correct."
#
# The low one is accurate. It just never tells the student what THEY showed or
# left out, and a turn that could follow any student's answer is the script
# `adaptation` punishes.

GAPS = ["non-zero requirement", "mutual exchange of value"]


def test_a_bare_acknowledgement_is_recognised():
    for turn in ("Correct. The eigenvalue describes the stretch. And now?",
                 "Right, so what happens next?",
                 "Exactly! Why is that?",
                 "Good. What about the reverse case?"):
        assert dc.opens_with_generic_praise(turn), turn


def test_a_specific_opening_is_not_generic():
    for turn in ("You got the scaling right but missed the non-zero part. Why?",
                 "You said it flips — does that hold for every vector?"):
        assert not dc.opens_with_generic_praise(turn), turn


def test_ignoring_a_named_gap_is_caught():
    turn = "Correct. The eigenvalue describes the stretch. What happens next?"
    v = dc.check(turn, learner_said="stretch", missing_concepts=GAPS)
    assert any(x.rule == "addresses_gap" for x in v)


def test_engaging_with_the_gap_passes():
    turn = ("You got the scaling right but missed the non-zero requirement. "
            "Why must it be non-zero?")
    v = dc.check(turn, learner_said="scaling", missing_concepts=GAPS)
    assert not any(x.rule == "addresses_gap" for x in v)


def test_addressing_ANY_named_gap_is_enough():
    """One turn, one idea — the contract already forbids piling them up."""
    turn = "You showed value passed one way, but was there mutual exchange?"
    assert dc.addresses_gap(turn, GAPS)


def test_no_named_gap_means_no_rule():
    """The grader found nothing missing; there is nothing to demand."""
    turn = "Correct. What happens next?"
    for gaps in ([], None, [""]):
        assert not any(x.rule == "addresses_gap"
                       for x in dc.check(turn, learner_said="x",
                                         missing_concepts=gaps))


def test_gap_matching_is_not_substring_matching():
    """Fifth place this bug class would have landed."""
    assert not dc.addresses_gap("Are you aware of the warranty?", ["war"])


def test_the_correction_names_the_gap_and_says_what_to_do():
    turn = "Correct. Next question?"
    v = [x for x in dc.check(turn, learner_said="x", missing_concepts=GAPS)
         if x.rule == "addresses_gap"][0]
    assert "non-zero requirement" in v.instruction
    assert "what they got right" in v.instruction.lower()
    assert '"Correct." tells them nothing' in v.instruction


def test_A8_constrains_one_element_not_the_whole_turn():
    """The lesson from A.6: dictating every turn removed variation and drove
    `adaptation` DOWN 0.53. Two turns that both address the gap may otherwise
    look completely different, and both must pass."""
    a = "You missed the non-zero requirement — why does zero break it?"
    b = ("Your exchange point was right. Nothing was given back though. "
         "What does mutual exchange of value require here?")
    for turn in (a, b):
        assert not any(x.rule == "addresses_gap"
                       for x in dc.check(turn, learner_said="value",
                                         missing_concepts=GAPS)), turn


# ------------------ A.8 must discriminate, not pass on a shared filler word
#
# Live grader output comes back as scaffolded noun phrases:
#   "Explanation of the mathematical relationship Av = lambda v"
#   "Context of how the eigenvalue acts on an eigenvector"
#
# Matching on ANY shared content word meant a turn saying "what is the
# relationship here" passed by hitting "relationship" — addressing nothing.
# That is a silent non-firing, which is the failure mode this repository keeps
# finding: a rule that looks active and enforces nothing.

REAL_GAPS = ["Explanation of the mathematical relationship Av = lambda v",
             "Context of how the eigenvalue acts on an eigenvector"]


def test_matching_only_the_graders_scaffolding_does_not_count():
    assert not dc.addresses_gap(
        "What is the relationship here, in your own explanation?", REAL_GAPS)


def test_naming_the_substance_counts():
    assert dc.addresses_gap(
        "You missed how the eigenvalue acts on it — how does it?", REAL_GAPS)


def test_a_gap_with_no_substance_is_not_enforced():
    """"Explanation of the concept" is all scaffolding — it names nothing a
    turn could be required to mention, so failing a turn on it would be
    arbitrary."""
    assert dc.addresses_gap("anything at all", ["Explanation of the concept"])


def test_scaffolding_stripping_does_not_reintroduce_substring_matching():
    assert not dc.addresses_gap("Are you aware of the warranty?", ["war"])


def test_a_real_generic_turn_still_fails_against_real_gaps():
    """The case this rule exists for, using verbatim live grader output."""
    turn = "Correct. What happens if it is negative?"
    v = dc.check(turn, learner_said="it gets bigger",
                 missing_concepts=REAL_GAPS)
    assert any(x.rule == "addresses_gap" for x in v)
