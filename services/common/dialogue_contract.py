"""A4.1a — the dialogue contract: enforceable structure for a tutor turn.

WHY THIS IS CODE AND NOT PROMPT TEXT
------------------------------------
The depth contract worked for generated content because it was enforceable
structure with a NAMED violation to regenerate against, not prose in a system
prompt. This repository has measured the difference repeatedly: prompt-only
enforcement lands 0/5, while a correction round that names the specific
offender lands 5/5. Telling a model "be concise and Socratic" is a wish.
Telling it "your last turn was 143 words; the limit is 60" is a fact it can
act on.

The judge scores `socratic` at 2.10/5 and flags the same two failures every
time: lecturing instead of questioning, and answering something other than
what the student actually said. Those are exactly the rules below.

THE FOUR RULES
--------------
  length        <= 60 words. A long tutor turn IS a lecture, whatever it
                contains. This is the rule that most directly moves `socratic`.
  question      must end with a question. (Already enforced in fsm_logic; kept
                here so the contract is checkable in one place.)
  reference     must quote or reference something the learner actually said.
                Waived on the opening turn, when there is nothing to reference.
  grounded_claim  if it claims the student said or did something, that claim
                must match what they actually wrote. See A.1 below.
  one_new_idea  must not introduce more than one new technical term.

COST
----
Checking is free. Regeneration costs ONE extra call and happens ONLY when a
rule trips, so the common case is unchanged. An interactive turn is ~4.5s, so
a second call still lands inside the budget; that measurement is what makes
this affordable at all.

HONESTY ABOUT `one_new_idea`
----------------------------
Three of these rules are exact. `one_new_idea` is a PROXY: it counts technical
terms that appear in the concept material but have not yet appeared in the
dialogue. Two or more in one turn is treated as more than one new idea. That
is not the same as counting ideas, and it is documented as an approximation
rather than dressed up as a measurement. It is deliberately generous -- it
fires on clear cases and stays quiet on ambiguous ones, because a false
violation costs a regeneration and teaches nothing.
"""
import re

MAX_WORDS = 60
MAX_NEW_TERMS = 1

#: Words that carry no content, so overlap with them is not evidence the tutor
#: engaged with what the learner said. Kept small and obvious on purpose.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those is are was were be been
being am do does did doing have has had having i you he she it we they me him
her us them my your his its our their to of in on at by for with about into
over after before between out against during without within along across
so no not yes ok okay just really very too also more most some any each
what which who whom whose when where why how can could may might must shall
should will would there here as from up down out off again once
think know mean guess maybe sure right wrong like well um uh
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")


class Violation:
    """One broken rule, with the sentence that will be handed back to the model.

    `instruction` is written in the second person and names the measurement,
    because a correction the model can act on is the entire point.
    """

    __slots__ = ("rule", "detail", "instruction")

    def __init__(self, rule, detail, instruction):
        self.rule = rule
        self.detail = detail
        self.instruction = instruction

    def __repr__(self):                                   # pragma: no cover
        return f"<Violation {self.rule}: {self.detail}>"

    def __eq__(self, other):
        return (isinstance(other, Violation) and self.rule == other.rule
                and self.detail == other.detail)


def _content_words(text):
    return {w.lower() for w in _WORD.findall(text or "")
            if w.lower() not in _STOPWORDS and len(w) > 2}


def word_count(text):
    """Words a reader would count. The aid fence is not prose and is excluded.

    A diagram is not a lecture, so counting its JSON toward the word cap would
    punish the tutor for drawing -- the opposite of what the product wants.
    """
    stripped = re.sub(r"```aid\s*.+?```", " ", text or "", flags=re.S)
    return len(_WORD.findall(stripped))


def ends_with_question(text):
    stripped = re.sub(r"```aid\s*.+?```", " ", text or "", flags=re.S)
    return stripped.rstrip().endswith("?")


def references_learner(turn, learner_said):
    """Did the turn engage with what the learner actually wrote?

    Word-boundary overlap on content words. NOT substring matching: this
    codebase has been bitten three times by `"war" in "aware"`, and a contract
    that fires on a coincidence is worse than no contract.
    """
    learner_words = _content_words(learner_said)
    if not learner_words:
        return True                    # nothing to reference; not a violation
    return bool(learner_words & _content_words(turn))


def new_terms(turn, concept_terms, already_seen):
    """Technical terms introduced in this turn that the dialogue has not used.

    `concept_terms` scopes it to the subject's own vocabulary, so ordinary
    English the tutor happens to use for the first time is not counted.
    """
    turn_words = _content_words(turn)
    seen = {t.lower() for t in (already_seen or set())}
    terms = {t.lower() for t in (concept_terms or set())}
    return sorted((turn_words & terms) - seen)


