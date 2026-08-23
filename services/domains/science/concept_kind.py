"""What KIND of scientific knowledge a concept is, and how each is taught.

THE CONSTRAINT, WHICH IS AGAIN DIFFERENT
----------------------------------------
Each domain here turns on one thing the learner must not be asked for.

    computer science   do not ask them to type code
    mathematics        do not ask them to produce a solved answer
    history            do not ask them to guess a contingent fact
    science            DO NOT ASK THEM FOR AN OBSERVATION THEY CANNOT MAKE

A learner sitting at a screen cannot see what happens when the current is
reversed, cannot weigh the precipitate, cannot run the cross. Asking "so what
do you think happens?" as though the answer were derivable is a quiz with the
result withheld — history's failure wearing a lab coat.

But the whole method of science runs the other way, and that is the
opportunity: the learner PREDICTS, the tutor SUPPLIES the observation, and the
learner explains the gap. Predicting is not solving and not guessing; it is
committing to a consequence of what you currently believe, which is exactly
what makes the subsequent observation informative.

That is Predict–Observe–Explain, and it is the best-evidenced conceptual-change
strategy in science education — a measured normalised gain of 0.44, and
specifically effective at correcting misconceptions rather than papering over
them. It is also, conveniently, purely Socratic: the learner is never asked to
compute anything.

**So the observation is SUPPLIED, never demanded.** That is this domain's rule.

JOHNSTONE'S TRIANGLE, WHICH THE KINDS ENCODE
--------------------------------------------
Johnstone's account of why chemistry is hard generalises across the natural
sciences: any phenomenon can be described at three levels —

    MACROSCOPIC     what is observed: the colour change, the falling ball
    SUBMICROSCOPIC  the model that explains it: particles, fields, alleles
    SYMBOLIC        the notation: equations, formulae, free-body diagrams

— and the great risk is COGNITIVE OVERLOAD, because meaningful learning needs
movement between all three while working memory can only hold one. Students
struggle precisely because macroscopic concepts only make sense in terms of
submicroscopic models, and they are asked to coordinate both at once.

An expert slides between the levels without noticing. A learner cannot, and an
unannounced slide is where the thread is lost. So the kinds separate them —
`OBSERVATION` is macroscopic, `MECHANISM` and `MODEL` are submicroscopic,
`REPRESENTATION` is symbolic — and the guidance for each names which level it
is on and requires any move between levels to be made explicit.

WHY ONE DOMAIN FOR THREE SUBJECTS
---------------------------------
Physics, chemistry and biology differ enormously in content and hardly at all
in the structure that decides teaching. All three distinguish a phenomenon from
the model that explains it, all three carry measured quantities that are wrong
without their units, all three have empirically catalogued misconceptions that
survive instruction, and all three answer "how do we know?" with an experiment.
Splitting them would triple the code to encode the same distinctions.

Where they genuinely differ, they differ in CONTENT — the misconceptions are
physics' impetus, chemistry's conservation-of-mass-in-gases, biology's design
stance — and content belongs in the guidance text, not in separate kinds.
"""
import logging
import re

logger = logging.getLogger(__name__)

#: What is observed. Johnstone's MACROSCOPIC vertex.
OBSERVATION = "OBSERVATION"
#: A measured property, inseparable from its units and dimensions.
QUANTITY = "QUANTITY"
#: A regularity that holds — with the conditions under which it does.
LAW = "LAW"
#: Why the phenomenon happens. Johnstone's SUBMICROSCOPIC vertex.
MECHANISM = "MECHANISM"
#: A deliberate simplification, defined as much by where it fails.
MODEL = "MODEL"
#: Notation — equations, formulae, diagrams. Johnstone's SYMBOLIC vertex.
REPRESENTATION = "REPRESENTATION"
#: How we know: the evidence and the design that produced it.
EXPERIMENT = "EXPERIMENT"
#: Taxonomy, periodicity, classification — and the criterion behind it.
CLASSIFICATION = "CLASSIFICATION"
#: A documented, empirically catalogued error that survives instruction.
MISCONCEPTION = "MISCONCEPTION"
UNKNOWN = "UNKNOWN"

