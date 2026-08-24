"""A.2 — what this student has actually demonstrated, as structured state.

THE PROBLEM THIS EXISTS FOR
---------------------------
`adaptation` is the weakest dimension in the benchmark: 1.33-2.80 across seven
domains, worse than `socratic` itself. The judge's rubric defines it as "did
the tutor adjust to THIS student's demonstrated level and behaviour, rather
than following a script?", and the tutor scores near the floor.

The cause is structural rather than a prompt-wording problem. Every turn, the
model is handed a transcript and asked to re-derive, from prose, what the
student has got right, what they got wrong, and what is still open — while
also teaching. It re-reads and re-infers state on every single turn, and it
infers it badly.

The grader ALREADY produces this information as data. `_parse_grade_response`
returns a grade, the concepts the answer missed, and a reason, for every
answer. None of it has ever reached the tutor prompt; it is used for
scheduling and mastery gates and then discarded. This module carries it
forward.

WHY THIS IS DIFFERENT FROM THE TRANSCRIPT
-----------------------------------------
The transcript says what was SAID. This says what was ESTABLISHED. Those are
not the same, and the difference is the whole point: a student can say a great
deal without demonstrating anything, and a tutor that cannot tell the
difference re-teaches what is already known and moves past what is not.

WHAT IT REFUSES TO RECORD
-------------------------
A grade with `graded=False` is the fail-safe default the parser emits when the
LLM call failed — grade 2, so it can never silently credit mastery. That is
data about the infrastructure, not about the learner, and `fsm_logic` already
flags it as indistinguishable downstream from an earned 2. It must not become
"the student partly understood this": during an outage the tutor would invent
a whole history of half-understanding that never happened. Only real
assessments are recorded.

Same discipline as learner_history: with nothing established, this renders
NOTHING rather than an empty scaffold. An invented state is worse than none.
"""

#: A grade at or above this counts as demonstrated. The FSM uses the same
#: threshold for its correct-streak, so "established" here means what it means
#: everywhere else in the system rather than being a second private opinion.
PASS_GRADE = 3

#: Hard caps. This rides in EVERY tutor turn, so it is bounded by design
#: rather than by hoping a dialogue stays short.
MAX_ESTABLISHED = 4
MAX_UNRESOLVED = 3
MAX_QUOTE_CHARS = 90
MAX_CHARS = 600


