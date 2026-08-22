"""What KIND of thing is being taught — the recognition both halves need.

WHY ONE MODULE AND NOT TWO
--------------------------
Two separate problems turned out to be the same problem.

**The builder** cannot sequence a technical course. Documentation-convention
tiers get the coarse shape right (orientation, then setup, then the rest, with
reference at the tail) but they cannot tell that `defer` is an advanced
deployment feature or that `Authentication tokens` is reference material. Both
landed in the "building" tier of a real dbt build, at lessons 6 and 10.

**The tutor** does not know what kind of thing it is teaching. `prompts.py`
distinguishes exactly two things: arbitrary-vs-derivable (C.1) and grade band.
So "what is a model", "write a select statement" and "why does dbt use Jinja"
all get the same Socratic posture — and that is wrong for at least two of them.
Asking a student to *derive* the syntax of a `ref()` call is the same failure
C.1 was written to fix, one level down: there is nothing to reason toward.

Both need the same judgement: what kind of knowledge is this? So it is made
once, and consumed twice — the builder reads `rank` to sequence, the tutor
reads `guidance` to teach.

GROUNDING
---------
The kinds follow the conceptual / procedural / conditional distinction that
learning science has used since Anderson, plus the divisions a programming
curriculum actually has to make (syntax is not mechanism; tooling is not the
subject). Each kind carries an explicit teaching instruction rather than a
description, because this repository has measured instruction at 5/5 against
0/5 for description.

WHAT IT REFUSES TO DO
---------------------
Returns UNKNOWN rather than guessing when nothing matches, and UNKNOWN carries
no guidance line at all. A tutor told confidently that a concept is "syntax"
when it is really a mechanism will withhold the reasoning that was the whole
lesson. Same discipline as turn_state and learner_behaviour: say nothing rather
than something invented.
"""
import re

ORIENTATION = "ORIENTATION"
TOOLING = "TOOLING"
SYNTAX = "SYNTAX"
PROCEDURE = "PROCEDURE"
MECHANISM = "MECHANISM"
DEBUGGING = "DEBUGGING"
CONVENTION = "CONVENTION"
REFERENCE = "REFERENCE"
UNKNOWN = "UNKNOWN"

#: Teaching order. Lower comes first. This is what the builder sequences on,
#: and it encodes real prerequisite structure rather than documentation layout:
#: you cannot practise before you can install, cannot debug before you can
#: write, and reference material is looked up rather than taught through.
RANK = {
    ORIENTATION: 0,
    TOOLING: 1,
    SYNTAX: 2,
    PROCEDURE: 3,
    MECHANISM: 4,
    DEBUGGING: 5,
    CONVENTION: 6,
    REFERENCE: 8,
    UNKNOWN: 4,
}

#: How to teach each kind. Stated as an instruction to the tutor.
GUIDANCE = {
    ORIENTATION: (
        "This concept is about WHAT something is and WHY it exists. Do not ask "
        "the student to guess a definition. State what it is in one plain "
        "sentence, then spend the turn on the question that has reasoning in "
        "it: what problem does this solve, and what would people do without "
        "it?"),
    TOOLING: (
        "This is setup and tooling — installing, configuring, running a "
        "command. There is nothing to derive. Show the exact command or config "
        "in a `code` aid, then ask one question about what that step DID, so "
        "the student builds a model of the tool rather than copying "
        "keystrokes."),
    SYNTAX: (
        "This is the literal FORM of the language — what to type and where. "
        "Syntax is convention, not reasoning, so never ask the student to "
        "guess it. TEACH IT WITH A `code` AID: show the real, correct form as "
        "a code block, then use `blanks` to remove ONE element and `highlight` "
        "to draw the eye to the line that matters. Ask about the blank. Do not "
        "ask them to type a whole statement from memory."),
    PROCEDURE: (
        "This is a repeatable HOW-TO the student must be able to perform. "
        "TEACH IT WITH A `code` AID, not with prose and not by asking them to "
        "compose code unaided: show one real worked example, then show a "
        "near-identical second case with `blanks` on the parts that differ. "
        "The student completes the blank; you already know the answer, so you "
        "can actually check it. Prefer a real example from the source material "
        "over one you invent."),
    MECHANISM: (
        "This is how or why something WORKS underneath. This is the kind of "
        "concept Socratic questioning is for: the student can reason toward "
        "it from what they already know. Do not tell them. Ask the question "
        "that makes the mechanism necessary."),
    DEBUGGING: (
        "This is diagnosis — reading an error and finding the cause. TEACH IT "
        "WITH A `code` AID showing the BROKEN case, with `highlight` on the "
        "lines a reader should suspect. Ask what the student would CHECK "
        "FIRST and why, before revealing anything. The skill is the order of "
        "investigation, not the answer."),
    CONVENTION: (
        "This is true by convention or decision, with no derivation. State it "
        "plainly and immediately, then spend the turn on why the convention "
        "exists and what breaks without it."),
    REFERENCE: (
        "This is lookup material — parameters, flags, endpoints. Nobody "
        "memorises it and nobody should be quizzed on it. Teach the student "
        "how to FIND it: what section of the documentation answers this, and "
        "how would they recognise the right entry when they got there?"),
}

