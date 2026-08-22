"""What KIND of mathematical knowledge a concept is, and how each one is taught.

WHY MATHEMATICS NEEDS ITS OWN KINDS
-----------------------------------
The computer-science kinds do not transfer. `SYNTAX` versus `MECHANISM` is a
real distinction about code; the distinction that matters in mathematics is
between a thing that is TRUE BY DEFINITION, a thing that is TRUE AND PROVABLE,
and a thing that is a CONVENTION people agreed to write down a certain way.
Teaching those three identically is the classic failure: a student asked to
"derive" why the integral sign is an elongated S is being asked to reason about
an arbitrary historical choice, and a student told that the Mean Value Theorem
"just is" has been robbed of the only interesting thing about it.

THE CONSTRAINT EVERY KIND HERE RESPECTS
---------------------------------------
Teach Socratically WITHOUT asking the learner to solve anything. No "now you
try", no "compute the derivative of", no "what is x". There is no marker and no
solver, so an unchecked answer that looks right is worse than no answer — and
in mathematics a learner who miscomputes and is congratulated has learned the
error.

That sounds like a crippling restriction and the evidence says otherwise.
Students studying INTERACTIVE ERRONEOUS EXAMPLES outperformed students who
solved problems on a DELAYED post-test (Adams et al., CHB 2014) — the retention
measure, which is the one that matters for a tutor. What replaces solving:

  * studying a worked example and explaining WHY a step is licensed
    (the self-explanation effect, Chi et al. 1989 — and the finding that
    matters for prompt design is that successful self-explainers relate a step
    to an UNDERLYING PRINCIPLE while unsuccessful ones restate it, so the
    question must ask for the principle, not for the step)
  * hunting the error in a wrong solution (erroneous examples)
  * comparing two correct methods and judging which is more efficient and why
    (Rittle-Johnson & Star — effective for procedural FLEXIBILITY, and
    dependent on some prior knowledge, so it is reserved for later turns)
  * predicting what a graph or expression will do, then being shown

All four ask the learner to REASON about mathematics without producing an
answer that nobody can mark.

WHY UNKNOWN IS A REAL ANSWER
----------------------------
A wrong kind teaches the concept the wrong way, and titles in mathematics are
especially thin — "Working with the Chain Rule" could be a procedure, a
mechanism, or a set of exercises. Patterns answer where they are confident and
`classify.py` reads the source where they are not.
"""
import logging
import re

logger = logging.getLogger(__name__)

#: A mathematical OBJECT and what makes it that object. True by definition:
#: there is nothing to prove, and asking a learner to derive one wastes a turn.
DEFINITION = "DEFINITION"

#: Symbols and how they are written. Pure convention — dx, Σ, f'(x), the order
#: of matrix subscripts. Nobody can reason their way to a convention.
NOTATION = "NOTATION"

#: A claim that is true, under stated conditions, and provable. The conditions
#: are the teachable part and the part learners skip.
THEOREM = "THEOREM"

#: WHY a theorem is true. The argument itself, where the reasoning lives.
PROOF = "PROOF"

#: A repeatable method — integration by parts, row reduction, the quadratic
#: formula. The kind most at risk of being taught as "now you try".
PROCEDURE = "PROCEDURE"

#: The graphical, geometric or numerical MEANING of an object. What a
#: derivative looks like, what a matrix does to space.
REPRESENTATION = "REPRESENTATION"

#: Modelling something real. The teachable content is the assumptions, not the
#: arithmetic.
APPLICATION = "APPLICATION"

#: Magnitude, plausibility, sanity-checking. Rarely taught explicitly and the
#: cheapest defence a learner has against a wrong answer.
ESTIMATION = "ESTIMATION"

#: A known, named, predictable error. Whole research programmes exist on
#: these, which is exactly why they deserve their own treatment.
MISCONCEPTION = "MISCONCEPTION"

UNKNOWN = "UNKNOWN"

