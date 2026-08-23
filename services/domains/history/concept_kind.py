"""What KIND of historical knowledge a concept is, and how each one is taught.

WHY HISTORY NEEDS ITS OWN KINDS
-------------------------------
Neither of the existing domains transfers. Computer science distinguishes
syntax from mechanism; mathematics distinguishes definition from theorem. The
distinction that decides how history is taught is between:

  a thing that is simply THE CASE and cannot be reasoned out — the Battle of
  Hastings was fought on 14 October 1066;

  a thing that FOLLOWS from what came before — the July Crisis is an ordered
  chain, and the ordering carries the causation;

  and a thing HISTORIANS GENUINELY DISAGREE ABOUT — the relative weight of
  alliance systems, imperial rivalry and militarism in causing the First World
  War.

Teaching those three the same way fails in three different directions, and two
of those failures are the ones this domain's benchmark dimension measures.

THE CONSTRAINT THIS DOMAIN RESPECTS
-----------------------------------
The computer-science constraint was "never make them type code"; the
mathematics one, "never make them solve". History's is sharper and points the
other way:

  **NEVER ASK A LEARNER TO GUESS A CONTINGENT FACT.**

You cannot elicit that Hastings was 1066. Asking a student to work it out is
not Socratic teaching, it is a quiz with the answer withheld, and Koedinger &
Aleven's assistance dilemma says withholding stops helping at some point.
`honest_telling` — the benchmark dimension that scores exactly this — is 2.20
for history, the second lowest of any domain.

The reasoning lives everywhere else: in what a source's author had reason to
emphasise, in what else was happening at the time, in where two accounts
diverge, and in what turns on a disagreement between historians.

THE TWO-SIDED FAILURE
---------------------
`contested_interpretation` penalises BOTH directions, and this is the whole
difficulty of the domain:

  flattening a live historiographical debate into one settled story  -> low
  inventing a controversy where there is none                        -> low

So a domain module that simply says "present everything as contested" scores no
better than one that presents everything as settled. The kinds below carry that
distinction, because it cannot be recovered at teaching time from a title.
"""
import logging
import re

logger = logging.getLogger(__name__)

#: Contingent, and true because it happened. A date, a name, a place, a count.
#: Cannot be derived; must be told.
FACT = "FACT"

#: An ordered sequence where the ORDER carries meaning. The July Crisis is the
#: type case: which came first is the whole causal question.
CHRONOLOGY = "CHRONOLOGY"

#: Historians genuinely disagree, and the disagreement is live. The teachable
#: content is what turns on it.
CONTESTED = "CONTESTED"

#: Why something happened. Almost always multi-causal, and the weighting is
#: where the argument is.
CAUSATION = "CAUSATION"

#: A document, and who made it, when, why and for whom. Wineburg's sourcing
#: heuristic is the one skill unique to the historian's work.
SOURCE = "SOURCE"

#: What else was happening at the time, and how it changes the reading.
CONTEXT = "CONTEXT"

#: Why an event matters — itself contested, and contested differently by
#: different generations of historians.
SIGNIFICANCE = "SIGNIFICANCE"

#: What changed and what stayed the same across a period.
CONTINUITY = "CONTINUITY"

#: A known, named, predictable error — "the assassination alone caused the war".
MISCONCEPTION = "MISCONCEPTION"

UNKNOWN = "UNKNOWN"

#: Lower = more specific, and the tie-break when several patterns match.
#:
#: FACT RANKS FIRST, ABOVE EVERYTHING. A concept that is both "a date" and
#: "about causation" must be taught as the date: the one thing that must never
#: happen is a learner being asked to reason their way to a contingent fact,
#: and every other kind's guidance invites reasoning.
RANK = {
    FACT: 0,
    MISCONCEPTION: 1,
    CONTESTED: 2,
    SOURCE: 3,
    CHRONOLOGY: 4,
    CAUSATION: 5,
    SIGNIFICANCE: 6,
    CONTEXT: 7,
    CONTINUITY: 8,
    UNKNOWN: 99,
}

#: Kinds worth attaching build-time material to. A FACT needs no source
#: exercise — it needs stating — and a CONTEXT concept is prose.
AIDED_KINDS_ORDER = (CONTESTED, SOURCE, CAUSATION, MISCONCEPTION, CHRONOLOGY,
                     SIGNIFICANCE)

