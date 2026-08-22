"""Teachable MOVES mined from mathematics source material.

THE CONSTRAINT THIS SERVES
--------------------------
Teach mathematics Socratically WITHOUT asking the learner to solve anything.
There is no marker and no computer-algebra system here, so a learner's answer
cannot be checked — and in mathematics an unchecked wrong answer that is
praised teaches the error. "Now you try" is therefore not available, and it is
the move mathematics teaching is almost entirely built from.

What remains is everything the tutor can SHOW and then VERIFY from the source.
Four moves, each with evidence behind it:

  ERROR_HUNT     a wrong solution, and the correction. Ask which line is first
                 wrong and why, before revealing. Students working with
                 interactive erroneous examples beat problem-solving students
                 on a DELAYED post-test (Adams et al., CHB 2014) — retention is
                 the measure a tutor should care about. For a beginner the
                 error is SIGNPOSTED to a region rather than hidden: low-prior-
                 knowledge learners do measurably better when errors are
                 highlighted and worse when they are not.

  WORKED_STEP    a complete worked example. Show it, then ask what LICENSES one
                 particular step. The self-explanation effect (Chi et al. 1989)
                 is the mechanism, and its design lesson is specific:
                 successful self-explainers relate a step to an underlying
                 PRINCIPLE while unsuccessful ones restate the step, so the
                 question must ask for the principle.

  COMPARE        two correct methods for the same problem. Ask which is more
                 efficient and why. Rittle-Johnson & Star found comparison
                 effective for procedural FLEXIBILITY — and dependent on prior
                 knowledge, so this is reserved for later turns rather than a
                 learner's first contact.

  PREDICT        an expression or figure and its result. Ask for a QUALITATIVE
                 prediction — where it increases, whether it is positive, what
                 happens at the boundary — then reveal. Prediction needs no
                 arithmetic, and the source already contains the verified
                 answer, which is what makes it checkable without a solver.

WHY MINED AND NOT GENERATED
---------------------------
A model asked to invent a wrong solution invents a plausible one, and a
plausible-but-subtly-different error teaches the wrong diagnosis. Worse, in
mathematics a generated "error" is frequently not an error at all, or is wrong
in a second unintended way. Textbook errors are real, chosen by an author who
knows which mistake students actually make, and correct by construction.

WHAT IT REFUSES
---------------
Returns nothing rather than a weak move. A "worked example" whose solution is
"See Answer Key" promises a solution the tutor cannot show; two unrelated
examples are not a COMPARE; and an ERROR_HUNT with no identifiable wrong line
is a guessing game. Once a move is in the prompt the tutor cannot tell the
difference, so the filtering has to happen here.
"""
import logging
import re

logger = logging.getLogger(__name__)

ERROR_HUNT = "ERROR_HUNT"
WORKED_STEP = "WORKED_STEP"
COMPARE = "COMPARE"
PREDICT = "PREDICT"

#: Text that announces a deliberate error. Textbooks flag these; that flag is
#: the evidence, and inventing one without it is how a "misconception" turns
#: out to be correct mathematics.
_WRONG_MARKER = re.compile(
    r"("
    r"\bcommon (mistake|error)\b"
    r"|\bincorrect(ly)?\b"
    r"|\bthis is wrong\b"
    r"|\bwhat went wrong\b"
    r"|\bavoid this\b"
    r"|\bmisconception\b"
    r"|\bstudents? often (think|believe|write|assume)\b"
    r"|\bit is tempting to\b"
    r"|\bbeware\b"
    r"|\bcaution\b"
    r")", re.I)

#: The correction that follows a flagged error.
_FIX_MARKER = re.compile(
    r"("
    r"\bcorrect(ly)?\b|\binstead\b|\bin fact\b|\bactually\b"
    r"|\bthe correct\b|\brather\b|\bshould be\b"
    r")", re.I)

MIN_CHARS = 30
MAX_CHARS = 1200


def _trim(text, cap=MAX_CHARS):
    return re.sub(r"\s+", " ", (text or "")).strip()[:cap]


def _has_math(text):
    """Does this contain actual mathematics, not just prose about it?

    A move whose "expression" is a sentence teaches nothing, and LaTeX is what
    the reader produces, so its presence is the cheap signal.
    """
    t = text or ""
    return bool(re.search(r"\$[^$]{2,}\$|\\frac|\\sqrt|\\int|\\sum|[=<>≤≥]", t))


