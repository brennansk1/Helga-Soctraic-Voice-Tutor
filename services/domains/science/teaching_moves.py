"""Mining a science chapter for something the learner can be asked to PREDICT.

WHY A PAIR, AND WHY THIS PAIR
-----------------------------
Every domain here mines a two-part object from the source and hands it to the
tutor, because a concept alone produces the same turn for everyone. What the
two parts ARE is the domain's signature:

    computer science   a correct listing and a broken one
    mathematics        a worked step and the step after it
    history            a source and its provenance, or two historians
    science            A SETUP AND WHAT ACTUALLY HAPPENED

The science pair is a Predict–Observe–Explain: the learner is given the setup
and asked what they expect, and only then is told the result. That ordering is
the entire point. A learner told the result first will find it unsurprising —
hindsight makes every outcome obvious — and will learn nothing. A learner who
has committed to a prediction has something at stake in the observation.

POE is the best-evidenced conceptual-change strategy in science education
(measured normalised gain 0.44, and specifically effective on misconceptions
rather than merely on recall). It is also purely Socratic under this project's
constraint: predicting commits the learner to a consequence of what they
already believe, and asks them to compute nothing.

WHAT IS REFUSED
---------------
A "result" that merely restates the setup is not an observation, and a setup
with no stated outcome is not a POE — it is a question the tutor cannot answer
either. Both are dropped rather than passed on, for the same reason the history
domain refuses an extract with no provenance: half a move is worse than none,
because the tutor will improvise the missing half.
"""
import logging
import re

logger = logging.getLogger(__name__)

#: The four moves. `PREDICT_OBSERVE` is the signature; the rest cover concepts
#: that have no observable outcome to predict.
PREDICT_OBSERVE = "PREDICT_OBSERVE"
#: Move deliberately between Johnstone's levels, saying that you are doing so.
LEVEL_BRIDGE = "LEVEL_BRIDGE"
#: Dimensional reasoning — is this expression even the right KIND of thing?
UNITS_CHECK = "UNITS_CHECK"
#: How do we know? The evidence and what would have falsified it.
EVIDENCE_CHECK = "EVIDENCE_CHECK"

MIN_CHARS = 60
MAX_CHARS = 900
MAX_PER_CHAPTER = 8

#: A CONDITIONAL OR CONSEQUENCE SENTENCE — the antecedent is a setup and the
#: consequent is an outcome, which is a Predict–Observe–Explain already written
#: down. Splitting one and withholding the second half IS the move.
#:
#: THIS REPLACED AN EXPLICIT-FRAMING DETECTOR, AND THE REASON WAS MEASURED.
#: The first version looked for "Consider...", "Suppose...", "The result is..."
#: — the way a POE activity is written up. On 21,575 characters of real physics
#: text it found ONE setup and ONE outcome, unrelated to each other, and mined
#: ZERO pairs from eight pages.
#:
#: The mistake was a category error, not a loose pattern: POE is a CLASSROOM
#: ACTIVITY, and textbooks are not transcripts of one. They state results
#: directly. But the raw material is everywhere in declarative prose —
#:
#:     "If the voltage source is suddenly removed, current will continue to
#:      flow in the coil because of electromagnetic induction."
#:
#: — where the learner predicts the current stops and it does not. Measured on
#: the same text, these shapes appear about once per 3,000 characters, so a
#: chapter yields a handful rather than none.
#:
#: This is the same lesson the history domain learned on a real textbook, and
#: the same one recorded in `wikisource`: build detectors from real pages.
_CONDITIONAL = re.compile(
    r"(?P<setup>\b(?:If|When|Whenever|As|Once|After|Because|Since)\b"
    r"[^.!?;]{15,140})"
    r",\s*(?P<outcome>[^.!?]{20,220}[.!?])")

#: The other half of the same idea: X does Y to Z, stated as a consequence.
_CONSEQUENCE = re.compile(
    r"(?P<setup>[^.!?\n]{20,140}?)"
    r"\s*\b(?:causes|results in|leads to|produces|gives rise to|means that|"
    r"so that|therefore|which is why)\s+"
    r"(?P<outcome>[^.!?]{20,220}[.!?])", re.I)

#: Retained: where a book DOES write an explicit demonstration, that is the
#: best pair available, so it is still preferred when present.
_SETUP = re.compile(
    r"(?:^|\n)\s*(?:consider|suppose|imagine|in this (?:experiment|"
    r"demonstration)|set ?up\b)", re.I)

_OUTCOME = re.compile(
    r"\b(?:the result(?:ing)?\b|it turns out|in fact|is observed|was observed|"
    r"we (?:observe|find|see|measure)|experiments? show|"
    r"measurements? show|the data show|what actually happens|"
    r"surprisingly|contrary to|however,? the)\b", re.I)

