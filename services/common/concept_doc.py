"""concept_doc.py — read the concept markdown the way each consumer needs it.

WHY THIS EXISTS

Hydration writes one Markdown file per concept, and that file is the only
durable product of an expensive pipeline: web research, a grounding-confidence
retry, an LLM structuring call, a depth-contract enforcement loop, a sampled
fact check, and asset collection. Everything downstream — the Socratic tutor,
the micro-lecture, grading, flashcards, search — reads that one file.

It was being read with two blunt instruments, and both were losing the
expensive part:

  1. A DELETE LIST. `_redact_context_for_tutor` stripped "Core Explanation",
     "Key Facts" and "Real-World Examples" from the tutor's context to stop the
     model reading the answer out loud. It applied to the LECTURER too — the
     one mode whose entire job is to explain the concept. So the lecturer was
     handed misconceptions and hooks, no explanation and no facts, and the
     system prompt then told it to "fill in the gaps" from its own knowledge.
     The fact-check pass only ever edits sections the tutor could not see.

  2. A HEAD CUT. What survived was truncated with `[:2000]`. Measured on a
     realistic mastery-4 concept (839 words): 43% of the stored document
     reached the lecturer, and the sections lost to the cut were Governing
     Result, Derivation and Exercise — which is to say, precisely the content
     that only mastery 4 and 5 generate. A more expensive course delivered
     LESS to the prompt than a cheap one, because the extra sections are
     appended at the end and a head cut takes the front.

The fix is not a bigger budget — a local 9B pays for every token. It is to
spend the SAME budget on the right sections. This module parses the document
once, then packs it by priority for the asking consumer, so the first thing
dropped is the least useful section rather than the last one written.

WHAT EACH MODE GETS, AND WHY

  lecture   Core Explanation, Key Facts, the worked example, the derivation.
            A lecturer that cannot see the explanation invents one.

  socratic  The same ground truth MINUS the worked example. That single
            section is the real spoiler the delete-list was built to stop: a
            step-by-step solution is trivially easy to hand over. Facts and
            the explanation are not spoilers — they are what keeps the
            questioner from teaching an error, and what grading compares
            against.

  grading   Everything. The grader is not talking to the student.

Headers are preserved verbatim (`## Socratic Hooks`, `## Edge Cases &
Limitations`) because prompts.py and fsm_logic.py both regex for them.
"""

import re
from collections import OrderedDict

# Section names this project writes, in the order the hydrator emits them.
# Aliases map the older names still present in courses built by earlier
# versions onto the current ones, so a v1 document is not silently treated as
# a document with no explanation in it.
ALIASES = {
    "Core Definition": "Core Explanation",
    "Contextual Explanation": "Core Explanation",
    "Component Breakdown": "Key Facts",
    "Advanced Notes": "Edge Cases & Limitations",
    "Socratic Hook": "Socratic Hooks",
    "Examples": "Real-World Examples",
}

_HEADER_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)


def split_sections(markdown):
    """Parse a concept document into (preamble, OrderedDict of sections).

    `preamble` is everything before the first `##` heading — the `# Title`
    line. Duplicate headings are joined rather than overwriting each other:
    `_validate_markdown_structure` appends a stub when a heading is missing,
    and an LLM that emitted the heading late produces two of them.
    """
    if not markdown:
        return "", OrderedDict()

    matches = list(_HEADER_RE.finditer(markdown))
    if not matches:
        return markdown.strip(), OrderedDict()

    preamble = markdown[:matches[0].start()].strip()
    sections = OrderedDict()
    for i, match in enumerate(matches):
        name = match.group(1).strip()
        name = ALIASES.get(name, name)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[match.end():end].strip()
        if name in sections:
            sections[name] = f"{sections[name]}\n{body}".strip()
        else:
            sections[name] = body
    return preamble, sections


def section(markdown, name):
    """One section's body, or "" — alias-aware, unlike a bare regex."""
    _pre, sections = split_sections(markdown)
    return sections.get(ALIASES.get(name, name), "")


# Per-mode section priority. Lower number = packed first = last to be dropped
# when the budget runs out. A section absent from a mode's table is never
# included in that mode.
#
# `cap` bounds a single section so one runaway section cannot starve the rest;
# the packer trims to the last complete line within the cap rather than mid-word.
_LECTURE = {
    "Core Explanation":         (1, 1400),
    "Key Facts":                (2, 700),
    "Real-World Examples":      (3, 900),
    "Misconceptions":           (4, 600),
    "Governing Result":         (5, 500),
    "Analogies":                (6, 500),
    "Derivation":               (7, 900),
    "Edge Cases & Limitations": (8, 500),
    "Learning Objectives":      (9, 300),
}

# The worked example is deliberately absent: it is a solved problem, and this
# mode is asking the student to solve one. Everything else that keeps the
# questioner factually honest is present.
_SOCRATIC = {
    "Socratic Hooks":           (1, 700),
    "Key Facts":                (2, 700),
    "Core Explanation":         (3, 1200),
    "Misconceptions":           (4, 600),
    "Mastery Criteria":         (5, 500),
    "Edge Cases & Limitations": (6, 500),
    "Analogies":                (7, 500),
    "Governing Result":         (8, 400),
    "Learning Objectives":      (9, 300),
    "Exercise":                 (10, 400),
}