#: Lower = more specific. Used to break ties when several patterns match: the
#: more specific kind wins, because a concept that is BOTH "a procedure" and
#: "a known misconception" is best taught as the misconception.
RANK = {
    MISCONCEPTION: 0,
    NOTATION: 1,
    DEFINITION: 2,
    # PROOF ABOVE THEOREM, deliberately. "Proof of the Chain Rule" matches
    # both — `rule\b` is a theorem pattern — and it is a proof: the argument
    # is the content, and teaching it as a theorem would state the result and
    # skip the only interesting part. An explicit "proof"/"derivation" in a
    # title is a stronger signal than the incidental presence of "theorem".
    PROOF: 3,
    THEOREM: 4,
    PROCEDURE: 5,
    REPRESENTATION: 6,
    ESTIMATION: 7,
    APPLICATION: 8,
    UNKNOWN: 99,
}

#: Kinds for which a worked example, an error hunt or a figure is worth
#: attaching at build time. A definition does not need one; a procedure is
#: nearly useless without one.
AIDED_KINDS_ORDER = (PROCEDURE, MISCONCEPTION, THEOREM, PROOF,
                     REPRESENTATION, APPLICATION, ESTIMATION)

GUIDANCE = {
    DEFINITION: (
        "This concept is a DEFINITION — it is true because of how the term is "
        "defined, so there is nothing here to derive and nothing to prove. Do "
        "not ask the student to guess or construct the definition. State it "
        "once, plainly. Then spend the turn on the only part that carries "
        "reasoning: show one EXAMPLE and one NON-EXAMPLE and ask which one "
        "satisfies the definition and WHICH CLAUSE of it decides. That is a "
        "question about the definition's structure, and it can be answered "
        "without computing anything."),

    NOTATION: (
        "This concept is NOTATION — a convention people agreed on. It cannot "
        "be reasoned out, and asking a student why a symbol looks the way it "
        "does teaches them that mathematics is arbitrary guesswork. Show the "
        "notation, say what each part denotes, then ask the student to READ a "
        "specific expression back in words — what it says, not what it "
        "evaluates to. Never ask them to write or evaluate an expression."),

    THEOREM: (
        "This concept is a THEOREM: a claim that is true UNDER CONDITIONS. The "
        "conditions are the part students skip and the part that matters. "
        "State the theorem, then show TWO cases — one where the conditions "
        "hold and one where they fail — and ask which one the theorem applies "
        "to and WHY. Do not ask the student to prove it or to apply it to a "
        "new problem. Whether a hypothesis is met is a question of reading, "
        "not of calculation."),

    PROOF: (
        "This concept is a PROOF — the reasoning is the content. Show the "
        "steps as given in the source. Then pick ONE step and ask what "
        "licenses it: which earlier fact, definition or theorem makes that "
        "move legal. Ask for the PRINCIPLE behind the step, never for a "
        "restatement of it — students who relate a step to an underlying "
        "principle learn from worked examples and students who paraphrase do "
        "not. Never ask the student to supply a missing step."),

    PROCEDURE: (
        "This concept is a PROCEDURE — a method the student must eventually "
        "perform. This is the kind most likely to slide into 'now you try', "
        "and you must not. There is no marker here, so a student's attempt "
        "cannot be checked and a wrong method praised is a wrong method "
        "learned. TEACH IT FROM A COMPLETED WORKED EXAMPLE: show the whole "
        "solution, then ask why one particular step was CHOSEN — what in the "
        "problem made that the right move rather than an alternative. The "
        "skill being taught is recognising when the method applies, which is "
        "the part that transfers."),

    REPRESENTATION: (
        "This concept is about what something LOOKS like — a graph, a shape, a "
        "geometric meaning. Use a visual aid; that is what it is for. Show the "
        "figure, then ask the student to PREDICT a qualitative feature before "
        "you confirm it: where it increases, where it is steepest, what "
        "happens at the boundary. Predicting a shape needs no arithmetic. "
        "Every claim you then make must agree with the figure you drew."),

    APPLICATION: (
        "This concept APPLIES mathematics to a real situation. The teachable "
        "content is the modelling, not the arithmetic. Present the situation "
        "and the model that was built from it, then ask what ASSUMPTION the "
        "model makes and where it would break down. Do not ask the student to "
        "set up or solve the model."),

    ESTIMATION: (
        "This concept is about MAGNITUDE and plausibility — the sanity check "
        "that catches a wrong answer. Show a result, ideally a wrong one, and "
        "ask whether it is plausible and what cheap check would reveal it. "
        "Ask for the check, not for the correct value."),

    MISCONCEPTION: (
        "This concept is a KNOWN ERROR that learners reliably make. Do not "
        "warn against it in the abstract — a warning is forgotten and the "
        "error is not. Show the mistake being made, in full, as if it were a "
        "real solution. Ask the student to find the first line that is wrong "
        "and say why it is wrong. Reveal nothing until they commit to a line. "
        "If the student is a beginner, point at the region containing the "
        "error rather than leaving the whole solution open: low-prior-"
        "knowledge learners do measurably better when the error is signposted "
        "and measurably worse when it is hidden."),
}