#: TEACHING ORDER. Lower is earlier.
#:
#: OBSERVATION leads because the phenomenon must exist for the learner before
#: any model of it can mean anything — teaching the model first is teaching an
#: answer to a question they have not yet been given.
#:
#: MISCONCEPTION ranks second, not last, and that is a claim from the
#: literature rather than a preference: the FCI showed these beliefs survive
#: instruction that never addresses them directly. A misconception met late is
#: met after the learner has spent the whole lesson reinterpreting everything
#: through it.
RANK = {
    OBSERVATION: 0,
    MISCONCEPTION: 1,
    QUANTITY: 2,
    LAW: 3,
    MECHANISM: 4,
    MODEL: 5,
    REPRESENTATION: 6,
    EXPERIMENT: 7,
    CLASSIFICATION: 8,
    UNKNOWN: 99,
}

#: Kinds that can USE a mined predict/observe pair. `CLASSIFICATION` cannot —
#: a taxonomy has nothing to predict — and neither can `UNKNOWN`.
AIDED_KINDS_ORDER = (MISCONCEPTION, OBSERVATION, LAW, MECHANISM, MODEL,
                     EXPERIMENT, QUANTITY)

#: Johnstone level per kind, so the tutor can say which one it is on.
LEVEL = {
    OBSERVATION: "macroscopic (what is observed)",
    QUANTITY: "macroscopic (what is measured)",
    EXPERIMENT: "macroscopic (what was done and seen)",
    MECHANISM: "submicroscopic (the model that explains it)",
    MODEL: "submicroscopic (the idealisation)",
    LAW: "spans macroscopic and symbolic",
    REPRESENTATION: "symbolic (the notation)",
    CLASSIFICATION: "macroscopic, organised by criterion",
    MISCONCEPTION: "usually a confusion BETWEEN levels",
}

GUIDANCE = {
    OBSERVATION: (
        "This concept is a PHENOMENON AS OBSERVED — Johnstone's macroscopic "
        "level. The learner cannot run the experiment, so DESCRIBE what is "
        "seen: what was set up, what happened, what the numbers were. Do not "
        "ask them to guess the result; that is asking for an observation they "
        "cannot make. Then ask ONE question about what the observation RULES "
        "OUT. Stay at this level for the whole turn — do not slip into "
        "explaining the mechanism, which is a different lesson and a "
        "different level."),

    MISCONCEPTION: (
        "This is a DOCUMENTED, EMPIRICALLY CATALOGUED error — not a guess "
        "about what learners might think. Physics has the Force Concept "
        "Inventory: that motion requires a continuing force, that a heavier "
        "mass exerts a greater force in a collision, that the most active "
        "agent produces the greatest force. Chemistry has mass appearing to "
        "vanish when a gas is produced. Biology has the design stance — "
        "explaining a trait by the purpose it serves rather than the process "
        "that produced it.\n"
        "These survive teaching that does not address them directly, so do "
        "not warn against it in the abstract. State the belief as it is "
        "actually held, get the learner to PREDICT what it implies in a "
        "specific case, then supply what is actually observed. The gap is the "
        "lesson. Do not tell them they are wrong before they have predicted — "
        "a prediction they made themselves is what makes the observation "
        "bite."),

    QUANTITY: (
        "This concept is a MEASURED QUANTITY, and its UNITS ARE PART OF IT — "
        "a right number with the wrong unit is not nearly right, it is wrong. "
        "Say what is being measured, in what units, and what a typical "
        "magnitude looks like, because a learner with no sense of scale "
        "cannot tell a plausible answer from an absurd one. Ask ONE question "
        "about DIMENSIONS rather than arithmetic: what the units of the "
        "combination must be, or which of two expressions cannot be right "
        "because its dimensions do not match. Never ask them to compute a "
        "value."),

    LAW: (
        "This concept is a REGULARITY THAT HOLDS — and the part learners miss "
        "is the CONDITIONS. A law stated without its domain of validity gets "
        "applied where it does not hold, which is the most common way a "
        "correct statement produces a wrong answer. State the law, then state "
        "where it stops. Ask ONE question about the boundary: a case where it "
        "applies and a neighbouring case where it does not, and what "
        "distinguishes them."),

    MECHANISM: (
        "This concept is WHY THE PHENOMENON HAPPENS — Johnstone's "
        "submicroscopic level, the model beneath what is seen. This is where "
        "reasoning actually lives, so spend the turn here. Connect it "
        "EXPLICITLY to the observation it explains, and say you are doing so: "
        "'what we saw was X; here is what is happening underneath.' Moving "
        "between levels without announcing it is where learners lose the "
        "thread, because an expert makes that move without noticing it. Ask "
        "ONE question about what the mechanism PREDICTS in a case not yet "
        "discussed."),

    MODEL: (
        "This concept is a DELIBERATE SIMPLIFICATION — the ideal gas, the "
        "point mass, the frictionless plane, the Bohr atom. A model is "
        "defined as much by WHERE IT FAILS as by what it captures, and a "
        "learner who does not know its limits will treat it as a description "
        "of reality. Say what it assumes away and what that buys. Ask ONE "
        "question about a situation where the assumption breaks and what goes "
        "wrong when it does."),

    REPRESENTATION: (
        "This concept is NOTATION — an equation, a formula, a free-body "
        "diagram, a Lewis structure. Johnstone's symbolic level, and the one "
        "students most often manipulate without meaning. READ IT ALOUD as a "
        "sentence about the world before anything else: what each symbol "
        "stands for, what the equality is claiming. Ask ONE question about "
        "MEANING, not manipulation: what a term becoming zero would "
        "physically correspond to, or what the notation asserts that could be "
        "false. Do not ask them to rearrange it."),

    EXPERIMENT: (
        "This concept is HOW WE KNOW — the evidence, and the design that "
        "produced it. This is the part of science most often skipped, leaving "
        "the learner with conclusions and no idea why anyone believes them. "
        "Describe what was done, what was measured, and what the result "
        "would have been had the hypothesis been false — a design that cannot "
        "fail cannot inform. Ask ONE question about the CONTROL: what had to "
        "be held fixed, and what the result would mean if it had not been."),

    CLASSIFICATION: (
        "This concept is a CLASSIFICATION — the periodic table, taxonomic "
        "rank, states of matter. The categories are not the point; the "
        "CRITERION is. Learners memorise the groupings and cannot say what "
        "puts a thing in one, which makes the whole scheme arbitrary to them. "
        "State the criterion first and the categories second. Ask ONE "
        "question about a borderline case — the thing that sits awkwardly is "
        "where the criterion becomes visible."),
}