GUIDANCE = {
    FACT: (
        "This concept is a CONTINGENT FACT — a date, a name, a place, a "
        "number. It is true because it happened, and it cannot be derived "
        "from anything else. TELL IT, plainly and immediately, in your first "
        "sentence. Do not ask the learner to guess it, do not offer a "
        "multiple choice, and do not hint. Asking someone to work out a date "
        "is a quiz with the answer withheld, and it teaches them that history "
        "is trivia. Once it is stated, the whole turn is available for the "
        "part that DOES carry reasoning: why this fact is worth knowing, what "
        "it lets you place, or what people commonly confuse it with.\n"
        "IF THE LEARNER STATES IT WRONGLY, CORRECT THEM PLAINLY AND AT ONCE. "
        "Measured failure on this exact concept: a student said the Battle of "
        "Hastings was 1065 and the tutor let it stand. A wrong date left "
        "uncorrected is the worst outcome available here — worse than never "
        "raising it — because the learner leaves more confident and wrong.\n"
        "AND SAY THAT IT IS SETTLED. This is not a matter historians dispute, "
        "and saying so briefly is part of teaching it honestly: a learner who "
        "cannot tell which parts of history are settled and which are argued "
        "over has not understood the subject. Do not manufacture a debate "
        "about a date."),

    CHRONOLOGY: (
        "This concept is an ORDERED SEQUENCE, and the order is the content — "
        "what came first constrains what could follow. Lay out the steps in "
        "order, with their dates, as a timeline. Then ask ONE question about "
        "the ORDERING rather than the items: what would have had to be "
        "different if two events had happened the other way round, or which "
        "step made the next one hard to avoid. Do not ask the learner to "
        "recall the sequence — that is a memory test. Reasoning about "
        "consequence is not."),

    CONTESTED: (
        "Historians GENUINELY DISAGREE about this, and the disagreement is "
        "the content. Name at least two positions and say WHO holds them and "
        "on what evidence. Then ask ONE question about what TURNS on the "
        "disagreement: what would have to be true for one reading to be "
        "right, or what evidence would move someone from one to the other. "
        "Do not resolve it, do not present your own view as the answer, and "
        "do not ask the learner which is correct — the question has no "
        "settled answer and pretending otherwise is the failure this kind "
        "exists to prevent."),

    CAUSATION: (
        "This concept explains WHY something happened, and historical "
        "causation is almost never single. Set out the main causes that are "
        "actually argued for, and be explicit that historians weight them "
        "differently. Then ask ONE question about weighting or "
        "counterfactual: which cause, if absent, would most likely have "
        "changed the outcome. Do not present one cause as the answer, and do "
        "not let a chain of events stand in for an explanation."),

    SOURCE: (
        "This concept is about a DOCUMENT and where it came from. The skill "
        "is sourcing, and it is the one skill unique to the historian's work: "
        "who wrote this, when, for whom, and what did they stand to gain. "
        "Give the extract AND its provenance — an extract without provenance "
        "cannot be sourced and is just a quotation. Then ask ONE question "
        "about what the author's position makes them likely to emphasise or "
        "to leave out. That is answerable from the provenance alone and needs "
        "no outside knowledge."),

    CONTEXT: (
        "This concept is about WHAT ELSE was happening at the time, and why "
        "that changes how something reads. State the surrounding "
        "circumstances plainly. Then ask ONE question about how the "
        "contemporary meaning differs from the obvious modern reading — what "
        "someone at the time would have understood by it. Guard against "
        "presentism without lecturing about presentism."),

    SIGNIFICANCE: (
        "This concept is about why something MATTERS, which is itself "
        "contested and has usually been answered differently by different "
        "generations. Say what significance has been claimed for it and by "
        "whom. Then ask ONE question about the criterion: significant to "
        "whom, judged how, and over what timescale. Do not assert importance "
        "as though it were a fact about the event rather than a judgement "
        "about it."),

    CONTINUITY: (
        "This concept is about what CHANGED and what stayed the same. "
        "Learners reliably over-read change, because change is what gets "
        "narrated. State both sides. Then ask ONE question about the "
        "continuity — what a person living through this would NOT have "
        "noticed changing."),

    MISCONCEPTION: (
        "This is a KNOWN ERROR that learners reliably hold — that the "
        "assassination alone caused the war, that the alliances activated "
        "automatically. Do not warn against it in the abstract; a warning is "
        "forgotten and the belief is not. State the claim as it is usually "
        "believed, then ask ONE question that puts pressure on it: what the "
        "claim would predict that did not in fact happen. Do not say it is "
        "wrong before they have answered."),
}

