"""A.6 — decide the TEACHING MOVE in code, then tell the model to make it.

THE PATTERN, AND THE EVIDENCE FOR IT
------------------------------------
This repository has already run this experiment once and it worked. B.1 moved
the *diagram* decision out of the model: a deterministic policy weighs the
moment, and the prompt then states what to do rather than asking the model to
weigh it again. `visual_policy` rose in all five domains measured, +0.53 to
+1.20, every one outside its noise floor.

The tutoring literature reports the same shape from the other side. The
ensemble-of-specialists architecture (ES-LLM) separates pedagogical
decision-making from language generation by routing through a rules-based
orchestrator, and reports **100% constraint adherence** against a monolithic
baseline. PATS makes it explicit: rules connecting learner state to specific
teaching actions.

What we never moved is the **teaching move itself**. The model still decides,
every turn, whether to probe, hint, show a worked example, correct an error,
advance, or simply tell — while also generating the language for it. The hint
ladder exists in `SOCRATIC_SYSTEM_RULES` as prose the model is asked to follow.
Prose-only enforcement measures 0/5 in this repository.

`adaptation` sits at 1.40-2.20 across seven domains and the judge's rubric
defines it as "did the tutor adjust to THIS student's demonstrated level and
behaviour, rather than following a script?". A model choosing its own move from
a transcript it re-reads every turn is exactly how you get a script.

WHAT THIS IS NOT
----------------
It does not choose the WORDS. It chooses the MOVE, from state the system
already computes for other purposes — the grade the grader returned, the retry
count the FSM already tracks, the open error `TurnState` already records. The
model writes the turn.

Deterministic on purpose: no model call, so this costs nothing and its decision
is inspectable in a log line — the same reasoning that made `aid_policy` and
`_detect_ignorance` rule-based.
"""

#: The moves. Deliberately small: a taxonomy nobody can hold in their head is
#: a taxonomy that gets applied inconsistently.
PROBE = "PROBE"
HINT = "HINT"
WORKED_EXAMPLE = "WORKED_EXAMPLE"
CORRECT = "CORRECT"
ADVANCE = "ADVANCE"
TELL = "TELL"

MOVES = (PROBE, HINT, WORKED_EXAMPLE, CORRECT, ADVANCE, TELL)

#: What each move instructs. Written in the second person and stating the ONE
#: thing to do, because a correction the model can act on is the entire point.
_INSTRUCTION = {
    PROBE: ("Ask a question that makes them reason one step further from what "
            "they just said. Do not explain first."),
    HINT: ("They are stuck. Give ONE sentence of conceptual hint — not the "
           "answer — then ask them to try again."),
    WORKED_EXAMPLE: ("They have been stuck twice. Stop asking. Work through a "
                     "parallel example yourself, briefly, then ask them one "
                     "question about the SAME step in a new case."),
    CORRECT: ("They got something specifically wrong and it is still "
              "unaddressed. Name the error plainly, say why it is wrong, then "
              "ask a question that exposes the correct reasoning."),
    ADVANCE: ("They have shown they understand. Do not re-check it. Raise the "
              "difficulty or move to the next idea."),
    TELL: ("This cannot be derived. State it plainly in your first sentence, "
           "then ask about something that CAN be reasoned about."),
}

#: Attempts on one question after which questioning has demonstrably failed.
#: Matches the hint ladder the system prompt already describes, so the code and
#: the prose agree rather than competing.
STUCK_ATTEMPTS = 2

#: Correct answers in a row that count as "they have this".
ADVANCE_STREAK = 2

#: Grade at or above which an answer counts as correct. Same threshold the FSM
#: uses for its streak and `TurnState` uses for "established", so one idea of
#: "correct" holds across the system.
PASS_GRADE = 3


class Move:
    """The chosen move and why, so a log line explains the turn."""

    __slots__ = ("move", "reason")

    def __init__(self, move, reason):
        self.move = move
        self.reason = reason

    @property
    def instruction(self):
        return _INSTRUCTION.get(self.move, "")

    def prompt_line(self):
        """The line appended to the tutor prompt."""
        if self.move not in _INSTRUCTION:
            return ""
        return f"THIS TURN — make this move: {self.move}. {self.instruction}"

    def __repr__(self):                                   # pragma: no cover
        return f"<Move {self.move}: {self.reason}>"

    def __eq__(self, other):
        return isinstance(other, Move) and self.move == other.move