#: Ordered most specific FIRST. Matched against title + objectives + a bounded
#: prefix of the source text.
_PATTERNS = (
    (MISCONCEPTION, r"\b(misconception|common error|common mistake|pitfall|"
                    r"(why|where) students? (go wrong|struggle)|"
                    r"frequently confused|caution|watch out)"),
    (NOTATION, r"\b(notation|symbol|convention for writing|how (we|to) write|"
               r"read(ing)? the (symbol|expression)|subscript|superscript|"
               r"sigma notation|interval notation|set-builder)"),
    # The passive and informal forms matter as much as the word "proof".
    # A course names this section "Where the quadratic formula comes from" at
    # least as often as "Derivation of the quadratic formula", and the earlier
    # 24-character window matched "Why the Fundamental Theorem holds" only by
    # luck — the gap is exactly 24 characters.
    (PROOF, r"\b(proof|prove|proving|derivation|deriv(e|ing) the|"
            r"why .{0,40}(is true|holds|works|must be)|"
            r"where .{0,40}(comes from|came from)|"
            r"how .{0,40}(was derived|is derived)|"
            r"justification of|reason(ing)? behind)"),
    # THEOREM before DEFINITION: "Definition of the Mean Value Theorem" is a
    # theorem being defined, and the theorem is the teachable object.
    (THEOREM, r"\b(theorem|lemma|corollary|law of|rule\b|identity|"
              r"criterion|test for|principle of|inequality)"),
    (DEFINITION, r"\b(definition|defining|what is a|introduction to the|"
                 r"the concept of|meaning of|terminology)"),
    (ESTIMATION, r"\b(estimat|approximat|order of magnitude|round(ing)?|"
                 r"sanity check|plausib|significant figures|bound(ing)? the)"),
    (REPRESENTATION, r"\b(graph|plot|sketch|geometr|visual|diagram|"
                     r"number line|unit circle|shape of|curve|"
                     r"transformation of the)"),
    (APPLICATION, r"\b(application|applied|real-world|modell?ing|word problem|"
                  r"in practice|used to (find|model|predict))"),
    (PROCEDURE, r"\b(how to|method|procedure|algorithm|technique|steps? for|"
                r"solving|comput(e|ing)|evaluat(e|ing)|simplify(ing)?|"
                r"finding the|factor(ing|ise|ize)?|integrat(e|ing|ion) by|"
                r"differentiat(e|ing)|row reduc|substitut)"),
)

_COMPILED = [(k, re.compile(p, re.I)) for k, p in _PATTERNS]

#: A BARE MATHEMATICAL OBJECT IS A DEFINITION CONCEPT — but only as a LAST
#: RESORT, after every ordered pattern above has declined.
#:
#: Real course sections are named "Eigenvalues" and "Partial derivatives", not
#: "Definition of an eigenvalue", and both fell through to UNKNOWN — which
#: costs the concept its teaching guidance entirely.
#:
#: Tried first as an ordinary pattern, it was far too greedy: "The Mean Value
#: Theorem" matched `mean` and "Graphing Rational Functions" matched
#: `functions`, and DEFINITION outranks both THEOREM and REPRESENTATION, so
#: two correct classifications became wrong ones. A title that names an object
#: AND an action is about the action. So this only speaks when nothing else
#: does, and only for a SHORT title — a long one has a verb in it somewhere.
_OBJECT = re.compile(
    r"^\W*(the\s+|a\s+|an\s+)?((partial|definite|indefinite|second|"
    r"first|inverse|natural|common)\s+)?"
    r"(eigenvalues?|eigenvectors?|derivatives?|integrals?|limits?|"
    r"matrices|matrix|vectors?|functions?|series|sequences?|polynomials?|"
    r"logarithms?|exponentials?|asymptotes?|slopes?|intercepts?|factorials?|"
    r"permutations?|combinations?|probabilit(y|ies)|distributions?|"
    r"variance|deviation|radians?|congruence|similarity)"
    r"\W*$", re.I)

