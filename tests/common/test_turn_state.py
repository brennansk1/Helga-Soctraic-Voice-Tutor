"""A.2 — structured turn state.

`adaptation` measured 1.33-2.80 across seven domains, worse than `socratic`.
The tutor is handed a transcript and asked to re-derive what the student has
established, every turn, while also teaching. The grader already produces that
as data and it was being thrown away.

The property protected hardest here is the REFUSAL to record a fallback grade.
`_parse_grade_response` returns grade 2 with `graded: False` when the LLM call
fails — a fail-safe that never credits mastery. If that became "the student
partly understood this", then during an outage the tutor would invent an
entire history of half-understanding that never happened.
"""
from services.common.turn_state import TurnState, MAX_CHARS


def _graded(grade, reason="", missing=None, feedback=""):
    return {"grade": grade, "reason": reason, "feedback": feedback,
            "missing_concepts": missing or [], "graded": True,
            "grade_source": "llm"}


def _fallback():
    """Exactly what the parser emits when the grading call fails."""
    return {"grade": 2, "missing_concepts": [], "feedback": "", "reason": "",
            "graded": False, "grade_source": "fallback"}


# ----------------------------------------------------------------- the refusal
def test_a_fresh_state_renders_nothing():
    """No invented scaffold on the opening turn."""
    assert TurnState().render() == ""
    assert TurnState().is_empty()


def test_a_fallback_grade_is_not_evidence_about_the_learner():
    """The single most important rule in this module."""
    s = TurnState()
    s.ask("Why does the vector keep its direction?")
    s.record("no idea", _fallback())
    assert s.render() == "", (
        "an LLM outage must not manufacture a history of half-understanding")


def test_a_real_grade_of_two_IS_recorded():
    """The fallback is refused for being ungraded, not for being a 2."""
    s = TurnState()
    s.ask("Why does the vector keep its direction?")
    s.record("it gets longer", _graded(2, reason="direction not addressed"))
    assert "STILL WRONG" in s.render()


def test_a_malformed_grade_result_is_ignored_not_crashed():
    s = TurnState()
    s.ask("q")
    for junk in (None, "grade 3", 42, [], {}):
        s.record("a", junk)
    assert s.render() == ""


# -------------------------------------------------------------- what it says
def test_a_correct_answer_becomes_established():
    s = TurnState()
    s.ask("What happens to the direction?")
    s.record("it stays the same, only the length changes", _graded(4))
    out = s.render()
    assert "ALREADY ESTABLISHED" in out
    assert "direction" in out
    assert "Do not re-teach" in out


def test_a_wrong_answer_is_carried_with_its_reason():
    s = TurnState()
    s.ask("What is the eigenvalue?")
    s.record("the vector itself", _graded(1, reason="confused vector with scalar"))
    out = s.render()
    assert "STILL WRONG" in out
    assert "the vector itself" in out
    assert "confused vector with scalar" in out
    assert "Address the error" in out


def test_getting_it_right_later_clears_the_error():
    """Otherwise the tutor keeps correcting something already fixed."""
    s = TurnState()
    s.ask("What is the eigenvalue?")
    s.record("the vector", _graded(1, reason="wrong object"))
    assert "STILL WRONG" in s.render()
    s.record("the scaling factor", _graded(4))
    out = s.render()
    assert "STILL WRONG" not in out
    assert "ALREADY ESTABLISHED" in out


def test_missing_concepts_are_surfaced():
    s = TurnState()
    s.ask("Define it")
    s.record("something", _graded(2, missing=["non-zero requirement",
                                              "the characteristic equation"]))
    assert "NOT YET COVERED" in s.render()
    assert "non-zero requirement" in s.render()


def test_repeated_attempts_tell_the_tutor_to_change_approach():
    """Four of fifteen failures were the tutor re-asking the same question."""
    s = TurnState()
    s.ask("Why is it non-zero?")
    s.record("dunno", _graded(1))
    s.record("still dunno", _graded(1))
    out = s.render()
    assert "CHANGE YOUR APPROACH" in out
    assert "already failed twice" in out


def test_the_change_approach_instruction_SURVIVES_A_REWORDING():
    """The defect this catches.

    `attempts` counts tries at one question STRING and is reset by a
    rewording — which is the first thing a tutor does when a learner is stuck.
    `misses` was added to survive that, and `render` went on reading
    `attempts`. Measured across 60 benchmark dialogues: `attempts` reached 2 in
    NONE of them, so the instruction never once appeared in a prompt.

    The judge's rationale for the worst-scoring dialogues names exactly this:
    "The tutor repeated the same question about walking West after the student
    already answered 'idk'."
    """
    s = TurnState()
    s.ask("Why must the vector be non-zero?")
    s.record("dunno", _graded(1))
    s.ask("Put another way — what would a zero vector point at?")
    s.record("still dunno", _graded(1))

    assert s.attempts == 1, "the rewording reset the per-question counter"
    assert s.misses == 2, "but the consecutive-failure counter survived it"
    assert "CHANGE YOUR APPROACH" in s.render()