#: Ordered most specific FIRST.
_PATTERNS = (
    # FACT first and deliberately narrow: only titles that ARE the fact.
    (FACT, r"\b(the date of|the year|what year|when did|dates? of the|"
           r"how many .{0,20}(died|were|there were)|the name of)"),
    (MISCONCEPTION, r"\b(misconception|myth|common(ly)? (believed|thought)|"
                    r"often (assumed|believed)|did .{0,20} really|"
                    r"the myth of)"),
    (CONTESTED, r"\b(debate|contested|controvers|historians (disagree|differ|"
                r"have argued)|interpretations? of|historiograph|"
                r"revisionis|schools? of thought|why historians)"),
    (SOURCE, r"\b(source|document|testimony|account of|letter|diary|"
             r"despatch|dispatch|memoir|eyewitness|propaganda|primary)"),
    (CHRONOLOGY, r"\b(sequence|chronolog|timeline|order of events|"
                 r"the .{0,16}crisis\b|steps? to war|road to|"
                 r"in what order|events? leading)"),
    (CAUSATION, r"\b(causes?|why did|origins? of|led to|brought about|"
                r"reasons? for|responsib)"),
    (SIGNIFICANCE, r"\b(significance|importance|why .{0,20}matters?|legacy|"
                   r"impact of|consequences? of)"),
    (CONTINUITY, r"\b(continuity|change and|what changed|persisted|endured|"
                 r"remained the same)"),
    (CONTEXT, r"\b(context|background|at the time|contemporary|"
              r"world of|conditions in)"),
)

_COMPILED = [(k, re.compile(p, re.I)) for k, p in _PATTERNS]


def classify(title, text="", objectives=None):
    """The kind of historical knowledge a concept is, or UNKNOWN.

    TITLE FIRST, then objectives, then a bounded prefix of the body — the
    mathematics domain learned this the hard way, where concatenating them let
    an incidental phrase in the prose outvote an explicit one in the title.

    Never raises.
    """
    try:
        def _best(hay):
            if not (hay or "").strip():
                return UNKNOWN
            hits = [k for k, rx in _COMPILED if rx.search(hay)]
            if not hits:
                return UNKNOWN
            return min(hits, key=lambda k: RANK.get(k, 99))

        head = " ".join([str(title or ""),
                         " ".join(str(o) for o in (objectives or []))])
        kind = _best(head)
        if kind != UNKNOWN:
            return kind
        return _best(str(text or "")[:600])
    except Exception as e:                # pragma: no cover - defensive
        logger.debug(f"[HIST] classify failed for {title!r}: {e}")
        return UNKNOWN


def rank(kind):
    return RANK.get(kind, RANK[UNKNOWN])


def guidance(kind):
    """The tutor instruction for this kind, or "" for UNKNOWN."""
    return GUIDANCE.get(kind, "")


#: The rule that rides EVERY history turn, whatever the kind.
#:
#: Stated globally for the same reason mathematics states its own: per-kind
#: guidance cannot be relied on to remember a rule that applies to all of them,
#: and the model's default behaviour — quizzing for recall — is what history
#: teaching looks like across most of its training data.
#:
#: TWO clauses, because this domain's benchmark dimension penalises two
#: opposite failures and a rule against only one of them would push the tutor
#: straight into the other.
NEVER_QUIZ = (
    "TWO ABSOLUTE RULES FOR THIS SUBJECT. FIRST: never ask the learner to "
    "guess a contingent fact — a date, a name, a place, a number. Those "
    "cannot be reasoned out; state them plainly and spend the turn on what "
    "can be reasoned about. Asking someone to produce a date they have not "
    "been told is a quiz, not a question. SECOND: do not present a live "
    "historical disagreement as settled, and do not manufacture a "
    "disagreement where historians broadly agree. Both are failures of the "
    "same kind — misrepresenting how confident the discipline actually is."
)


def prompt_line(kind, has_pair=False):
    """The line that rides in the tutor prompt, or the standing rule alone.

    `has_pair` means the turn also carries mined source material with its own
    imperative instruction. Two imperatives for one turn produced a turn that
    followed neither when this was measured on the computer-science domain, so
    the mined material wins and the kind guidance stands down to background.
    """
    text = guidance(kind)
    if not text:
        return NEVER_QUIZ
    if has_pair:
        return (f"{NEVER_QUIZ}\n\nTEACHING THIS KIND ({kind}), as background "
                f"only — the turn's instruction is the material below: {text}")
    return f"{NEVER_QUIZ}\n\nHOW TO TEACH THIS CONCEPT ({kind}): {text}"
