"""Deterministic per-turn checks: injection in, drift out.

Both directions, no model in either, because a second model call per turn costs
~18-30 s at 30.1 tok/s and would roughly double a session's latency.

WHY DETERMINISTIC AND NOT A CLASSIFIER
--------------------------------------
The obvious candidates do not survive scrutiny. Prompt Guard and ProtectAI fit
the memory budget easily but are *injection* detectors that over-flag benign
text — ProtectAI-v2 showed a **42.5% false-positive rate** on prompts containing
ordinary words like "ignore" or "explosive", which a chemistry course says
constantly. A regex/rule pre-check is cheaper, more precise for the attack set
we actually face, and costs no RAM at all.

INJECTION: WHAT THIS IS AND IS NOT
----------------------------------
This is layer one and it is not the defence. Spotlighting — which
`prompts.sanitize_untrusted` already does — cuts *static* attacks from >50% to
<2%, and falls to **>95% ASR under adaptive attack**. What actually holds is
`session_state`: facts a learner cannot argue the tutor out of, because the
model never held them.

Grading is the harder case and it is measured: educational grading injection
reaches **ASR 0.73-0.82** with roughly 20-point grade inflation, and models that
resisted "almost never said so" — so the grader cannot be asked whether it was
fooled. An answer is untrusted text the model must both *evaluate* and *not
obey*, which is why the checks below run before grading, not after.

DRIFT: SIGNALS, NOT JUDGEMENTS
------------------------------
A system prompt weakens as a conversation lengthens — attention is partitioned
between early sinks and recent tokens, so the share reaching task-defining
tokens declines. Assume it is materially weaker by ~10-15 turns on a small
quantised model.

Every signal here is a regex or a counter, compared against **this session's own
baseline** rather than an absolute: a history tutor legitimately asserts more
than a maths tutor, so an absolute threshold would fire on subject, not drift.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Chat-template and role markers. An answer must never be able to open a turn.
_ROLE_MARKERS = re.compile(
    r"(<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>"
    r"|\[/?INST\]|<</?SYS>>|###\s*(?:System|Assistant|Human)\s*:"
    r"|^\s*(?:system|assistant)\s*:)",
    re.IGNORECASE | re.MULTILINE)

# Direct override attempts. High precision by design — each names an action on
# the instructions themselves, not a topic.
_OVERRIDE = re.compile(
    r"(ignore (?:all |any |the )?(?:previous|prior|above|earlier) "
    r"(?:instructions?|prompts?|rules?)"
    r"|disregard (?:the |all )?(?:above|previous|instructions?|rubric)"
    r"|forget (?:everything|all|your instructions)"
    r"|you are (?:now|actually) (?:a|an|no longer)"
    r"|new (?:instructions?|rules?|system prompt)"
    r"|reveal (?:your|the) (?:system )?prompt"
    r"|repeat (?:everything |all )?(?:above|your instructions))",
    re.IGNORECASE)

# Grade manipulation. Its own class because the grading path is where injection
# is measured most successful, and because these are worded as requests rather
# than as instruction overrides.
_GRADE_ATTACK = re.compile(
    r"(give (?:me )?(?:full|top|maximum|all the) (?:credit|marks|points)"
    r"|(?:mark|grade|score) (?:this|me|it) (?:as )?(?:correct|right|5|five|a\+)"
    r"|you (?:already |previously )?(?:marked|graded|passed) (?:this|me)"
    r"|(?:this|the) answer is (?:correct|right) *[.!]? *(?:grade|score)"
    r"|assign (?:full|maximum) (?:marks|credit|score))",
    re.IGNORECASE)

# A fake conversational turn appended to an answer — a specific known vector.
_PSEUDO_TURN = re.compile(
    r"(great work[!.]? *grade *[:=]|correct[!.]? *score *[:=]"
    r"|tutor *: *(?:excellent|correct|well done))",
    re.IGNORECASE)

_BASE64ISH = re.compile(r"[A-Za-z0-9+/]{120,}={0,2}")


def screen_answer(text, max_len=2000):
    """Screen a learner's answer before it reaches the grader.

    Returns {clean, flags, text}. **`text` is returned with control tokens
    removed and nothing else changed** — rewriting a learner's words would
    corrupt grading, which is the same reason `sanitize_untrusted` only
    truncates.

    A flag is not a refusal. The caller decides, and for work-avoidance the
    right response is a factual redirect rather than a lecture about prompt
    injection.
    """
    raw = text or ""
    flags = []
    if _ROLE_MARKERS.search(raw):
        flags.append("role_marker")
    if _OVERRIDE.search(raw):
        flags.append("instruction_override")
    if _GRADE_ATTACK.search(raw):
        flags.append("grade_manipulation")
    if _PSEUDO_TURN.search(raw):
        flags.append("pseudo_turn")
    if _BASE64ISH.search(raw):
        flags.append("encoded_blob")
    if len(raw) > max_len:
        flags.append("overlong")

    # Strip only what could break out of the fence or open a turn.
    cleaned = _ROLE_MARKERS.sub(" ", raw)[:max_len]
    return {"clean": not flags, "flags": flags, "text": cleaned}


def redirect_for(flags, state=None):
    """The non-moralising reply to a flagged answer, or ''.

    A fourteen-year-old trying to get out of work is the common case, not an
    attacker. Refusing theatrically or explaining prompt injection loses them;
    the ledger quietly holds the line and the session continues.
    """
    if not flags:
        return ""
    if "grade_manipulation" in flags:
        if state is not None and not state.has_been_graded_correct():
            return ("We haven't finished this one yet — let's keep going with "
                    "the next question.")
        return "Let's keep working through this one."
    if "instruction_override" in flags or "role_marker" in flags:
        return "Let's stay with the question — what do you think the answer is?"
    return ""


# --- drift ------------------------------------------------------------------

_QUESTION = re.compile(r"\?")
# Any sentence boundary, not just a full stop. The clearest drift case is a
# turn that asks and then answers — "What happens next? The answer is..." — and
# anchoring on ". " alone missed exactly that, because the assertion follows a
# QUESTION MARK.
_ASSERTION = re.compile(
    r"(?:^|[.?!]\s+)(?:the answer is|it is|this is|that is|the reason is"
    r"|which means|so the)\b",
    re.IGNORECASE)


def turn_signals(text, mode):
    """Cheap per-turn measurements of a tutor turn."""
    t = text or ""
    words = len(t.split())
    return {
        "words": words,
        "has_question": bool(_QUESTION.search(t)),
        "assertions": len(_ASSERTION.findall(t)),
        "mode": mode,
        # A turn containing both a question and its answer is the tutor
        # answering itself, which is the clearest drift tell there is.
        "answers_own_question": bool(_QUESTION.search(t)) and
                                bool(_ASSERTION.search(t)),
    }


class DriftMonitor:
    """Per-session baseline, and deviation from it.

    Against this session's own history, never an absolute: a history tutor
    legitimately asserts more than a maths tutor, so an absolute threshold would
    fire on subject rather than on drift.
    """

    def __init__(self, window=8):
        self.window = window
        self.turns = []

    def observe(self, text, mode, mode_rule_fired=True):
        s = turn_signals(text, mode)
        s["mode_rule_fired"] = mode_rule_fired
        self.turns.append(s)
        return self.check()

    def check(self):
        """Drift flags for the most recent turn. Empty means healthy."""
        if not self.turns:
            return []
        cur = self.turns[-1]
        flags = []

        # A tutor in QUESTION mode that stops asking has drifted. This is the
        # single most reliable signal and it is a boolean.
        if cur["mode"] == "QUESTION" and not cur["has_question"]:
            flags.append("no_question_in_question_mode")

        if cur["answers_own_question"]:
            flags.append("answers_own_question")

        # Mode is rule-driven, so a LECTURE with no rule behind it is a hard
        # flag rather than a heuristic one.
        if cur["mode"] == "LECTURE" and not cur["mode_rule_fired"]:
            flags.append("untriggered_mode_switch")

        # Verbosity growth: models that get lost produce bloated answers.
        prior = self.turns[-(self.window + 1):-1]
        if len(prior) >= 3:
            base = sum(p["words"] for p in prior) / len(prior)
            if base > 0 and cur["words"] > base * 2.0:
                flags.append("verbosity_spike")
            base_assert = sum(p["assertions"] for p in prior) / len(prior)
            if cur["mode"] == "QUESTION" and cur["assertions"] > max(2, base_assert * 3):
                flags.append("assertion_spike")
        return flags

    def summary(self):
        if not self.turns:
            return {"turns": 0}
        return {
            "turns": len(self.turns),
            "mean_words": round(sum(t["words"] for t in self.turns) / len(self.turns), 1),
            "question_rate": round(
                sum(1 for t in self.turns if t["has_question"]) / len(self.turns), 2),
        }