#: Ordered most-specific first. A title matching several kinds gets the most
#: specific, which is why "debug a failing test" is DEBUGGING and not PROCEDURE.
_PATTERNS = (
    (DEBUGGING, r"\b(debug|troubleshoot|error|exception|traceback|failure|"
                r"failing|diagnos|fix(ing)?\b|common problems|why (is|does).{0,30}"
                r"(fail|break|not work))"),
    (REFERENCE, r"\b(reference|api\b|endpoint|cli reference|all (options|flags|"
                r"commands)|parameters?\b|glossary|cheat ?sheet|specification)"),
    (TOOLING, r"\b(install|installation|setup|set up|configure|configuration|"
              r"environment variable|cli\b|command line|quickstart|prerequisite|"
              r"upgrade|version|deploy|authenticat|token|credential)"),
    (SYNTAX, r"\b(syntax|notation|declaration|signature|keyword|operator|"
             r"literal|expression|statement form|how to write|writing a)"),
    (DEBUGGING, r"\btest(ing|s)?\b.{0,20}\b(fail|error)"),
    (PROCEDURE, r"\b(how to|build(ing)?|creat(e|ing)|writ(e|ing)|add(ing)?|"
                r"implement|configur(e|ing) a|step|walkthrough|tutorial|"
                r"exercise|practice|workflow|run(ning)? a)"),
    # NOTE ON ORDERING: PROCEDURE is tested BEFORE this, so "how to build a
    # model" stays a procedure. That leaves the passive forms — "how the DAG is
    # built", "how a ref is resolved" — which are mechanism, and which the
    # original "how .. works" pattern missed entirely: they fell through to
    # UNKNOWN and so got no teaching guidance and no code aid.
    (MECHANISM, r"\b(how .{0,24}works?"
                r"|how .{0,24}\bis (built|resolved|stored|computed|generated"
                r"|created|handled|parsed|executed)"
                r"|under the hood|internals?|architecture|"
                r"why\b|because|mechanism|lifecycle|execution model|compil|"
                r"resolution|evaluat|behind the scenes)"),
    (CONVENTION, r"\b(convention|by design|naming|style guide|standard practice|"
                 r"idiomatic|best practice)"),
    (ORIENTATION, r"\b(what is|introduction|^intro\b|overview|welcome|"
                  r"core concepts?|key concepts?|about the)"),
)

_COMPILED = [(k, re.compile(p, re.I)) for k, p in _PATTERNS]


def classify(title, text="", objectives=None):
    """The kind of knowledge a concept is, or UNKNOWN.

    Reads the title first because it is the most deliberate signal, then falls
    back to a bounded prefix of the body. Never raises.
    """
    try:
        hay = " ".join([
            str(title or ""),
            " ".join(str(o) for o in (objectives or [])),
            str(text or "")[:600],
        ])
        if not hay.strip():
            return UNKNOWN
        for kind, pat in _COMPILED:
            if pat.search(hay):
                return kind
        # A body dense with code and no other signal is procedural: it is
        # showing the reader how to do a thing.
        if str(text or "").count("```") >= 4:
            return PROCEDURE
        return UNKNOWN
    except Exception:                        # pragma: no cover - defensive
        return UNKNOWN


def rank(kind):
    return RANK.get(kind, RANK[UNKNOWN])


def guidance(kind):
    """The tutor instruction for this kind, or "" for UNKNOWN."""
    return GUIDANCE.get(kind, "")


def prompt_line(kind, has_pair=False):
    """The line that rides in the tutor prompt, or "" when nothing is known.

    `has_pair` means the turn also carries mined source material with its own
    imperative instruction. Measured: DEBUGGING guidance says "show the BROKEN
    case" and the ERROR_FIX pair block says "show THIS error" — two
    instructions for the same turn, and the model followed neither cleanly. The
    mined material wins, because it is real and the general guidance is a
    description of what real material would look like.
    """
    g = guidance(kind)
    if not g:
        return ""
    if has_pair:
        return (f"WHAT KIND OF CONCEPT THIS IS ({kind}): {g}\n"
                f"NOTE: this turn carries specific source material below. Where "
                f"that material's instruction differs from this general "
                f"guidance, FOLLOW THE MATERIAL — it is real, this is general.")
    return f"WHAT KIND OF CONCEPT THIS IS ({kind}): {g}"


#: The teaching order as an ordered tuple, for callers that want the sequence
#: rather than a per-kind rank.
CODE_KINDS_ORDER = tuple(
    k for k, _ in sorted(RANK.items(), key=lambda kv: kv[1]) if k != UNKNOWN)