def _clip(text, limit=MAX_QUOTE_CHARS):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class TurnState:
    """Rolling record of what the learner has demonstrated on this concept.

    Built by the FSM as answers are graded. Rendered into the tutor prompt so
    the model is TOLD the state instead of re-deriving it from prose.
    """

    __slots__ = ("established", "errors", "unresolved", "attempts",
                 "current_question", "_answers_seen", "misses")

    def __init__(self):
        self.established = []        # [(question, quote)] answered well
        self.errors = []             # [{"what", "quote", "reason"}] still open
        self.unresolved = []         # concepts the answers keep missing
        self.attempts = 0            # attempts on the CURRENT question
        self.current_question = ""
        self._answers_seen = 0       # monotonic counter, for ageing errors
        # Consecutive failing answers, NOT reset by a rephrased question.
        # `attempts` counts tries at one question STRING, so a tutor that
        # rewords ("let me put it another way") resets it — and rewording is
        # exactly what a tutor does when the student is stuck. That made
        # "stuck twice" almost unreachable, so the move that stops the loop
        # (WORKED_EXAMPLE) never fired for the learner who most needed it.
        self.misses = 0

    # ------------------------------------------------------------- recording
    def ask(self, question):
        """The tutor put a new question. Resets the per-question attempt count."""
        q = " ".join((question or "").split())
        if q and q != self.current_question:
            self.current_question = q
            self.attempts = 0

    def record(self, answer, grade_result):
        """Fold one graded answer into the state.

        `grade_result` is what `_parse_grade_response` returns. A result that
        is not a real assessment (`graded` false) is IGNORED — see the module
        docstring. Never raises: a bookkeeping failure must not cost a turn.
        """
        try:
            if not isinstance(grade_result, dict):
                return
            if grade_result.get("graded") is False:
                return               # infrastructure noise, not evidence
            # A result with no usable grade is not an assessment. Without this,
            # `{}` fell through as grade 0 and was recorded as a WRONG answer —
            # inventing an error the student never made, which is precisely
            # what this module exists to stop the tutor doing.
            try:
                grade = int(grade_result["grade"])
            except (KeyError, TypeError, ValueError):
                return
            self._answers_seen += 1
            if not 1 <= grade <= 5:
                return
            quote = _clip(answer)
            self.attempts += 1

            if grade >= PASS_GRADE:
                self.misses = 0
                if self.current_question:
                    self.established.append((self.current_question, quote))
                    del self.established[:-MAX_ESTABLISHED]
                # Getting it right resolves the open error on this question.
                self.errors = [e for e in self.errors
                               if e.get("what") != self.current_question]
            else:
                self.misses += 1
                reason = _clip(grade_result.get("reason")
                               or grade_result.get("feedback") or "", 120)
                self.errors = [e for e in self.errors
                               if e.get("what") != self.current_question]
                self.errors.append({"what": self.current_question,
                                    "quote": quote, "reason": reason,
                                    "at": self._answers_seen})
                del self.errors[:-MAX_UNRESOLVED]

            missing = grade_result.get("missing_concepts") or []
            for m in missing:
                m = _clip(str(m), 40)
                if m and m not in self.unresolved:
                    self.unresolved.append(m)
            del self.unresolved[:-MAX_UNRESOLVED]
        except Exception:            # pragma: no cover - defensive
            return

    # -------------------------------------------------------------- rendering
    #: How many graded answers an error stays "open" for. An error the student
    #: has since moved past should not keep forcing a correction: A.6 selected
    #: CORRECT on every subsequent turn of a simulated dialogue because the
    #: error list never aged, which would have had the tutor re-correcting a
    #: mistake the learner had already fixed — worse than the behaviour it
    #: replaces.
    #:
    #: 1 = "the error in the answer they just gave". The measured failure is
    #: "the student erred and the tutor moved on", which means the correction
    #: belongs on the VERY NEXT turn. A window of 2 kept selecting CORRECT
    #: while the learner was answering correctly again — which is the opposite
    #: failure, and the one the judge calls "failing to adapt".
    OPEN_ERROR_WINDOW = 1

    def open_errors(self, within=None):
        """Errors recent enough to still be worth addressing."""
        window = self.OPEN_ERROR_WINDOW if within is None else within
        try:
            return [e for e in self.errors
                    if self._answers_seen - int(e.get("at", 0)) < window]
        except Exception:            # pragma: no cover - defensive
            return list(self.errors)

    def is_empty(self):
        # `misses` counts: a learner who has failed twice running is exactly
        # the case this block exists to act on, even if nothing else has been
        # recorded yet.
        return not (self.established or self.errors or self.unresolved
                    or self.misses >= 2)

    def render(self):
        """The prompt block, or "" when nothing has been established yet.

        Deliberately terse and factual. It states what happened and what to do
        with it; it does not editorialise about the learner.
        """
        if self.is_empty():
            return ""

        # THE ACTIONABLE INSTRUCTION GOES FIRST, AND IS NEVER TRUNCATED.
        #
        # This block ends `[:MAX_CHARS]`, MAX_CHARS is 600, and the
        # change-your-approach line used to be appended LAST — after the
        # established list, the errors and the unresolved topics. Measured
        # across 60 benchmark dialogues: 26 were eligible for it, and in 25 of
        # those the line was generated and then CUT OFF by the character cap.
        # It reached exactly one prompt.
        #
        # Everything else here is DESCRIPTION — what the learner has shown.
        # This one line is an INSTRUCTION, and it is the only part that tells
        # the tutor to do something different. Losing the instruction and
        # keeping the description is precisely the wrong way round.
        head = []
        if self.misses >= 2:
            # "SHOW THEM" ROUTES THROUGH THE FIGURE, NOT AROUND IT.
            #
            # KEPT ON PRINCIPLE, NOT ON EVIDENCE — and the distinction is
            # recorded because the evidence was claimed and then withdrawn.
            #
            # The line originally read "show them the answer worked through",
            # full stop. One run showed dialogues where the aid policy asked
            # for a figure and none appeared jumping from 20-33% to 47%, with
            # `visual_policy` down 0.63, past its floor. That was diagnosed as
            # the instruction suppressing diagrams.
            #
            # THE NEXT RUN, ON THE IDENTICAL PRE-FIX CONFIG, PRODUCED 7% —
            # the best figure rate of any run — and visual_policy 4.27, the
            # highest. The regression did not exist; it was one draw at n=15.
            # Note that the 47% was a DIRECT COUNT, not a judged score, and
            # still swung that far between identical configurations.
            #
            # The wording is kept because it is right on the merits: a worked
            # answer IS a better figure than a paragraph, and an instruction
            # that can be read as "explain in prose instead" is worth removing
            # whether or not it has yet done harm.
            head.append(f"  They have now failed this point {self.misses} "
                        f"times in a row. CHANGE YOUR APPROACH — asking it "
                        f"again in different words has already failed twice. "
                        f"Drop the difficulty sharply: give them a concrete "
                        f"choice between two options, or a much smaller "
                        f"question they can answer in a few words. Do NOT "
                        f"simply hand over the full answer — that is the "
                        f"other way to fail this. If a figure was requested "
                        f"this turn, use it to carry the easier question.")

        lines = ["WHAT THIS STUDENT HAS DEMONSTRATED "
                 "(from graded answers — this is fact, not your impression):"]
        lines.extend(head)

        if self.established:
            got = "; ".join(f'{q} — they answered "{a}"'
                            for q, a in self.established[-MAX_ESTABLISHED:])
            lines.append(f"  ALREADY ESTABLISHED: {got}")
            lines.append("  Do not re-teach or re-ask these. Build on them.")

        if self.errors:
            for e in self.errors[-MAX_UNRESOLVED:]:
                bit = f'  STILL WRONG: {e["what"]} — they said "{e["quote"]}"'
                if e.get("reason"):
                    bit += f' ({e["reason"]})'
                lines.append(bit)
            lines.append("  Address the error above before moving on.")

        if self.unresolved:
            lines.append("  NOT YET COVERED: " + ", ".join(self.unresolved))

        return "\n".join(lines)[:MAX_CHARS]

    def __repr__(self):              # pragma: no cover
        return (f"<TurnState established={len(self.established)} "
                f"errors={len(self.errors)} attempts={self.attempts}>")


