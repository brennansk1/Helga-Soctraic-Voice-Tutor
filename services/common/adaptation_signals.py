"""Mechanical script-tells in a tutoring transcript. DIAGNOSTICS, not a score.

WHAT THIS IS NOT
----------------
It was written to be a deterministic replacement for the judged `adaptation`
dimension, on the theory that a computed measure would be more stable. IT WAS
NOT, and the attempt is recorded here because the reasoning was wrong in a way
worth keeping.

Measured across the same four runs:

    deterministic composite, run-to-run spread   0.48
    judged `adaptation`, run-to-run spread       0.40

Worse, it ANTI-CORRELATED with the judge: `fast_learner` scored 5.00 computed
against 1.75 judged, `confident_bluffer` 4.72 against 1.50. The profiles the
judge rates worst, this rated best — because it detects only crude script-tells,
which the tutor mostly avoids while still failing to adapt.

THE ERROR IN THE REASONING.
`notation_speakable` has a floor of 0.00, and that was taken as evidence that
computed dimensions are stable. It is not: that dimension is SATURATED — the
tutor now always writes speakable LaTeX, so there is nothing left to vary.
Determinism of the scorer buys nothing when the INPUT is stochastic, and the
input here is very stochastic indeed: across four runs, both the tutor's turns
and the simulated student's turns repeat at a mean text similarity of **0.062**.
Every run is a different conversation.

So the composite `score()` is gone. What remains are the two signals that found
a real defect, kept as diagnostics for reading a transcript — never as a number
to gate on.

WHAT THEY FOUND
---------------
  responded_to_stuck   the learner said they do not know, and the tutor's very
                       next turn stopped interrogating and explained. This is
                       the one that earned its place: on the silent_struggler
                       profile the student said "idk" in three of four turns
                       and every reply had the same shape — affirm, correct,
                       ask again — which is how the ordering defect in
                       `prompts.py` was found.

  repeated_openings    consecutive tutor turns beginning the same way. The same
                       transcript opened two turns running with "You correctly
                       identified that..." / "You correctly noted that...".

WHAT THE JUDGE ACTUALLY SEES, AND WHY IT IS NOT HERE
----------------------------------------------------
The judge's own `worst_moment` rationales separate cleanly. Words appearing in
the 34 dialogues scoring 1 and NEVER in the 11 scoring 4+:

    repeatedly 8   instead 8   despite 7   same 7   loop 6
    ignores/ignoring 9   adapt 5   failing 4

    "The tutor repeated the same question about walking West after the student
     already answered 'idk'"
    "The tutor ignores the student's repeated 'idk' and 'not sure' responses,
     continuing to lecture and ask complex conceptual questions"

So the failure is REPEATING and IGNORING. That sounds mechanical, and nine
surface features were tried against it: turn length, length variance, questions
per turn, echoing the learner's content words, repeated openings, handling a
stuck learner, quoting the learner, and lexical repetition of whole questions.

NONE discriminates. Lexical question-repetition is 0.076 on dialogues scoring 1
and 0.056 on those scoring 4+ — 21% versus 18% have any repeat at all.

The repetition the judge names is SEMANTIC: asking the same thing in different
words. Detecting that needs a model, which is what the judge already is. This
module cannot substitute for it and does not try to.

Use them to READ a transcript, not to score one. A tutor can pass both while
teaching badly, and — as the four-run comparison above shows — can fail them
while the judge is satisfied.
"""
import difflib
import logging
import re

logger = logging.getLogger(__name__)

#: The learner saying, in the ways learners actually say it, that they are lost.
_STUCK = re.compile(
    r"\b(i (do not|don'?t) know|idk|no idea|i'?m lost|not sure what|"
    r"i give up|no clue|can'?t tell)\b", re.I)

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")

#: How much of an opening has to match before two turns count as the same move.
#:
#: FOUR words, not six. The formula being detected is short — "You correctly
#: identified that..." versus "You correctly noted that..." — and a six-word
#: window pulls in the CONTENT that follows it, which of course differs. That
#: diluted the real pair to 0.659 and let it through a 0.75 threshold, missing
#: precisely the repetition this exists to catch.
OPENING_WORDS = 4
OPENING_SIMILARITY = 0.72

#: A reply that stops interrogating: at most one question, and enough words to
#: have actually explained something rather than just asked more softly.
MAX_QUESTIONS_WHEN_STUCK = 1
MIN_WORDS_WHEN_STUCK = 20


def _turns(transcript, role):
    out = []
    for t in (transcript or []):
        if not isinstance(t, dict):
            continue
        who = t.get("role") or t.get("sender")
        if who in (role, {"tutor": "helga", "student": "user"}.get(role)):
            out.append((t.get("text") or "").strip())
    return out


def _opening(text):
    return " ".join(_WORD.findall(text or "")[:OPENING_WORDS]).lower()


def responded_to_stuck(transcript):
    """(handled, total) — times the learner was lost and the tutor changed tack.

    Only the tutor turn IMMEDIATELY AFTER the admission counts. Explaining two
    turns later is not responding to it.
    """
    handled = total = 0
    turns = transcript or []
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            continue
        who = turn.get("role") or turn.get("sender")
        if who not in ("student", "user"):
            continue
        if not _STUCK.search(turn.get("text") or ""):
            continue
        reply = next((t for t in turns[i + 1:]
                      if isinstance(t, dict)
                      and (t.get("role") or t.get("sender")) in ("tutor", "helga")),
                     None)
        if reply is None:
            continue
        total += 1
        text = reply.get("text") or ""
        if (text.count("?") <= MAX_QUESTIONS_WHEN_STUCK
                and len(_WORD.findall(text)) >= MIN_WORDS_WHEN_STUCK):
            handled += 1
    return handled, total


def repeated_openings(transcript):
    """(repeats, comparisons) — consecutive tutor turns that start alike.

    Two turns beginning "You correctly identified that..." and "You correctly
    noted that..." are the same move wearing different words, and that is what
    a script is.
    """
    tutor = [t for t in _turns(transcript, "tutor") if t]
    repeats = comparisons = 0
    for a, b in zip(tutor, tutor[1:]):
        oa, ob = _opening(a), _opening(b)
        if not oa or not ob:
            continue
        comparisons += 1
        if difflib.SequenceMatcher(None, oa, ob).ratio() >= OPENING_SIMILARITY:
            repeats += 1
    return repeats, comparisons