def decide_move(last_grade=0, attempts=0, correct_streak=0,
                open_error=False, is_opening=False, is_arbitrary=False,
                already_told=False, behaviour=None):
    """Choose the teaching move for this turn. Never raises.

    Ordered by precedence, most specific first. The ordering IS the pedagogy:

      1. arbitrary content that has not been stated -> TELL. Nothing else can
         apply, because there is no reasoning path to probe along.
      2. stuck twice -> WORKED_EXAMPLE. Asking a third time is the single most
         frequent complaint the judge makes, and after two failures the
         outstanding error is usually WHY they are stuck.
      3. an unaddressed error, not yet stuck -> CORRECT. Four of fifteen
         measured failures were "the student erred and the tutor moved on";
         letting it stand is the worst available move.
      4. stuck once -> HINT.
      5. demonstrated understanding -> ADVANCE. Re-checking what they have shown
         is what "ignored the student's explicit request to move on" looks like.
      6. otherwise -> PROBE, the default Socratic move.
    """
    try:
        if is_arbitrary and not already_told:
            return Move(TELL, "arbitrary content, not yet stated")

        # --- BEHAVIOUR OUTRANKS THE COUNTER -----------------------------
        #
        # Without this the move is dominated by one signal — the miss count —
        # and a bluffer, a silent struggler, a confused beginner and a
        # misconception-holder all miss twice and all got WORKED_EXAMPLE.
        # Measured: five profiles producing prompts 96.9% identical, which is
        # the definition of the script `adaptation` punishes.
        #
        # Same miss count, different right move:
        if behaviour == "GIVING_UP":
            # They said they do not know. Do not wait for a second miss.
            return Move(WORKED_EXAMPLE, "they said they do not know")
        if behaviour == "AHEAD":
            return Move(ADVANCE, "they asked to move on and were right")
        if behaviour == "BLUFFING":
            # A worked example rewards the bluff — it hands over the substance
            # they were pretending to have. Make them show mechanism instead.
            return Move(CORRECT, "fluent but no mechanism; do not hand it over")
        if behaviour == "HEDGING" and attempts < STUCK_ATTEMPTS:
            # They are reasoning and close. Explaining now takes it away.
            return Move(PROBE, "hedging — close enough to reason it out")

        if attempts >= STUCK_ATTEMPTS:
            # Outranks CORRECT deliberately. After two failures the error is
            # usually WHY they are stuck, and naming it again while asking
            # again is the loop -- "repeats the same question in different
            # words" is the most frequent complaint the judge makes. Show them.
            return Move(WORKED_EXAMPLE,
                        f"stuck for {attempts} attempts on this question")
        if open_error:
            return Move(CORRECT, "an error of theirs is still unaddressed")
        if attempts == 1 and last_grade and last_grade < PASS_GRADE:
            return Move(HINT, "one failed attempt")
        if correct_streak >= ADVANCE_STREAK and not is_opening:
            return Move(ADVANCE, f"{correct_streak} correct in a row")
        return Move(PROBE, "default Socratic move")
    except Exception:                    # pragma: no cover - defensive
        return Move(PROBE, "move selection failed; defaulting to probe")


def from_turn_state(turn_state, last_grade=0, correct_streak=0,
                    is_opening=False, is_arbitrary=False, already_told=False,
                    behaviour=None):
    """Decide the move from an A.2 `TurnState`, which already tracks the
    attempt count and whether an error is outstanding.

    Tolerant of a missing or malformed state: a bookkeeping failure must cost
    the move selection, not the turn.
    """
    attempts = 0
    open_error = False
    try:
        # The larger of "tries at this exact question" and "failing answers in
        # a row". A rephrase resets the former, and rephrasing is precisely
        # what a tutor does to a stuck learner.
        attempts = max(int(getattr(turn_state, "attempts", 0) or 0),
                       int(getattr(turn_state, "misses", 0) or 0))
        # Only errors recent enough to still be worth addressing. Using the
        # whole list made A.6 return CORRECT on every turn after the first
        # mistake, forever.
        if hasattr(turn_state, "open_errors"):
            open_error = bool(turn_state.open_errors())
        else:
            open_error = bool(getattr(turn_state, "errors", None))
    except Exception:                    # pragma: no cover - defensive
        pass
    return decide_move(last_grade=last_grade, attempts=attempts,
                       correct_streak=correct_streak, open_error=open_error,
                       is_opening=is_opening, is_arbitrary=is_arbitrary,
                       already_told=already_told, behaviour=behaviour)