# --- persistence -------------------------------------------------------------
#
# WHY THIS EXISTS
# `TurnState` is reset per concept and was never saved, so pausing mid-struggle
# and coming back lost `misses` — the counter that fires "they have now failed
# this point N times in a row, CHANGE YOUR APPROACH". A learner who gave up on
# a hard point, closed the tab, and returned was greeted as though it were
# their first attempt, by a tutor that had forgotten the whole difficulty.
#
# That is the one piece of session memory whose loss a learner would actually
# notice, because it is the piece that was about THEM.

def to_dict(ts):
    """Serialise a TurnState. Returns {} for None, so callers need no guard."""
    if ts is None:
        return {}
    return {
        "established": list(getattr(ts, "established", []) or []),
        "errors": list(getattr(ts, "errors", []) or []),
        "unresolved": list(getattr(ts, "unresolved", []) or []),
        "attempts": int(getattr(ts, "attempts", 0) or 0),
        "current_question": getattr(ts, "current_question", "") or "",
        "answers_seen": int(getattr(ts, "_answers_seen", 0) or 0),
        "misses": int(getattr(ts, "misses", 0) or 0),
    }


def from_dict(data):
    """Rebuild a TurnState. Never raises — a corrupt blob costs the memory of
    one concept's struggle, and must not cost the session."""
    ts = TurnState()
    if not isinstance(data, dict):
        return ts
    try:
        # Tuples survive a JSON round trip as lists; `established` is read as
        # pairs, so restore the shape rather than the type it happened to have.
        ts.established = [tuple(x) if isinstance(x, (list, tuple)) else x
                          for x in (data.get("established") or [])]
        ts.errors = list(data.get("errors") or [])
        ts.unresolved = list(data.get("unresolved") or [])
        ts.attempts = int(data.get("attempts") or 0)
        ts.current_question = data.get("current_question") or ""
        ts._answers_seen = int(data.get("answers_seen") or 0)
        ts.misses = int(data.get("misses") or 0)
    except Exception:
        return TurnState()
    return ts