# --- A.1: a claim about the student must be grounded in what they said -------
#
# The measured failure, from the judge's own words on the maths run:
#
#   "apologizing for confusion that never existed"
#   "incorrectly claims the student made a calculation error, despite the
#    student having already correctly identified the partial derivative"
#   "the student falsely claimed it was not provided, and the tutor failed to
#    correct this hallucination"
#
# Three in fifteen dialogues. Conversation history reaches the model correctly
# paired and untruncated -- this was checked -- so the tutor HAS the transcript
# and misremembers it.
#
# The existing `reference` rule cannot catch this: it asks whether the turn
# overlaps the learner's words at all, so "You said the derivative is
# negative" passes on the word "derivative" even when the learner said the
# opposite. This rule asks the harder question: when the turn ATTRIBUTES
# something to the student, is that attribution supported?
#
# PAST-TENSE ATTRIBUTION ONLY. "What do you think?" and "you might consider"
# are not claims about what happened and must not trip it.
_ATTRIBUTION = re.compile(
    r"\b(you (said|wrote|mentioned|claimed|stated|told me|suggested|answered|"
    r"guessed|called it|described)"
    r"|you'?re (confusing|mixing|assuming|thinking of|saying)"
    r"|you (were|got it|got that|had) (right|wrong|confused|mixed up)"
    r"|your (answer|error|mistake|reasoning|point|example|definition)"
    r"|as you (said|noted|pointed out|mentioned)"
    r"|earlier you|a moment ago you|you just said"
    r"|(sorry|apologies) for the confusion"
    r"|you seem to (think|believe))\b", re.I)

#: How much of the learner's own vocabulary an attribution has to reuse before
#: we believe it. Two content words is a low bar on purpose: this rule is
#: aimed at inventions, not paraphrases, and a false violation costs a
#: regeneration that teaches nothing.
MIN_GROUNDING_WORDS = 2


def attributes_to_learner(turn):
    """Does this turn claim the student said or did something?"""
    return bool(_ATTRIBUTION.search(turn or ""))


def attribution_is_grounded(turn, learner_said, recent_learner=None):
    """Is that claim supported by what the learner actually wrote?

    Grounded when the turn reuses at least MIN_GROUNDING_WORDS content words
    from the learner's recent messages, or quotes them directly. Whole words
    only -- "war" inside "aware" has cost this codebase four separate bugs.
    """
    pool = " ".join([learner_said or ""] + list(recent_learner or []))
    said = _content_words(pool)
    if not said:
        return False              # nothing was said; nothing can be attributed
    quoted = re.findall(r"[\"\u201c]([^\"\u201d]{3,80})[\"\u201d]", turn or "")
    for q in quoted:
        if _content_words(q) & said:
            return True
    return len(_content_words(turn) & said) >= MIN_GROUNDING_WORDS


def check(turn, learner_said="", concept_terms=None, already_seen=None,
          is_opening=False, max_words=MAX_WORDS, recent_learner=None):
    """Every rule this turn breaks, in the order worth fixing.

    Returns [] for a compliant turn. An empty or missing turn returns no
    violations -- there is nothing to regenerate against, and the caller has a
    bigger problem than turn shape.
    """
    if not (turn or "").strip():
        return []

    out = []

    n = word_count(turn)
    if n > max_words:
        out.append(Violation(
            "length", f"{n} words (limit {max_words})",
            f"Your last reply was {n} words; the limit is {max_words}. "
            f"Say the same thing in under {max_words} words. Cut explanation, "
            f"keep the question."))

    if not ends_with_question(turn):
        out.append(Violation(
            "question", "does not end with a question",
            "Your last reply did not end with a question. End with one short "
            "question that moves the student forward."))

    if not is_opening and not references_learner(turn, learner_said):
        snippet = " ".join((learner_said or "").split()[:16])
        out.append(Violation(
            "reference", "does not engage with what the learner said",
            f'Your last reply did not engage with what the student actually '
            f'said: "{snippet}". Refer to their words directly before asking '
            f'anything new.'))

    if attributes_to_learner(turn) and not attribution_is_grounded(
            turn, learner_said, recent_learner):
        snippet = " ".join((learner_said or "").split()[:20])
        out.append(Violation(
            "grounded_claim",
            "claims the student said something they did not",
            "Your last reply said something about what the student had said or "
            "done, but it does not match what they actually wrote"
            + (f': "{snippet}"' if snippet else " — this is the first turn, so "
               "they have not said anything yet")
            + ". Either quote their exact words, or drop the claim and ask "
              "instead."))

    intro = new_terms(turn, concept_terms, already_seen)
    if len(intro) > MAX_NEW_TERMS:
        out.append(Violation(
            "one_new_idea", f"introduced {len(intro)} new terms: {intro}",
            f"Your last reply introduced {len(intro)} new terms at once "
            f"({', '.join(intro)}). Introduce ONE and ask about it; the rest "
            f"can wait for later turns."))

    return out


def correction_note(violations):
    """The regeneration instruction. Names every offender, states the fix."""
    if not violations:
        return ""
    lines = ["Your previous reply broke the tutoring contract. Fix ALL of it "
             "and reply again:"]
    lines += [f"  - {v.instruction}" for v in violations]
    lines.append("Reply with the corrected tutor turn only.")
    return "\n".join(lines)


def is_better(candidate, original, **kw):
    """Fewer violations wins; ties keep the original.

    Used after a regeneration. A retry that fixes nothing must not be shipped
    just because it is newer -- and a retry that trades one violation for
    another is not an improvement.
    """
    return len(check(candidate, **kw)) < len(check(original, **kw))