def from_examples(examples, notes=None):
    """Teachable moves from a page's mined examples and notes, best first.

    `examples` are {problem, solution, steps} as produced by
    `openstax.parse_book_html`. `notes` are the book's caution/note boxes,
    which is where flagged errors live.
    """
    out = []

    # ERROR_HUNT first — the scarcest material and the strongest move.
    for note in (notes or []):
        if not _WRONG_MARKER.search(note) or not _FIX_MARKER.search(note):
            continue
        if not _has_math(note) or len(note) < MIN_CHARS:
            continue
        out.append({
            "kind": ERROR_HUNT,
            "first": _trim(note),
            "second": "",
            "prompt": ("Show the flagged mistake and ask which line is first "
                       "wrong, and why, before revealing the correction."),
        })

    for ex in (examples or []):
        if not isinstance(ex, dict):
            continue
        problem = ex.get("problem") or ""
        solution = ex.get("solution") or ""
        steps = ex.get("steps") or []
        if len(solution) < MIN_CHARS or not _has_math(problem + solution):
            continue
        out.append({
            "kind": WORKED_STEP,
            "first": _trim(problem, 600),
            "second": _trim(solution),
            "steps": steps[:12],
            "prompt": ("Show the problem and its COMPLETE solution, then ask "
                       "what licenses ONE step — which definition, theorem or "
                       "property makes that move legal."),
        })

    # COMPARE needs two examples solving genuinely similar problems.
    pair = _comparable(examples or [])
    if pair:
        a, b = pair
        out.append({
            "kind": COMPARE,
            "first": _trim(a.get("solution"), 700),
            "second": _trim(b.get("solution"), 700),
            "prompt": ("Show both solutions side by side and ask which method "
                       "is more efficient and WHY — not which is correct, "
                       "both are."),
        })

    # PREFER MATERIAL THE TUTOR CAN SAY ALOUD.
    #
    # Helga teaches by VOICE, so a move whose notation `math_speech` cannot
    # render is one the learner HEARS AS RAW MARKUP. Measured: before
    # `math_speech` learned `\int`, every integration example in a calculus
    # chapter was unspeakable — and there was nothing to stop one being chosen
    # over a speakable alternative sitting right next to it.
    #
    # A tie-breaker rather than a filter: dropping every example containing one
    # exotic command would cost more teaching than it saves, and the kind
    # ranking still dominates — a spoken-imperfect ERROR_HUNT beats a clean
    # WORKED_STEP, because a real error is the scarcer material.
    rank = {ERROR_HUNT: 0, WORKED_STEP: 1, COMPARE: 2, PREDICT: 3}
    out.sort(key=lambda m: (rank.get(m["kind"], 9), 0 if is_speakable(m) else 1))
    return out


def is_speakable(move):
    """Can a voice tutor read this move's notation aloud? Never raises.

    True when `math_speech` is unavailable: an unavailable checker must not
    silently demote every piece of material in the course.
    """
    try:
        from services.core.math_speech import speech_for
    except Exception:
        return True
    try:
        blob = (move.get("first") or "") + " " + (move.get("second") or "")
        for _latex, _spoken, left in speech_for(blob):
            if left:
                return False
        return True
    except Exception:
        return True


_TOKEN = re.compile(r"[A-Za-z\\]{3,}")
_EXPR = re.compile(r"\$([^$]{2,})\$")


def _norm_expr(e):
    return re.sub(r"\s+", "", e or "")


def _comparable(examples):
    """Two examples solving THE SAME problem by different methods, or None.

    THE SIGNAL IS THE MATHEMATICS, NOT THE PROSE.
    The first version of this compared word overlap, and scored two solutions
    of the identical equation $2x+6=14$ at 0.2 — because the words differed
    ("by isolating x" versus "by dividing through") and the tokeniser dropped
    the expression, which was the only part that mattered. Backwards: differing
    prose with a shared expression is exactly what a COMPARE is.

    So a shared non-trivial EXPRESSION is the primary evidence, and word
    overlap is only the fallback for problems stated without notation.
    """
    examples = [e for e in (examples or []) if isinstance(e, dict)]
    if len(examples) < 2:
        return None

    def exprs(ex):
        return {_norm_expr(m) for m in _EXPR.findall(ex.get("problem") or "")
                if len(_norm_expr(m)) >= 4}

    for i in range(len(examples) - 1):
        for j in range(i + 1, len(examples)):
            shared = exprs(examples[i]) & exprs(examples[j])
            if shared:
                # Same problem, and the solutions must actually differ or
                # there is nothing to compare.
                a = _norm_expr(examples[i].get("solution"))
                b = _norm_expr(examples[j].get("solution"))
                if a and b and a != b:
                    return examples[i], examples[j]

    best, score = None, 0.0
    for i in range(len(examples) - 1):
        for j in range(i + 1, len(examples)):
            a = set(_TOKEN.findall((examples[i].get("problem") or "").lower()))
            b = set(_TOKEN.findall((examples[j].get("problem") or "").lower()))
            if not a or not b:
                continue
            overlap = len(a & b) / max(len(a), len(b))
            if 0.6 <= overlap < 0.95 and overlap > score:
                best, score = (examples[i], examples[j]), overlap
    return best


def best_move(moves, kind=None, allow_compare=False):
    """The single most teachable move, or None.

    `allow_compare` is False by default: comparison depends on prior knowledge
    (Rittle-Johnson & Star), so it is wrong for a learner's first contact with
    a concept and right once they have met one of the methods.
    """
    found = list(moves or [])
    if kind:
        found = [m for m in found if m["kind"] == kind]
    if not allow_compare:
        found = [m for m in found if m["kind"] != COMPARE]
    return found[0] if found else None


