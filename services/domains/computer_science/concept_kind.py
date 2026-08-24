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
#: Operating a GUI product — the clicks, panes and menu paths of Power BI,
#: Snowflake's console, n8n's canvas, a Fabric workspace. Distinct from
#: TOOLING, which is a command you can show in a `code` aid.
TOOL_OPERATION = "TOOL_OPERATION"
#: Which layer a capability belongs in — dbt or the BI tool, SQL or the visual
#: builder. No menu path, no vendor answer: the durable half of tool knowledge.
TOOL_BOUNDARY = "TOOL_BOUNDARY"
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
    TOOL_OPERATION: 1,
    # Ranked with MECHANISM, not with TOOLING: it is a reasoning concept that
    # happens to be about tools, and teaching it early — before the learner
    # knows what either layer costs — makes it a preference poll.
    TOOL_BOUNDARY: 4,
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
    #: THE BOUNDARY DECISION — where the reasoning actually lives.
    #:
    #: The career checklist that prompted this kind states it as a learning
    #: objective outright: "Decide what logic belongs in dbt versus in the BI
    #: tool." That question has no menu path and no vendor answer. It is the
    #: durable half of tool knowledge, it survives the vendor moving the menu,
    #: and it is exactly what a Socratic tutor is FOR.
    TOOL_BOUNDARY: (
        "THIS IS A WHERE-DOES-IT-BELONG DECISION — the same capability can be "
        "built in more than one layer, and the skill is choosing. Logic in dbt "
        "or in the BI tool; a transformation in SQL or in the visual builder; "
        "a workflow in n8n's canvas or in one of its code nodes; a rule in the "
        "warehouse or in the application.\n"
        "Do NOT answer it. This is the most reasoning-rich thing in the "
        "subject and the learner can genuinely be led to it. Establish what "
        "each option makes easy and what it makes expensive — who can see the "
        "logic, what happens when a second consumer needs the same number, "
        "what breaks when the tool is replaced, who is on call when it fails "
        "at 3am. Then ask ONE question that forces the trade-off into the "
        "open.\n"
        "There is usually a defensible answer and it is not always the "
        "engineer's instinct: pushing everything into code can be wrong when "
        "the business owns the tool and not the repo. If the learner argues "
        "the other side well, say so."),

    TOOL_OPERATION: (
        "THIS IS OPERATING A PRODUCT'S INTERFACE, AND SOCRATIC QUESTIONING "
        "CANNOT REACH IT. Where a setting lives — Power BI's row-level "
        "security under Modeling then Manage Roles, a Snowflake warehouse's "
        "auto-suspend in its console — is a CONTINGENT FACT ABOUT A PRODUCT, "
        "decided by a vendor's designers. No amount of reasoning derives it, "
        "and asking the learner to guess it is a quiz with the answer "
        "withheld. It is also perishable: vendors move menus between "
        "releases.\n"
        "So STATE THE PATH PLAINLY — the pane, the menu, the order of steps — "
        "the way you would state a date. Do not ask them to find it, do not "
        "hint, and do not use a `code` aid: there is no command, and a code "
        "block implies one exists. USE A `steps` AID INSTEAD — a click path "
        "IS a sequence of labelled actions, which is exactly what that aid "
        "is: `label` the click, `detail` what it does.\n"
        "THEN SPEND THE WHOLE TURN ON THE PART THAT DOES CARRY REASONING, "
        "which is always one of: WHEN you would reach for this rather than "
        "the alternative, WHY the product models it this way, or WHAT it "
        "costs you later. Ask ONE question there. A learner who can click the "
        "path but cannot say when to use it has learned the perishable half "
        "and missed the durable one."),

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
    # THE TWO TOOL KINDS RUN FIRST, and the BOUNDARY runs before the
    # OPERATION. A concept can name a product AND ask where logic belongs —
    # "decide what logic belongs in dbt versus in the BI tool" contains "BI
    # tool" — and if OPERATION matched first, the most reasoning-rich concept
    # in the subject would be taught as a menu path.
    (TOOL_BOUNDARY, r"\b(versus|vs\.?|rather than|instead of|which layer|"
                    # "WHERE should X live" and "SHOULD X live" are the same
                    # question; requiring the leading "where" missed the
                    # commoner phrasing entirely.
                    r"(where |)(should|does|would)\b[^.?]{0,40}\b"
                    r"(live|belong|go|sit|be built|be done)\b|belongs? in|"
                    r"push(ing)? (logic|it) (down|up)|"
                    r"(tool|ui|gui|no.?code) (or|vs) (code|sql|script)|"
                    r"when the (visual|no.?code|gui) layer|"
                    r"drop into a code node|escape hatch)\w*"),
    # GUI products by name. Deliberately a NAME list rather than a shape: what
    # makes something a click-path is that a vendor drew it, and no pattern
    # over English detects that.
    (TOOL_OPERATION, r"\b(power ?bi|tableau|looker studio|excel|spreadsheet|"
                     r"fabric|synapse|databricks (ui|workspace|notebook)|"
                     r"snowsight|snowflake (console|ui)|airflow ui|"
                     r"n8n (canvas|editor|node|nodes)|zapier|make\.com|"
                     r"dashboard|workspace|dax|power query|"
                     r"click|menu|pane|ribbon|drag.and.drop|visual (builder|"
                     r"editor|layer))\w*"),
    # ANALYTICS AND PLATFORM VOCABULARY. Measured on a real career checklist:
    # after routing was fixed, 18 of 24 items still classified UNKNOWN — the
    # domain was claimed and no per-kind guidance applied. These patterns are
    # written from that checklist's own words rather than invented.
    (MECHANISM, r"\b(dimensional model|kimball|star schema|grain\b|"
                r"slowly changing dimension|scd\b|conformed dimension|"
                r"semantic layer|metricflow|governed metric|"
                r"normalis|normaliz|denormalis|denormaliz|"
                r"idempoten|partition|clustering|query plan|execution plan|"
                r"rbac|least privilege|role hierarch|"
                r"retrieval|rag\b|chunking|embedding|vector search|"
                r"guardrail|human.in.the.loop|eval harness|"
                r"data contract|lineage|drift|"
                r"warehouse sizing|cost model|finops|spend attribution)\w*"),
    (PROCEDURE, r"\b(end to end|stand up|deploy|self.host|provision|"
                r"orchestrat|schedule a|backfill|incremental model|"
                r"ci/cd|pipeline|containeris|containeriz|compose file|"
                r"infrastructure as code|terraform|migration)\w*"),
    (CONVENTION, r"\b(testing strategy|test suite|project structure|"
                r"naming convention|code review|branching|pull request|"
                r"documentation|runbook|style guide)\w*"),
    (TOOLING, r"\b(snowflake|airflow|dagster|docker|kubernetes|"
              r"databricks|bigquery|redshift|duckdb|n8n|postgres|"
              r"install|configure|setup|set up|virtualenv|"
              r"observability|monitoring|alerting|incident response)\w*"),
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