#: The documented-error signal. Deliberately narrow: a claim about what
#: LEARNERS believe, not merely a statement that something is false.
_MISCONCEPTION = re.compile(
    r"\b(?:students?|learners?|people|many|it is)\s+(?:often|commonly|"
    r"frequently|usually|widely)?\s*(?:think|believe|assume|expect|"
    r"imagine|suppose|tempting to)\b", re.I)

#: Units and dimensions, for a UNITS_CHECK.
_UNITS = re.compile(
    r"\b(?:units? of|measured in|dimension(?:s|al)?|"
    r"(?:m|kg|s|N|J|W|Pa|mol|K|A|V|C|Hz|Ω)\s*(?:/|·|\^|per\b)|"
    r"per (?:second|metre|meter|kilogram|mole|kelvin))\b")

#: Evidence language, for an EVIDENCE_CHECK.
_EVIDENCE = re.compile(
    r"\b(?:control(?:led)?\s+(?:group|variable|experiment)|"
    r"hypothesis|falsif|repeat(?:ed|able)|replicat|"
    r"randomis|randomiz|blind(?:ed)?\b|sample size|"
    r"would have (?:shown|been)|rule[sd]? out)\b", re.I)

_SENT = re.compile(r"(?<=[.!?])\s+")

#: NOT A PREDICTION, whatever its grammar. Measured: splitting conditionals
#: without this filter mined "When you have mastered the information in this
#: chapter, you should be able to: compare and contrast hypotheses and
#: theories" — a learning-objectives list — and offered it to the tutor as
#: something for a learner to predict.
_BOILERPLATE = re.compile(
    r"\b(?:you (?:should|will) be able to|in this (?:chapter|section|book|"
    r"course)|by the end of|learning objectives?|as you (?:might |will )?"
    r"(?:remember|recall|have seen|know)|in the previous (?:chapter|section)|"
    r"we (?:will|shall) (?:see|discuss|examine|cover)|"
    r"figure \d|table \d|see (?:chapter|section|figure)|"
    r"click|exercise \d|problem \d)\b", re.I)

#: A DEFINITION, not an outcome. "X is the amount of Y per Z" restates a term;
#: a learner asked to predict it is being asked to already know the word.
_DEFINITIONAL = re.compile(
    r"^\s*(?:the\s+)?\w[\w\s-]{0,40}\b(?:is|are|was|were)\b\s*"
    r"(?:the|a|an|defined|called|known as|equal to)\b", re.I)

#: SOMETHING HAPPENS. A predictable outcome describes a change, a motion, a
#: process — not a property. This is what separates a physical consequence from
#: a restatement, and requiring it is what makes the split pairs worth having.
_PHYSICAL = re.compile(
    r"\b(?:increase|decrease|rise|fall|drop|grow|shrink|expand|contract|"
    r"flow|move|travel|accelerat|decelerat|stop|continue|reverse|"
    r"attract|repel|deflect|oscillat|vibrat|rotat|spin|"
    r"form|dissolve|precipitat|react|bond|break|split|combine|"
    r"heat|cool|melt|freeze|boil|evaporat|condens|"
    r"emit|absorb|reflect|refract|scatter|"
    r"divide|replicat|mutat|express|inherit|"
    r"double|halve|vanish|remain|persist|change|become|turn)\w*\b", re.I)


#: Words that make the next word a NOUN rather than a process. "the change in
#: potential" and "chemical reactions" both matched `_PHYSICAL` and neither
#: describes something happening — they name a quantity and a category.
_NOUNIFIER = re.compile(
    r"\b(?:the|a|an|of|in|its|their|this|that|these|those|any|each|"
    r"chemical|physical|nuclear|such)\s+$", re.I)


def _has_physical_verb(outcome):
    """True when the outcome describes something HAPPENING.

    Requiring a physical word is not enough: "the change in electric potential
    energy per second" and "crucial in chemical reactions" both contain one and
    neither is an outcome anybody could predict. The word has to be doing verb
    work, and the cheap test for that is what precedes it.
    """
    for m in _PHYSICAL.finditer(outcome or ""):
        if not _NOUNIFIER.search(outcome[:m.start()]):
            return True
    return False


def _trim(text, cap=MAX_CHARS):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:cap]


def _window(sentences, i, before=1, after=3):
    lo = max(0, i - before)
    hi = min(len(sentences), i + after + 1)
    return " ".join(sentences[lo:hi])