# Grading judges ONE answer to ONE question. It needs the standard to judge
# against and the facts to check the answer's claims — not the pedagogy.
#
# The FSM passed `self.current_context` here: the whole document, up to the
# 10,000-character slice taken when the concept loaded. Measured, that is
# ~2,780 tokens of prefill on EVERY student answer, to produce a ~90-token
# JSON verdict. Hooks, analogies and misconceptions cannot change a grade;
# they were being re-read by the model on every turn for nothing.
_GRADING = {
    "Mastery Criteria":         (1, 600),
    "Key Facts":                (2, 700),
    "Core Explanation":         (3, 900),
    "Real-World Examples":      (4, 600),   # the worked answer IS the rubric here
    "Governing Result":         (5, 400),
    "Edge Cases & Limitations": (6, 400),
}

MODES = {"lecture": _LECTURE, "socratic": _SOCRATIC, "grading": _GRADING}

# Emission order — how the packed sections are laid out for the model, which is
# not the order they were selected in. Selection order decides what SURVIVES;
# this decides what it READS like.
_EMIT_ORDER = [
    "Learning Objectives", "Core Explanation", "Key Facts", "Governing Result",
    "Derivation", "Real-World Examples", "Misconceptions",
    "Edge Cases & Limitations", "Analogies", "Socratic Hooks",
    "Mastery Criteria", "Exercise",
]

import os
STRICT_SOCRATIC_BUDGET = os.getenv("HELGA_STRICT_SOCRATIC_BUDGET") == "1"
DEFAULT_BUDGET = {"lecture": 2400, "socratic": 1500 if STRICT_SOCRATIC_BUDGET else 3600, "grading": 1600}

# The preamble is normally one `# Title` line. The cap is there so a malformed
# document with everything above the first heading cannot spend the whole
# budget before a single section is considered.
PREAMBLE_CAP = 600


def _trim(body, cap):
    """Trim to `cap` chars on a line boundary. A section cut mid-sentence reads
    as a model error to the model — it will try to continue it."""
    if len(body) <= cap:
        return body
    clipped = body[:cap]
    newline = clipped.rfind("\n")
    if newline > cap * 0.4:
        clipped = clipped[:newline]
    return clipped.rstrip() + "\n…"


def tutor_context(markdown, mode="socratic", budget=None):
    """Pack the concept document into `budget` characters for `mode`.

    Returns markdown with `## ` headers intact, so the existing hook and
    notes regexes in prompts.py keep matching.
    """
    table = MODES.get(mode)
    if table is None:
        return markdown or ""
    if budget is None:
        budget = DEFAULT_BUDGET.get(mode, 3000)

    preamble, sections = split_sections(markdown)
    if not sections:
        return (markdown or "")[:budget]

    # Anything above the first heading is kept. It is normally just the
    # `# Title` line, but the FSM also prepends a GROUNDING caution here when
    # the concept's research pass came back thin — and get_micro_lecture_prompt
    # re-packs defensively, so dropping the preamble would silently delete that
    # caution on exactly the lecture turns that most need it.
    # Capped against the budget as well as absolutely, so a caller asking for a
    # very small context cannot get back a reply made entirely of preamble.
    preamble = preamble[:min(PREAMBLE_CAP, max(0, budget) // 4)].strip()
    used = len(preamble) + 2 if preamble else 0

    candidates = sorted(
        ((table[name][0], name, body, table[name][1])
         for name, body in sections.items()
         if name in table and body.strip()),
        key=lambda row: row[0],
    )

    chosen = {}
    for _priority, name, body, cap in candidates:
        block = _trim(body.strip(), cap)
        cost = len(name) + len(block) + 6      # "## " + name + "\n\n" + body
        if used + cost > budget:
            # Try a squeezed version before giving up on the section entirely;
            # half a Key Facts list beats none of it. The 8 is the 6-char
            # header overhead plus the 2 characters `_trim` appends as an
            # ellipsis: `_trim` can return up to cap+2, and budgeting only the
            # header leaves that to be reclaimed by its `rstrip()`, which
            # happens to work on today's inputs and is not something to rely on.
            room = budget - used - len(name) - 8
            if room < 160:
                continue
            block = _trim(block, room)
            cost = len(name) + len(block) + 6
        chosen[name] = block
        used += cost

    ordered = [n for n in _EMIT_ORDER if n in chosen]
    ordered += [n for n in chosen if n not in _EMIT_ORDER]
    blocks = [f"## {n}\n{chosen[n]}" for n in ordered]
    if preamble:
        blocks.insert(0, preamble)
    return "\n\n".join(blocks)


def index_text(markdown, title="", limit=1200):
    """The text an embedding should represent this concept by.

    `build_dense_index` was embedding `title + body[:512]`. Measured on a real
    document, 512 characters covers Metadata, Learning Objectives and part of
    Prerequisites — not one word of the explanation. Worse, Metadata carries a
    `- **Path**: Course > Module > Unit > Lesson` line, so every concept in a
    course embedded to nearly the same vector and the nearest neighbour of any
    query was whichever concept sat in the biggest lesson. This selects the
    sections that actually say what the concept IS.
    """
    _pre, sections = split_sections(markdown)
    parts = [title.strip()] if title else []
    for name in ("Core Explanation", "Key Facts", "Learning Objectives",
                 "Real-World Examples"):
        body = sections.get(name, "").strip()
        if body:
            parts.append(body)
        if sum(len(p) for p in parts) >= limit:
            break
    if len(parts) <= 1:
        # Nothing substantive parsed (stub or malformed) — fall back to the raw
        # body so the concept is at least indexed by something.
        parts.append((markdown or "").strip())
    return " ".join(parts)[:limit]