MAX_OBJECT_WORDS = 4


def classify(title, text="", objectives=None):
    """The kind of mathematical knowledge a concept is, or UNKNOWN.

    Title first — it is the most deliberate signal — then objectives, then a
    bounded prefix of the body. Never raises.
    """
    try:
        hay = " ".join([
            str(title or ""),
            " ".join(str(o) for o in (objectives or [])),
            str(text or "")[:600],
        ])
        if not hay.strip():
            return UNKNOWN
        hits = [k for k, rx in _COMPILED if rx.search(hay)]
        if not hits:
            title_only = str(title or "").strip()
            if (title_only and len(title_only.split()) <= MAX_OBJECT_WORDS
                    and _OBJECT.match(title_only)):
                return DEFINITION
            return UNKNOWN
        # Most specific wins. Ties cannot happen: RANK is injective.
        return min(hits, key=lambda k: RANK.get(k, 99))
    except Exception as e:                # pragma: no cover - defensive
        logger.debug(f"[MATH] classify failed for {title!r}: {e}")
        return UNKNOWN


def rank(kind):
    return RANK.get(kind, RANK[UNKNOWN])


def guidance(kind):
    """The tutor instruction for this kind, or "" for UNKNOWN."""
    return GUIDANCE.get(kind, "")


#: The one rule that rides EVERY mathematics turn, whatever the kind.
#:
#: Measured across 24 turns without it: 2 turns asked the learner to compute —
#: "what is the average rate of change between $x=1$ and $x=1+h$?" and "what
#: is the derivative of the product of two functions?". Both slipped through
#: per-kind guidance because neither kind's instruction happened to name the
#: failure, and the model reaches for "ask them to work it out" by default:
#: it is what mathematics teaching looks like everywhere in its training data.
#:
#: So the prohibition is stated ONCE, globally, rather than trusting nine
#: separate guidance strings to each remember it.
NEVER_SOLVE = (
    "ABSOLUTE RULE FOR THIS SUBJECT: never ask the learner to compute, "
    "evaluate, simplify, differentiate, integrate or solve anything, and "
    "never ask what an expression EQUALS. There is no marker here, so a wrong "
    "answer cannot be caught and praising one teaches the error. Ask about "
    "STRUCTURE, REASONS and PREDICTIONS instead — which clause applies, what "
    "licenses a step, which line is first wrong, what the shape does. A "
    "question whose answer is a number or an expression is forbidden; a "
    "question whose answer is a reason is what you want."
)


def prompt_line(kind, has_pair=False):
    """The line that rides in the tutor prompt, or "".

    `has_pair` means the turn also carries mined source material with its own
    imperative instruction. Two imperatives for one turn produced a turn that
    followed neither cleanly when this was measured on the CS domain, so the
    mined material wins: it is real, and this guidance is a description of what
    real material would look like.
    """
    text = guidance(kind)
    # The standing rule applies even when the kind is UNKNOWN — an
    # unclassified mathematics concept is still mathematics, and is exactly
    # the case where per-kind guidance cannot help.
    if not text:
        return NEVER_SOLVE
    if has_pair:
        return (f"{NEVER_SOLVE}\n\nTEACHING THIS KIND ({kind}), as background "
                f"only — the turn's instruction is the material below: {text}")
    return f"{NEVER_SOLVE}\n\nHOW TO TEACH THIS CONCEPT ({kind}): {text}"