#: THE STANDING RULE, in every science prompt.
NEVER_DEMAND_OBSERVATION = (
    "TWO ABSOLUTE RULES FOR THIS SUBJECT. FIRST: never ask the learner for an "
    "observation or a measurement they cannot make. They cannot run the "
    "experiment. Asking 'what do you think happens?' as though the result "
    "were derivable is a quiz with the answer withheld. Instead have them "
    "PREDICT — which commits them to a consequence of what they already "
    "believe — and then SUPPLY what is actually observed, and spend the turn "
    "on the gap between the two. SECOND: never ask them to calculate a value. "
    "Ask about units, dimensions, direction, sign, order of magnitude and "
    "what a result would MEAN — all of which are reasoning, none of which is "
    "arithmetic."
)

#: Ordered MOST SPECIFIC FIRST — the first match wins.
_PATTERNS = (
    (MISCONCEPTION, r"\b(misconception|common error|students? (?:often |"
                    r"typically )?(?:think|believe|assume)|naive|"
                    r"intuitive(?:ly)? wrong|why .{0,24} is not|"
                    r"myth about)\b"),
    (EXPERIMENT, r"\b(experiment|investigation|how (?:do|did) we know|"
                 r"evidence for|measuring|apparatus|procedure|lab\b|"
                 r"practical|assay|trial|observation(?:al)? study|"
                 r"control(?:led)? (?:group|variable))\b"),
    # `\w*` NOT `\b`. A trailing word-boundary after an alternation means
    # "equations" does not match "equation" — the boundary lands mid-word — so
    # "Balancing chemical equations" classified as UNKNOWN. Every group below
    # takes a word tail for the same reason.
    (REPRESENTATION, r"\b(equation|formula|notation|symbol|diagram|"
                     r"free[- ]body|lewis structure|balanced equation|"
                     r"chemical equation|graph of|circuit diagram|"
                     r"punnett|dot(?:-and-)?cross)\w*"),
    (QUANTITY, r"\b(unit|units|si unit|dimension(?:al|s)?|magnitude|"
               r"measur(?:e|ing|ement)|quantity|constant of|"
               r"how (?:much|many|fast|far|hot)|per (?:second|mole|kilogram))"
               r"\b"),
    (LAW, r"\b(law of|law\b|principle of|conservation of|"
          r"(?:newton|ohm|hooke|boyle|charles|faraday|mendel)'?s\b|"
          r"rule of|theorem of|equation of state)\b"),
    (CLASSIFICATION, r"\b(classification|taxonom|periodic table|"
                     r"(?:the )?groups? and periods?|kingdom|phylum|genus|"
                     r"species\b|states? of matter|types? of|categor)\b"),
    (MODEL, r"\b(model|idealis|idealiz|approximation|assumption|"
            r"simplif|bohr|ideal gas|point (?:mass|charge)|"
            r"frictionless|perfectly elastic)\b"),
    # SPECIFIC OBSERVATION PHRASES, ahead of MECHANISM's generic causal verbs.
    #
    # The table's rule is most-specific-first, and widening MECHANISM broke it:
    # "Observing the colour change" became MECHANISM because "change" matched
    # as a causal verb, when it is plainly a noun there. Rather than teach the
    # regex English grammar, the unambiguous observation phrases are matched
    # before the ambiguous verbs get a chance.
    (OBSERVATION, r"\b(observ\w*|colou?r change|precipitate forms?|"
                  r"what happens when|demonstration|phenomen\w*)"),

    # `\bwhy\b` BARE, not `why (?:does|do|is|are)`. Measured on the benchmark's
    # own topics: "Why ice floats on water" classified as UNKNOWN because there
    # is no auxiliary after "why" — and "Why X" is the most natural way a
    # science concept gets titled. A "why" question IS the request for a
    # mechanism; the auxiliary was never the signal.
    #
    # The causal verbs catch the other miss. "Natural selection" is a bare noun
    # phrase no title pattern can read, but its CONTEXT says heritable
    # variation "changes allele frequencies" — process language, which is what
    # a mechanism is. MECHANISM sits second-to-last here, so anything more
    # specific still wins first.
    (MECHANISM, r"\b(mechanism|why|explain(?:s|ing)? (?:why|how)|cause of|"
                r"process by which|how .{0,20} works|pathway|"
                r"reaction mechanism|underlying|results? in|leads? to|"
                r"gives? rise to|(?:change|produce|cause|drive|trigger)[sd]?)"
                r"\w*"),
    (OBSERVATION, r"\b(observ|phenomen|what happens when|demonstration|"
                  r"appears?|colour change|color change|precipitate forms|"
                  r"is seen|behaviour of|behavior of)\w*"),
)


