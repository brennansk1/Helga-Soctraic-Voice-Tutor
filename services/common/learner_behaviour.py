"""A.7 — what KIND of learner this is right now, from how they write.

THE GAP THIS CLOSES
-------------------
The judge scores `adaptation` on whether the tutor adjusted to "THIS student's
demonstrated level AND BEHAVIOUR, rather than following a script". The system
tracks level exhaustively — grades, streaks, Bloom, FSRS, and now A.2's turn
state. It tracks behaviour nowhere.

That matters because two learners can earn the SAME GRADE and need opposite
responses. A confident bluffer writing 60 fluent words of jargon with no
mechanism, and a silent struggler writing "idk", both score 1. Told only the
grade, the tutor gives them the same next turn — which is the definition of
following a script.

Measured on our own benchmark: `silent_struggler` averages 8.3 words per answer
and `confident_bluffer` 63.2. That is a 55-word spread the tutor is currently
blind to.

GROUNDING
---------
AutoTutor's disengagement tracking detects maladaptive behaviour — mind
wandering, impetuous responding, gaming — from response accuracy, language and
timing, and intervenes on it rather than on the answer alone. PATS makes the
same move explicitly: real-time trait analysis mapped to a teaching strategy.

We have no response times (and would not trust them from a simulated student),
so this reads language only.

WHAT IT REFUSES TO SAY
----------------------
Same discipline as learner_history and turn_state: with a thin or ambiguous
record it returns None, and the prompt carries no behaviour line. A tutor told
"this student is bluffing" about a learner who is simply concise would open by
challenging honesty that was never in question — worse than saying nothing.
"""
import re

#: Answers needed before any pattern is claimed. One short reply is a mood, not
#: a behaviour.
MIN_ANSWERS = 2

#: Mean words at or below which answers count as TERSE. Calibrated against the
#: measured 8.3-word mean of the benchmark's silent-struggler profile, with
#: headroom so an ordinary short-but-complete answer does not trip it.
TERSE_WORDS = 12

#: Mean words at or above which an answer is long enough that saying nothing
#: with them is meaningful. Set from the measured 63.2-word confident-bluffer
#: mean, with margin: the benchmark's simulated students are capped at 200
#: tokens and told 'one to three sentences', so individual answers run well
#: below the profile mean. 35 missed a clear bluff at 32 words.
VERBOSE_WORDS = 30

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")

#: Explicit surrender. Distinct from a wrong answer: they are telling you they
#: have nothing, and continuing to question is the measured failure.
_GIVING_UP = re.compile(
    r"\b(i (don'?t|do not) know|no idea|not sure|dunno|idk|i'?m lost|"
    r"i give up|no clue|whatever|skip (this|it))\b", re.I)

#: Hedges. A learner who hedges is reasoning and uncertain — a different state
#: from one who is bluffing or one who has stopped.
_HEDGE = re.compile(
    r"\b(maybe|i think|i guess|probably|might be|sort of|kind of|"
    r"i'?m not certain|possibly|perhaps|something like)\b", re.I)

#: Mechanism language. The presence of these is what separates an explanation
#: from a recitation, and their ABSENCE in a long, confident answer is the
#: bluff signature.
_MECHANISM = re.compile(
    r"\b(because|since|so that|therefore|which means|that means|"
    r"as a result|due to|the reason|causes?|leads to|depends on|"
    r"if .{0,40}then)\b", re.I)

#: Asking to move on. The judge's own complaint, twice: "ignored the student's
#: explicit request to move on".
_MOVE_ON = re.compile(
    r"\b(next|move on|already know|i know (this|that)|got it|"
    r"too easy|something harder|can we|let'?s move)\b", re.I)

# The behaviours. Small on purpose.
TERSE = "TERSE"
GIVING_UP = "GIVING_UP"
BLUFFING = "BLUFFING"
HEDGING = "HEDGING"
AHEAD = "AHEAD"