def _usable_pair(setup, outcome):
    """Whether this is a PREDICTION rather than a sentence with a comma in it.

    Shared by both mining paths, and that sharing is the point: these filters
    originally lived only in the split path, so the explicit path kept
    returning "A scientific theory, contrary to what many people think, is not
    a guess" as something for a learner to predict. Same defect, second door.
    """
    if len(setup) < 25 or len(outcome) < 25:
        return False
    # A consequent that merely renames the antecedent teaches nothing, and a
    # learner asked to predict it would be right by reading.
    if outcome[:40].lower() == setup[:40].lower():
        return False
    if _BOILERPLATE.search(setup) or _BOILERPLATE.search(outcome):
        return False
    if _DEFINITIONAL.match(outcome):
        return False
    # A definition anywhere in the opening, not only at the start.
    if re.search(r"\b(?:is|are) (?:not )?(?:a|an|the)\b", outcome[:70], re.I):
        return False
    # Advice to the reader is not a physical outcome.
    if re.search(r"\byou(?:r|'ll|'ve)?\b", outcome, re.I):
        return False
    return _has_physical_verb(outcome)


def _explicit_pairs(text, limit):
    """Pairs where the book actually stages a demonstration. Rare but best."""
    out = []
    sentences = _SENT.split(text or "")
    for i, s in enumerate(sentences):
        if not (_SETUP.search(s) or _MISCONCEPTION.search(s)):
            continue
        tail = _window(sentences, i, before=0, after=4)
        m = _OUTCOME.search(tail)
        if not m:
            continue
        setup = _trim(s)
        outcome = _trim(tail[m.start():])
        if not _usable_pair(setup, outcome):
            continue
        out.append({"kind": PREDICT_OBSERVE, "first": setup,
                    "second": outcome, "explicit": True,
                    "misconception": bool(_MISCONCEPTION.search(s))})
        if len(out) >= limit:
            break
    return out


def _split_pairs(text, limit):
    """Pairs made by splitting a conditional or consequence sentence.

    The antecedent becomes the setup and the consequent is withheld. See
    `_CONDITIONAL` for why this is the primary path and the explicit one is
    not.
    """
    out = []
    seen = set()
    for pattern in (_CONDITIONAL, _CONSEQUENCE):
        for m in pattern.finditer(text or ""):
            setup = _trim(m.group("setup"))
            outcome = _trim(m.group("outcome"))
            if not _usable_pair(setup, outcome):
                continue
            # DEDUPE ON BOTH HALVES. `_CONDITIONAL` and `_CONSEQUENCE` overlap
            # on the same sentence, which produced two "pairs" sharing one
            # outcome — the tutor would have revealed the same result twice.
            key, okey = setup[:60].lower(), outcome[:60].lower()
            if key in seen or okey in seen:
                continue
            seen.add(key)
            seen.add(okey)
            window = text[max(0, m.start() - 200):m.end()]
            out.append({
                "kind": PREDICT_OBSERVE,
                # The setup is restored to a readable clause: "If the voltage
                # source is suddenly removed" stands alone, "the current
                # continues to flow" is what the learner must not see.
                "first": setup if setup.endswith((".", "?")) else setup + " …",
                "second": outcome,
                "explicit": False,
                "misconception": bool(_MISCONCEPTION.search(window)),
            })
            if len(out) >= limit:
                return out
    return out


def poe_in_text(text, limit=MAX_PER_CHAPTER):
    """Predict/observe pairs: (setup, what actually happens).

    A pair needs BOTH halves present in the source. A setup with no stated
    outcome is dropped — the tutor would otherwise be asked to reveal a result
    the chapter never gave it, and would invent one.

    Explicit demonstrations come first when a book stages any; otherwise the
    pairs are split out of ordinary declarative prose, which is where they
    actually live. See `_CONDITIONAL`.
    """
    out = _explicit_pairs(text, limit)
    if len(out) < limit:
        have = {p["first"][:60].lower() for p in out}
        for p in _split_pairs(text, limit - len(out)):
            if p["first"][:60].lower() not in have:
                out.append(p)
    return out[:limit]


#: Shorter than MIN_CHARS, deliberately. A sentence stating units is naturally
#: terse — "The magnetic field is measured in tesla." is 40 characters — and
#: the 60-character floor rejected exactly the sentences this move wants.
MIN_UNITS_CHARS = 30


def units_in_text(text, limit=4):
    """Passages where a quantity's units are stated, for a UNITS_CHECK."""
    out = []
    for s in _SENT.split(text or ""):
        if _UNITS.search(s) and len(s.strip()) >= MIN_UNITS_CHARS:
            out.append({"kind": UNITS_CHECK, "first": _trim(s), "second": ""})
        if len(out) >= limit:
            break
    return out


