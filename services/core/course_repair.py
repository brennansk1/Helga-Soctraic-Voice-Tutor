"""Stage 4, Pass 3 — fix what the audit found, or withhold the concept.

Detection without repair is a list of complaints. This is the half that acts
on it, and the whole design turns on one measured fact:

    LLMs cannot reliably self-correct without external feedback, and often get
    WORSE when they try. Merely challenging a model makes it abandon correct
    answers.

So nothing here ever says "this is wrong, fix it". Every repair carries the
specific defect, the evidence that settles it, and an instruction to change
the minimum — and every repair is re-checked by the same external gates that
raised the finding. That is what makes a small model safe to use for this: a
bad repair costs a generation, not a new falsehood in a lesson.

THE FOUR OUTCOMES, AND WHY THE LAST ONE EXISTS

    fixed        the re-check passes, and the text is stored
    unchanged    the model returned something no better; the original stands
    escalated    the small model failed, the builder model was asked instead
    withheld     nothing fixed it, so the concept is marked and NOT taught

A concept withheld is a gap in a course. A concept served with a claim a
database contradicts is a lie told to someone who trusted it. The gap is the
better failure, and it is the one this chooses.

WHAT IS REPAIRABLE, AND WHAT IS NOT

Repairable here means "more or better TEXT would fix it": a false claim, a
missing section, deliberation left in the prose, a stub. Not repairable:
citations with no stored passage (a research problem), duplicate concept
titles (a structural one). Attempting those would burn model time to change
nothing, and the audit already reports them.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Checks whose findings a rewrite can actually settle.
REPAIRABLE_CHECKS = frozenset({
    "executable_claims",   # the engine's answer is in the finding
    "tutor_sections",      # a missing section is a generation request
    "content_guards",      # deliberation, stubs, build apologies
    "thin_content",        # needs evidence as well, but text is the fix
    "depth_contract",
})

# Findings a rewrite cannot touch, kept explicit so the split is readable
# rather than implied by omission.
NOT_REPAIRABLE = frozenset({"citations", "coherence", "missing_content"})

REPAIR_SYSTEM = (
    "You repair one section of teaching material. You are given the current "
    "text, a specific problem with it, and evidence. Change as little as "
    "possible: keep the structure, the headings, the voice, and every "
    "sentence that is not implicated. Return the complete corrected markdown "
    "and nothing else — no commentary, no explanation of what you changed."
)


def repairable(findings):
    """The subset of findings a rewrite could settle."""
    return [f for f in findings or []
            if (f.get("check") if isinstance(f, dict) else None)
            in REPAIRABLE_CHECKS]


def _order(findings):
    """Worst first, so a truncated prompt still carries the false claims."""
    rank = {"blocking": 0, "serious": 1, "minor": 2}
    return sorted(findings, key=lambda f: rank.get(f.get("severity"), 9))


def build_prompt(markdown, findings, title, course_title, evidence=None,
                 domain_guidance=""):
    """The repair instruction: defect, evidence, and change-the-minimum.

    NEVER "this is wrong, fix it". That phrasing is what the FlipFlop result
    measures — a model told it is wrong abandons correct answers — and it is
    the reason each problem is stated as a specific, checkable defect with the
    evidence that settles it attached.
    """
    problems = []
    for i, f in enumerate(_order(findings)[:6], 1):
        detail = f.get("detail") or ""
        quote = (f.get("quote") or "").strip()
        line = f"{i}. {detail}"
        if quote:
            line += f'\n   The text in question: "{quote[:200]}"'
        problems.append(line)

    parts = [
        f"Concept: {title}",
        f"Course: {course_title}" if course_title else "",
        "",
        "Problems found in the text below:",
        "\n".join(problems),
        "",
    ]

    if evidence:
        parts += [
            "Evidence. Correct the text to agree with this. Where it is an "
            "engine's actual behaviour, it is not open to interpretation:",
            "\n".join(f"- {e}" for e in evidence[:6]),
            "",
        ]
    if domain_guidance:
        parts += [domain_guidance, ""]

    parts += [
        "Rules:",
        "- Fix ONLY the problems listed. Leave everything else exactly as it is.",
        "- Keep every heading that is already present, and add one only if a "
        "problem above says it is missing.",
        "- Do not add commentary, notes about the correction, or any text "
        "explaining what you changed.",
        "",
        "Current text:",
        "---",
        markdown,
        "---",
        "",
        "Return the complete corrected markdown.",
    ]
    return "\n".join(p for p in parts if p != "")


def evidence_for(findings):
    """What the checks already know, phrased as facts the model must honour.

    The executable tier is the valuable case: it does not merely say a claim
    is wrong, it says what the engine actually does. Handing that over is the
    difference between a model guessing at a correction and applying one.
    """
    out = []
    for f in _order(findings):
        if f.get("check") == "executable_claims":
            says = f.get("detail") or ""
            if says:
                out.append(says)
    return out


_FENCE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n\s*```\s*$",
                    re.DOTALL)


def clean_output(text):
    """Strip a wrapping code fence, which models add to whole-document output."""
    if not text:
        return ""
    m = _FENCE.match(text.strip())
    return (m.group(1) if m else text).strip()


def is_plausible_repair(original, candidate):
    """Would storing this be an improvement, or damage?

    RARR's finding is the constraint: a revision is only useful if it PRESERVES
    the original. A model asked to fix one sentence sometimes returns a summary,
    an apology, or half the document — and a repair that silently drops two
    thirds of a lesson has done more harm than the sentence it fixed.

    Length is a blunt proxy and deliberately so: it needs no model, cannot be
    argued with, and catches the failure that actually happens.
    """
    if not candidate or len(candidate.split()) < 40:
        return False, "the repair came back empty or truncated"
    o, c = len(original.split()), len(candidate.split())
    if o and c < o * 0.6:
        return False, (f"the repair dropped {100 - round(100 * c / o)}% of the "
                       f"text — a fix, not a rewrite, was asked for")
    if o and c > o * 2.5:
        return False, "the repair more than doubled the text"
    return True, ""