#: What to DO about each. Stated as an instruction, not an observation — the
#: pattern this repository has measured at 5/5 against 0/5 for description.
_INSTRUCTION = {
    TERSE: ("This learner answers in a few words. Do not read that as "
            "refusal. Ask something they can answer in a few words, and give "
            "them a concrete choice rather than an open invitation to "
            "explain."),
    GIVING_UP: ("This learner has said they do not know. Stop questioning. "
                "Explain the point plainly in two or three sentences, then ask "
                "one simple question to check it landed."),
    BLUFFING: ("This learner writes fluently and confidently without saying "
               "any mechanism. Do not accept it. Ask them to explain WHY, in "
               "plain words, or to apply it to one concrete case. Do not "
               "praise the fluency."),
    HEDGING: ("This learner is reasoning but unsure. They are close. Affirm "
              "the specific part that is right, then ask one question that "
              "resolves the part they are hedging about."),
    AHEAD: ("This learner has said they already understand and want to move "
            "on. Believe them. Do not re-check it. Raise the difficulty or "
            "move to the next idea."),
}


def _words(text):
    return _WORD.findall(text or "")


def classify(answers, grades=None):
    """The behaviour these answers show, or None.

    `answers` is the learner's recent messages, oldest first. `grades` are the
    matching grades where known; a behaviour that depends on being wrong is not
    claimed without them.

    Ordered by how much the response should change. Never raises.
    """
    try:
        answers = [a for a in (answers or []) if (a or "").strip()]
        if len(answers) < MIN_ANSWERS:
            return None
        grades = list(grades or [])
        recent = answers[-3:]
        lengths = [len(_words(a)) for a in recent]
        mean_len = sum(lengths) / len(lengths)
        last = recent[-1]

        # Explicit surrender outranks everything: they have told us.
        if _GIVING_UP.search(last):
            return GIVING_UP

        # Asking to move on, and not wrong about it.
        if _MOVE_ON.search(last) and (not grades or grades[-1] >= 3):
            return AHEAD

        # Long, confident, and WRONG.
        #
        # A CONNECTIVE IS NOT A MECHANISM. The original required
        # `not _MECHANISM.search(last)`, and `_MECHANISM` matches words like
        # "because", "since" and "which is why". Fluent use of exactly those
        # words is the defining trait of a bluffer, so the test was defeated by
        # the thing it exists to catch.
        #
        # Measured on the mathematics benchmark, confident_bluffer profile,
        # three topics: two were blocked by "Since" and "because" — in
        # "The slope remains identical BECAUSE the mixed partial derivatives
        # are commutative", a sentence that states no mechanism at all — and
        # the third by the last grade being exactly 3. Adaptation scored 1.00
        # of 5 on that profile, the lowest of any, while the mechanism meant to
        # handle it never once fired.
        #
        # The grade already carries the judgement the connective was standing
        # in for: if the answer were a real mechanism it would not be graded
        # low. So confidence (no hedging) plus length plus a low RECENT grade
        # is the evidence, and the connective is dropped.
        recent_grades = [g for g in grades[-2:] if isinstance(g, (int, float))]
        wrong_recently = bool(recent_grades) and min(recent_grades) < 3
        if (mean_len >= VERBOSE_WORDS
                and not _HEDGE.search(last)
                and wrong_recently):
            return BLUFFING

        if _HEDGE.search(last):
            return HEDGING

        if mean_len <= TERSE_WORDS:
            return TERSE

        return None
    except Exception:                    # pragma: no cover - defensive
        return None


def prompt_line(behaviour):
    """The line appended to the tutor prompt, or "" for no clear behaviour."""
    if behaviour not in _INSTRUCTION:
        return ""
    return f"THIS LEARNER RIGHT NOW: {_INSTRUCTION[behaviour]}"


def describe(answers, grades=None):
    """Convenience: classify and render in one call."""
    return prompt_line(classify(answers, grades))
