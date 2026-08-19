"""Lesson-batched hydration — one LLM call for a lesson's concepts.

WHY
---
Hydration is one call per concept, and a parity course is 104-135 concepts at
~90 s each. The skeleton builder went the other way — a module's whole subtree
in ONE call — and that change cut the number of calls *and* improved coherence,
because the model could see the module as a unit rather than guessing at its
neighbours. The same argument extends here.

WHY A LESSON AND NOT A MODULE
-----------------------------
A lesson is 2-4 concepts; a module is 20-40. Measured on this machine the served
context is 32768 tokens, which must also hold the section template, the research
payload, the ledger context and the OUTPUT. A lesson's worth of documents fits
with room; a module's does not, and the failure mode of overflowing it is
silent truncation — the 4096-token ceiling cost this project a full debugging
cycle before it was found.

WHY IT IS OFF BY DEFAULT
------------------------
`HELGA_LESSON_BATCH=1` to enable. Per-concept hydration is the path with hours
of real builds behind it, every quality gate (depth contract, fact-check,
redundancy correction) is written against a single document, and batching
changes the unit those gates operate on.

The honest position is that this is **built and tested but not yet proven on a
real build**, which is exactly the state the skeleton builder's one-shot path
was in before it was measured and promoted. Enable it, measure calls and
coherence against the per-concept path, and flip the default only on evidence.

WHAT IT DOES NOT CHANGE
-----------------------
Every downstream gate still runs per concept. Batching decides how many
documents come back from one call, not how they are judged — so a batch whose
third concept is thin still gets that concept regenerated against its named
deficiency, alone.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# A lesson beyond this many concepts is split into chunks. The cap exists
# because output tokens, not input, is what overflows: each document carries the
# full section template's worth of headings.
MAX_PER_BATCH = 4


def enabled():
    return os.getenv("HELGA_LESSON_BATCH", "0").lower() in ("1", "true", "yes")


def group_by_lesson(concept_list, lesson_of):
    """Split a flat concept list into consecutive same-lesson runs.

    Consecutive, not grouped-by-key: teaching order is what makes the ledger
    complete for everything prior, and reordering concepts to gather a lesson
    would break that guarantee for the sake of a slightly larger batch.
    """
    batches, current, current_lesson = [], [], None
    for entry in concept_list:
        uid = entry[0]
        lesson = lesson_of(uid)
        if current and (lesson != current_lesson or len(current) >= MAX_PER_BATCH):
            batches.append(current)
            current = []
        current_lesson = lesson
        current.append(entry)
    if current:
        batches.append(current)
    return batches


def batch_prompt(concepts, course_title, section_template, shared_context=""):
    """One prompt asking for several concepts as delimited documents.

    Invariant material first, for the same prefix-caching reason the
    per-concept prompt was inverted: the template is identical across every
    batch in a course and should be prefilled once, not once per lesson.
    """
    titles = [c["title"] for c in concepts]
    body = "\n".join(
        f"\n<<<CONCEPT {i}: {c['title']}>>>\n"
        f"Objectives: {', '.join(c.get('objectives') or []) or 'n/a'}\n"
        f"Bloom: {c.get('bloom_level', 3)}\n"
        for i, c in enumerate(concepts, 1))
    return f"""### OUTPUT FORMAT
Write {len(concepts)} complete documents, one per concept, each using EXACTLY
the section structure below. Separate them with a line containing only:

===CONCEPT-BREAK===

Do not write anything before the first document or after the last.

{section_template}

### THIS LESSON — course: {course_title}
These {len(concepts)} concepts are taught together, in this order:
{chr(10).join(f'  {i}. {t}' for i, t in enumerate(titles, 1))}

Each must teach something the others do not. Where one depends on another,
refer to it by name rather than explaining it twice.
{shared_context}
{body}
Now write the {len(concepts)} documents, separated by ===CONCEPT-BREAK===.
"""


def split_batch(text, expected):
    """Split a batched response back into documents.

    Returns a list of `expected` length, with None where a document is missing.
    None rather than a stub, deliberately: the caller falls back to hydrating
    that concept alone, and a stub would look like content and silently ship.
    """
    if not text:
        return [None] * expected
    parts = [p.strip() for p in re.split(r"^\s*=+CONCEPT-BREAK=+\s*$", text,
                                         flags=re.MULTILINE) if p.strip()]
    if len(parts) != expected:
        # A model that emitted the marker inconsistently may still have written
        # the right number of documents; try the heading that every document
        # must start with before giving up.
        alt = [p.strip() for p in re.split(r"(?=^##\s*Mastery Criteria)", text,
                                           flags=re.MULTILINE) if p.strip()]
        alt = [p for p in alt if "## Mastery Criteria" in p]
        if len(alt) == expected:
            parts = alt
    if len(parts) != expected:
        logger.warning(f"[BATCH] expected {expected} documents, parsed "
                       f"{len(parts)} — falling back to per-concept")
        return [None] * expected
    # Strip any leftover <<<CONCEPT n:>>> marker the model echoed back.
    return [re.sub(r"^<<<CONCEPT \d+:[^>]*>>>\s*", "", p).strip() for p in parts]


def usable(doc, min_words=80):
    """Is a split document real content, or an apology?

    A batch can come back with the first document complete and the rest
    truncated, which is the failure mode a single-document check would miss.
    """
    if not doc or "## Mastery Criteria" not in doc:
        return False
    return len(doc.split()) >= min_words
