"""A.6 — the teaching move chosen in code rather than by the model.

WHY THIS EXISTS
---------------
B.1 ran this experiment already and it worked: the diagram decision moved out
of the model into a deterministic policy, the prompt then stated what to do,
and `visual_policy` rose in all five domains measured (+0.53 to +1.20), every
one outside its floor.

The teaching move itself was never moved. The model still decides each turn
whether to probe, hint, show an example, correct, advance or tell — while also
generating the language. The hint ladder exists only as prose in the system
prompt, and prose-only enforcement measures 0/5 in this repository.

`adaptation` sits at 1.40-2.20 across seven domains, against a rubric asking
whether the tutor adjusted to THIS student "rather than following a script".
"""
from services.common.teaching_move import (
    ADVANCE, CORRECT, HINT, MOVES, PROBE, TELL, WORKED_EXAMPLE,
    decide_move, from_turn_state,
)
from services.common.turn_state import TurnState


def _graded(grade, reason="", missing=None):
    return {"grade": grade, "reason": reason, "missing_concepts": missing or [],
            "graded": True}


# ------------------------------------------------------------- precedence
def test_arbitrary_content_is_told_not_probed():
    """There is no reasoning path to probe along."""
    m = decide_move(is_opening=True, is_arbitrary=True)
    assert m.move == TELL


def test_arbitrary_content_already_stated_is_not_told_again():
    """Stating a fact the student already has is a loop, not honesty — this is
    the C.1b failure, measured as adaptation 2.80 -> 2.07."""
    m = decide_move(is_arbitrary=True, already_told=True)
    assert m.move != TELL


def test_an_unaddressed_error_outranks_advancing_and_probing():
    """Four of fifteen measured failures were 'the student erred and the tutor
    moved on'."""
    assert decide_move(open_error=True, correct_streak=5).move == CORRECT


def test_being_stuck_outranks_correcting():
    """After two failures the error is usually WHY they are stuck, so naming
    it again and asking again is the loop. Show them instead."""
    assert decide_move(open_error=True, attempts=3).move == WORKED_EXAMPLE


def test_stuck_twice_stops_asking():
    """'Repeats the same question in different words' is the single most
    frequent complaint the judge makes."""
    assert decide_move(attempts=2, last_grade=1).move == WORKED_EXAMPLE
    assert decide_move(attempts=5, last_grade=1).move == WORKED_EXAMPLE


def test_stuck_once_gets_a_hint_not_an_example():
    assert decide_move(attempts=1, last_grade=1).move == HINT


def test_a_correct_first_answer_is_not_treated_as_stuck():
    """attempts==1 alone must not mean failure — they may have got it."""
    assert decide_move(attempts=1, last_grade=4).move != HINT


def test_demonstrated_understanding_advances():
    m = decide_move(correct_streak=2, last_grade=4)
    assert m.move == ADVANCE


def test_the_opening_turn_never_advances():
    """Nothing has been demonstrated yet, whatever a stale streak says."""
    assert decide_move(correct_streak=9, is_opening=True).move == PROBE


def test_the_default_is_to_probe():
    assert decide_move().move == PROBE


# -------------------------------------------------------------- the output
def test_every_move_carries_an_actionable_instruction():
    for move in MOVES:
        m = decide_move()
        m.move = move
        line = m.prompt_line()
        assert line.startswith("THIS TURN"), move
        assert move in line
        assert len(line) > 40, f"{move}: instruction too thin to act on"


def test_the_instructions_tell_rather_than_suggest():
    """'You may consider probing' is a wish. B.1 measured the difference."""
    for move in MOVES:
        m = decide_move()
        m.move = move
        line = m.prompt_line().lower()
        for hedge in ("you may", "consider whether", "if you think", "perhaps"):
            assert hedge not in line, f"{move} hedges: {line}"


def test_an_unknown_move_yields_no_prompt_line():
    m = decide_move()
    m.move = "NONSENSE"
    assert m.prompt_line() == ""


def test_every_decision_states_a_reason():
    for kw in ({}, {"open_error": True}, {"attempts": 2},
               {"is_arbitrary": True, "is_opening": True},
               {"correct_streak": 3}):
        assert decide_move(**kw).reason


# ------------------------------------------------- driven by the A.2 state
def test_it_reads_the_attempt_count_from_turn_state():
    ts = TurnState()
    ts.ask("Why is it non-zero?")
    ts.record("dunno", _graded(1))
    ts.record("still dunno", _graded(1))
    assert from_turn_state(ts, last_grade=1).move == WORKED_EXAMPLE


def test_it_reads_an_outstanding_error_from_turn_state():
    ts = TurnState()
    ts.ask("What is the eigenvalue?")
    ts.record("the vector", _graded(1, reason="named an object not a factor"))
    assert from_turn_state(ts, last_grade=1).move == CORRECT


def test_an_error_the_student_later_fixed_does_not_keep_forcing_CORRECT():
    ts = TurnState()
    ts.ask("What is the eigenvalue?")
    ts.record("the vector", _graded(1, reason="wrong object"))
    ts.record("the scaling factor", _graded(4))
    assert from_turn_state(ts, last_grade=4).move != CORRECT


def test_a_missing_or_broken_state_does_not_cost_the_turn():
    for bad in (None, object(), "not a state"):
        assert from_turn_state(bad).move in MOVES


# ------------------------------- behaviour must OUTRANK the miss counter
#
# Measured before this existed: five wildly different learner profiles produced
# the SAME move (WORKED_EXAMPLE) because the selector keyed almost entirely on
# miss count, and four of five miss twice. That is the script `adaptation`
# punishes.
#
# It was also silently broken once: `from_turn_state` accepted `behaviour` and
# never passed it to `decide_move`. The signature said one thing and the call
# did another, and nothing failed.

def test_from_turn_state_actually_passes_behaviour_through():
    """The regression that made this whole feature a no-op."""
    ts = TurnState()
    ts.ask("q")
    ts.record("a", _graded(1))
    ts.record("b", _graded(1))
    assert from_turn_state(ts, last_grade=1, behaviour="BLUFFING").move == CORRECT
    assert from_turn_state(ts, last_grade=1, behaviour="AHEAD").move == ADVANCE


def test_a_bluffer_is_challenged_not_shown_the_answer():
    """A worked example REWARDS the bluff — it hands over the substance they
    were pretending to have."""
    assert decide_move(last_grade=1, attempts=3, behaviour="BLUFFING").move == CORRECT


def test_giving_up_does_not_wait_for_a_second_miss():
    """They told you. Continuing to question is the measured failure."""
    assert decide_move(last_grade=1, attempts=0,
                       behaviour="GIVING_UP").move == WORKED_EXAMPLE


def test_a_hedging_learner_is_probed_not_lectured():
    """They are reasoning and close; explaining now takes it away."""
    assert decide_move(last_grade=2, attempts=1, behaviour="HEDGING").move == PROBE


def test_the_same_miss_count_yields_different_moves_by_behaviour():
    """The whole point, in one assertion."""
    moves = {b: decide_move(last_grade=1, attempts=2, behaviour=b).move
             for b in ("BLUFFING", "GIVING_UP", "AHEAD", None)}
    assert len(set(moves.values())) >= 3, moves