def test_getting_it_right_clears_the_stuck_state():
    """A learner who recovers must not keep being told they are stuck."""
    s = TurnState()
    s.ask("Why non-zero?")
    s.record("dunno", _graded(1))
    s.record("still dunno", _graded(1))
    s.record("because a point has no direction", _graded(5))
    assert "CHANGE YOUR APPROACH" not in s.render()


def test_a_new_question_resets_the_attempt_count():
    s = TurnState()
    s.ask("first")
    s.record("a", _graded(1))
    s.record("b", _graded(1))
    s.ask("second")
    assert s.attempts == 0
    assert "tried this question" not in s.render()


def test_asking_the_same_question_again_does_not_reset_attempts():
    s = TurnState()
    s.ask("same")
    s.record("a", _graded(1))
    s.ask("same")
    s.record("b", _graded(1))
    assert s.attempts == 2


# ------------------------------------------------------------------- bounds
def test_it_is_bounded_because_it_rides_in_every_turn():
    s = TurnState()
    for i in range(40):
        s.ask(f"question number {i} about a reasonably long topic name")
        s.record(f"an answer of some length number {i}", _graded(4))
    out = s.render()
    assert len(out) <= 600
    assert out.count("they answered") <= 4


def test_a_long_answer_is_quoted_not_dumped():
    s = TurnState()
    s.ask("q")
    s.record("word " * 200, _graded(4))
    assert len(s.render()) <= 600


def test_unresolved_concepts_do_not_grow_without_bound():
    s = TurnState()
    s.ask("q")
    for i in range(20):
        s.record("a", _graded(2, missing=[f"concept {i}"]))
    assert len(s.unresolved) <= 3


# -------------------------------------------------- it states fact, not opinion
def test_it_does_not_editorialise_about_the_learner():
    """"This student is struggling" is an impression. The point is to replace
    impressions with the record."""
    s = TurnState()
    s.ask("q")
    s.record("wrong thing", _graded(1, reason="missed the mechanism"))
    out = s.render().lower()
    for editorial in ("struggling", "weak student", "poor", "confused learner",
                      "seems to", "probably"):
        assert editorial not in out, f"editorialising: {editorial}"
    assert "fact, not your impression" in s.render().lower() or \
           "this is fact" in s.render().lower()


def test_the_instruction_survives_truncation():
    """The second half of the defect.

    `render()` ends `[:MAX_CHARS]` with MAX_CHARS = 600, and the
    change-your-approach line used to be appended LAST — after the established
    list, the errors and the unresolved topics.

    Measured across 60 benchmark dialogues: 26 were eligible for the
    instruction, and in 25 of those it was generated and then CUT OFF by the
    character cap. It reached exactly one prompt.

    Everything else in this block is DESCRIPTION of what the learner has shown.
    This one line is the INSTRUCTION. Truncating the instruction and keeping
    the description is the wrong way round.
    """
    s = TurnState()
    s.ask("Why must it be non-zero?")
    s.record("dunno", _graded(1))
    s.record("still dunno", _graded(1))
    # Flood the descriptive parts well past MAX_CHARS.
    for i in range(6):
        s.ask(f"Question number {i} about a fairly long topic name here")
        s.record("wrong answer that is quite long indeed " * 3, _graded(1))

    out = s.render()
    assert len(out) <= MAX_CHARS
    assert "CHANGE YOUR APPROACH" in out, (
        "the instruction was truncated away by the descriptive history")


def test_a_stuck_learner_alone_is_not_an_empty_state():
    """`is_empty` gated on established/errors/unresolved only, so a learner
    who has just failed twice could render nothing at all — which is exactly
    the moment the block exists to act on."""
    s = TurnState()
    s.misses = 2
    assert not s.is_empty()


def test_the_stuck_instruction_does_not_cancel_a_requested_figure():
    """The instruction must not read as "explain in prose instead".

    Kept on PRINCIPLE, not on evidence. One run suggested this line suppressed
    diagrams — missed figures 20-33% -> 47%, visual_policy down 0.63 past its
    floor — and the next run on the identical pre-fix config produced 7%, the
    best of any run. The regression did not exist.

    The wording stays because a worked answer IS a better figure than a
    paragraph, and an instruction open to the prose reading is worth closing
    whether or not it has yet done harm.
    """
    s = TurnState()
    s.ask("Why must it be non-zero?")
    s.record("dunno", _graded(1))
    s.record("still dunno", _graded(1))
    out = s.render()
    assert "CHANGE YOUR APPROACH" in out
    assert "make that figure carry" in out
    assert "skipping it" in out