def classify(title, text="", objectives=None):
    """The kind of scientific knowledge a concept is, or UNKNOWN.

    TITLE FIRST, then objectives, then a bounded prefix of the body. The
    mathematics domain learned by measurement that concatenating them lets an
    incidental phrase in the prose outvote an explicit one in the title — a
    lesson body will mention "experiment" in passing far more often than a
    title names one.

    Never raises.
    """
    try:
        def _best(hay):
            hay = (hay or "").strip().lower()
            if not hay:
                return UNKNOWN
            for kind, pattern in _PATTERNS:
                if re.search(pattern, hay, re.I):
                    return kind
            return UNKNOWN

        for hay in (title,
                    " ".join(objectives or []) if objectives else "",
                    (text or "")[:600]):
            kind = _best(hay)
            if kind != UNKNOWN:
                return kind
    except Exception as e:                       # pragma: no cover - defensive
        logger.debug(f"[SCI] classify failed for {title!r}: {e}")
    return UNKNOWN


def rank(kind):
    return RANK.get(kind, RANK[UNKNOWN])


def guidance(kind):
    return GUIDANCE.get(kind, "")


def level_of(kind):
    """Which of Johnstone's levels this kind sits on."""
    return LEVEL.get(kind, "")


def prompt_line(kind, has_pair=False):
    """The line that rides in the tutor prompt, or the standing rule alone."""
    text = GUIDANCE.get(kind)
    if not text:
        return NEVER_DEMAND_OBSERVATION
    lvl = LEVEL.get(kind, "")
    lvl_line = f"\n\nTHIS CONCEPT SITS AT THE {lvl.upper()} LEVEL." if lvl else ""
    if has_pair:
        return (f"{NEVER_DEMAND_OBSERVATION}{lvl_line}\n\nTEACHING THIS KIND "
                f"({kind}), as background only — the turn's instruction is the "
                f"material below: {text}")
    return (f"{NEVER_DEMAND_OBSERVATION}{lvl_line}\n\nHOW TO TEACH THIS "
            f"CONCEPT ({kind}): {text}")