def evidence_in_text(text, limit=4):
    """Passages describing how something was established."""
    out = []
    sentences = _SENT.split(text or "")
    for i, s in enumerate(sentences):
        if _EVIDENCE.search(s):
            body = _trim(_window(sentences, i, before=1, after=2))
            if len(body) >= MIN_CHARS:
                out.append({"kind": EVIDENCE_CHECK, "first": body,
                            "second": ""})
        if len(out) >= limit:
            break
    return out


def from_text(text):
    """Every move minable from one chapter, best kind first."""
    if not text:
        return []
    moves = []
    try:
        moves += poe_in_text(text)
    except Exception as e:
        logger.debug(f"[SCI] POE mining failed: {e}")
    try:
        moves += evidence_in_text(text)
    except Exception as e:
        logger.debug(f"[SCI] evidence mining failed: {e}")
    try:
        moves += units_in_text(text)
    except Exception as e:
        logger.debug(f"[SCI] units mining failed: {e}")
    return moves


#: Which move suits which learner state. The mathematics domain introduced this
#: hook; the point is that choosing material from the concept alone produces
#: the same turn whoever is sitting there.
_BEHAVIOUR_WANTS = {
    "bluffing": UNITS_CHECK,        # something concrete and checkable
    "stuck": PREDICT_OBSERVE,       # a prediction needs no prior success
    "ahead": EVIDENCE_CHECK,        # how do we know is the harder question
}


def best_move(moves, kind=None, behaviour=None):
    """The move to use, or None."""
    if not moves:
        return None
    want = _BEHAVIOUR_WANTS.get((behaviour or "").lower())
    if want:
        for m in moves:
            if m.get("kind") == want:
                return m
    # A misconception-bearing POE is the strongest thing this domain has.
    for m in moves:
        if m.get("kind") == PREDICT_OBSERVE and m.get("misconception"):
            return m
    for m in moves:
        if m.get("kind") == PREDICT_OBSERVE:
            return m
    return moves[0]


def choose_move(stored, behaviour=None):
    """Pick from a concept's stored `teaching_pair` and its alternatives."""
    if not stored:
        return None
    options = [stored] + list(stored.get("alternatives") or [])
    return best_move(options, behaviour=behaviour)


def prompt_block(move, beginner=False):
    """Turn a mined pair into the turn's instruction.

    THE ORDERING IS THE MOVE. The setup goes to the learner and the outcome is
    withheld until they have answered. Handing over both at once turns the
    strongest strategy in science education into a paragraph of exposition.
    """
    if not move:
        return ""
    kind = move.get("kind")
    first = _trim(move.get("first"), MAX_CHARS)
    second = _trim(move.get("second"), MAX_CHARS)

    if kind == PREDICT_OBSERVE:
        block = (
            "PREDICT–OBSERVE–EXPLAIN — RUN THIS TURN AS FOLLOWS, IT OVERRIDES "
            "THE GENERAL GUIDANCE ABOVE.\n\n"
            f"THE SETUP, which you GIVE the learner:\n{first}\n\n"
            f"WHAT ACTUALLY HAPPENS, which you WITHHOLD this turn:\n{second}\n\n"
            "Describe the setup, then ask them what they expect and WHY. Do "
            "not reveal the outcome in this message and do not hint at it — "
            "a learner who can read the answer off your phrasing has not "
            "predicted anything. Once they commit, give them the outcome "
            "plainly and ask them to account for the difference.")
        if move.get("misconception"):
            block += ("\nTheir prediction is likely to be the documented "
                      "wrong one. That is the point: do not steer them away "
                      "from it. A prediction they made and watched fail is "
                      "worth more than a warning they were given.")
        return block

    if kind == UNITS_CHECK:
        return ("UNITS THIS TURN. Use the passage below.\n\n"
                f"{first}\n\n"
                "Ask ONE question about DIMENSIONS, not arithmetic: what "
                "units the combination must have, or which of two candidate "
                "expressions cannot be right because its dimensions do not "
                "match. A right number with the wrong unit is wrong, and "
                "noticing that is a skill worth a whole turn.")

    if kind == EVIDENCE_CHECK:
        return ("HOW WE KNOW — use the passage below.\n\n"
                f"{first}\n\n"
                "Ask ONE question about the DESIGN: what had to be held "
                "fixed, or what the result would have been if the hypothesis "
                "were false. A study that could not have come out the other "
                "way tells us nothing, and that is the question to put.")

    if kind == LEVEL_BRIDGE:
        return ("BRIDGE THE LEVELS this turn.\n\n"
                f"{first}\n\n{second}\n\n"
                "Say explicitly which level you are moving from and to — "
                "'that is what we SEE; here is what is happening underneath'. "
                "Experts make this move without noticing; learners lose the "
                "thread exactly here.")
    return ""