def prompt_block(move, beginner=False):
    """The tutor instruction for a mined move, or "".

    WHY AN INSTRUCTION AND NOT AN OFFER.
    Measured on the computer-science domain: four of five turns that had real
    mined material in the prompt used none of it, because material that is
    DESCRIBED competes with everything else in a long prompt and loses.
    Stating the turn's shape in the imperative, with the material inline,
    took uptake from 0/4 to 4/4. Same construction here.
    """
    if not move:
        return ""
    kind = move.get("kind")
    first = move.get("first") or ""
    second = move.get("second") or ""

    if kind == ERROR_HUNT:
        # HIGHLIGHTING IS DIRECTING ATTENTION, NOT DELIVERING THE VERDICT.
        # The first version told the tutor to "say which part contains the
        # mistake" for a beginner. Measured: it then opened "The mistake is in
        # the very first line where $x^{-2}$ is equated to $-x^2$. The rule
        # broken is that a negative exponent indicates a reciprocal" — both
        # the location AND the rule, which is the entire exercise.
        #
        # For a one-line statement "which part" IS the answer. The research
        # finding is that novices do better when the error is SIGNPOSTED; a
        # signpost points, it does not explain. So the beginner hint names
        # WHERE TO LOOK — a feature to examine — and never what is wrong
        # with it.
        signpost = (
            "The learner is a beginner, so point their attention at the "
            "feature to examine — 'look closely at the sign and where the "
            "exponent sits' — WITHOUT saying what is wrong with it and "
            "WITHOUT naming the line as the mistake. A signpost points; it "
            "does not diagnose.\n"
            if beginner else
            "Do not say where the mistake is; locating it is the skill.\n")
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE. You have a REAL "
            "flagged mistake from the source, so use it rather than inventing "
            "one — an invented error is often not an error, or is wrong in a "
            "second unintended way.\n"
            "\n"
            # SHAPE FIRST, AND EXPLICIT. Measured: with the prohibition stated
            # at the END of this block, the tutor opened "The mistake lies in "
            # "how the negative sign interacts with the exponent" — it
            # diagnosed the error in its first sentence, which is the entire
            # lesson given away. The learner then has nothing to find.
            "YOUR ENTIRE TURN IS TWO PARTS, IN THIS ORDER, AND NOTHING ELSE:\n"
            "  1. Display the flawed statement below, as written.\n"
            "  2. Ask ONE question: which line is the first that cannot be "
            "justified, and what rule does it break.\n"
            "\n"
            "FORBIDDEN THIS TURN, without exception: saying which line is "
            "wrong, naming the mistake, explaining why it is wrong, stating "
            "the correct rule, or describing what the right answer would be. "
            "Sentences beginning 'The mistake is...', 'The error is...' or "
            "'The rule broken is...' are all forbidden. If your answer "
            "contains the diagnosis, you have destroyed the exercise — the "
            "learner's job is to FIND it, and you have just done that job for "
            "them.\n"
            + signpost +
            "\nTHE FLAWED STATEMENT:\n"
            f"{first}\n")

    if kind == WORKED_STEP:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE — you have a real "
            "worked example from the source, so use it.\n"
            "THIS TURN: SHOW THE PROBLEM AND ITS COMPLETE SOLUTION, THEN ASK "
            "WHAT LICENSES ONE STEP.\n"
            "Show the whole solution. Do not hide steps and do not ask the "
            "learner to fill any in — they cannot be checked.\n"
            f"PROBLEM:\n{first}\n"
            f"SOLUTION:\n{second}\n"
            "Then ask ONE question about a SINGLE step: which definition, "
            "theorem or property makes that move legal. Ask for the "
            "PRINCIPLE, never for a restatement of the step — learners who "
            "name the principle learn from worked examples and learners who "
            "paraphrase do not.")

    if kind == COMPARE:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE — you have two "
            "real solutions from the source, so use them.\n"
            "THIS TURN: SHOW BOTH METHODS AND ASK WHICH IS MORE EFFICIENT.\n"
            f"METHOD A:\n{first}\n"
            f"METHOD B:\n{second}\n"
            "Then ask ONE question: which method is more efficient here, and "
            "what feature of the PROBLEM makes it so. Both are correct — do "
            "not ask which is right, and do not ask the learner to redo "
            "either one.")

    if kind == PREDICT:
        return (
            "THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE — you have real "
            "material from the source, so use it.\n"
            "THIS TURN: SHOW THE OBJECT BELOW AND ASK FOR A QUALITATIVE "
            "PREDICTION.\n"
            f"{first}\n"
            "Ask ONE question about SHAPE or BEHAVIOUR — where it increases, "
            "whether it is positive, what happens at the boundary. Never ask "
            "for a numeric value: a number cannot be checked here and a "
            "qualitative claim can be read straight off the source.")
    return ""
