"""What must never reach a learner, checked before the concept is stored.

The depth contract asks whether a concept is DEEP ENOUGH — word count, a
definition, a worked example, a source. A content audit on 2026-08-25 found
both shipped courses passing it while carrying things no learner should ever
see, because none of them is a depth question:

  * the model's own deliberation, left in the taught text —
    "Wait, let's verify PostgreSQL's default for DESC. ... No. Let's check the
    docs carefully." Four rounds of visible self-argument, ending on the wrong
    answer, in a concept that met its contract.
  * a `## Core Explanation` reading "NULLs in Recursive CTEs is a key concept
    in sql." — a stub the pipeline injected itself when the model omitted the
    heading, which then passed every structural check and taught nothing. Six
    of nine concepts in one course.
  * "**Belief**: None identified. **Correction**: N/A" — a misconception
    section with no misconception in it.
  * "Grounding unavailable. The research service could not be reached..." — a
    true statement about the BUILD, printed in the middle of a lesson.
  * "Part 2" in the curriculum path, from the lesson splitter.

Each is cheap to detect and expensive to leave in. These run at the same point
as the depth contract, so a failure feeds the regeneration loop that already
exists rather than needing a new one — and when regeneration cannot fix it,
the concept is recorded as failing rather than stored as if it were fine.

Every check returns a sentence naming what is wrong and where, because the
string is fed back to the model as the instruction for the retry.
"""
import re

# --- 1. the model thinking out loud -----------------------------------------
#
# Anchored to sentence starts and to the emphasis the model uses when it
# corrects itself. Deliberately NOT a bare search for "wait" or "actually":
# "wait for the lock to be released" and "actually evaluated at run time" are
# ordinary technical prose and must pass.
_DELIBERATION = re.compile(
    r"(?:^|[.!?]\s+|\*)\s*(?:"
    r"wait\s*[,.]|"
    r"hold on\b|"
    r"let'?s (?:re-?)?(?:verify|check|re-?read|reconsider|think)|"
    r"let me (?:re-?)?(?:verify|check|reconsider|think|correct)|"
    r"no[,.]\s+actually|"
    r"actually[,.]\s+(?:no|wait)|"
    r"i (?:was|am) (?:wrong|mistaken)|"
    r"scratch that|"
    r"on second thought"
    r")",
    re.IGNORECASE | re.MULTILINE)

# The audit's other tell: a correction/verification label used mid-lesson,
# e.g. "*Correction:* Wait, let's verify PostgreSQL's default for DESC."
#
# "**Correction**:" IS THE TEMPLATE. The Misconceptions section is specified as
# "- **Belief**: ... **Correction**: ..." and every well-formed concept
# contains it. A first version of this pattern matched that and would have
# rejected 95 of 95 concepts in a finished course — a guard that fails
# everything is indistinguishable from no guard, so the Misconceptions section
# is excluded before this runs, and the label is required to be ITALIC
# (single asterisk), which is the form the model uses when arguing with
# itself, not the bold form the template asks for.
_SELF_CORRECTION_LABEL = re.compile(
    r"(?<!\*)\*(?!\*)\s*(?:correction|verification|re-?check|revised)\s*[:.]?\s*\*(?!\*)",
    re.IGNORECASE)

# --- 2. stubs the pipeline or the model produced ----------------------------

_STUB_SENTENCES = (
    re.compile(r"^\s*(.{3,80}?)\s+is a key concept in\s+.{2,80}\.\s*$",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Student should demonstrate understanding of\b",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Examples of\s+.{2,80}\s+can be found in everyday applications\.",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*-?\s*\*?\*?Belief\*?\*?\s*[:.]?\s*None identified",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*-?\s*See further reading on\b", re.IGNORECASE | re.MULTILINE),
)

_PLACEHOLDER = re.compile(
    r"\bLorem ipsum\b|\bTODO\b|\bTBD\b|\[Hydration failed\]|"
    # The hydrator's own failure text is "Content for X is currently
    # unavailable", so the subject sits between "content" and "unavailable".
    r"\bcontent\b[^.\n]{0,60}?\b(?:is )?(?:currently )?unavailable\b",
    re.IGNORECASE)

# --- 3. build-time apology printed as lesson text ----------------------------

_BUILD_APOLOGY = re.compile(
    r"\*\*Grounding unavailable\.?\*\*|"
    r"the research service could not be reached",
    re.IGNORECASE)

# --- 4. splitter artefacts in the path ---------------------------------------

_SPLIT_ARTEFACT = re.compile(r"\bPart\s+\d+\b(?=\s*(?:>|$|\n))", re.IGNORECASE)

# A Core Explanation shorter than this is not an explanation. The stub the
# pipeline used to inject is nine words; a real one at the lowest mastery is
# well over a hundred.
MIN_CORE_EXPLANATION_WORDS = 40


def _section_body(markdown, heading):
    """The text under `heading`, up to the next heading of any level."""
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^#{{1,3}}\s|\Z)",
                  markdown or "", re.MULTILINE | re.DOTALL)
    return (m.group(1) if m else "").strip()


def _quote(match, body, width=90):
    """The offending text, trimmed, so the retry prompt can point at it."""
    start = max(0, match.start() - 20)
    return " ".join(body[start:match.end() + width].split())[:140]


def inspect(markdown, title="", course_title=""):
    """Return a list of problems, empty if the body is fit to store.

    Phrased as instructions, because the caller feeds them straight back to
    the model as the correction for the next attempt.
    """
    body = markdown or ""
    problems = []

    m = _DELIBERATION.search(body)
    if m:
        problems.append(
            "the text contains your own deliberation — remove it and state the "
            f"conclusion only (found: \"{_quote(m, body)}\")")

    # Outside the Misconceptions section, where "**Correction**:" belongs.
    outside_misconceptions = re.sub(
        r"^##\s+Misconceptions\s*$.*?(?=^#{1,3}\s|\Z)", "", body,
        flags=re.MULTILINE | re.DOTALL)
    m = _SELF_CORRECTION_LABEL.search(outside_misconceptions)
    if m:
        problems.append(
            "the text contains a correction label mid-lesson — decide first, "
            f"then write the settled explanation (found: "
            f"\"{_quote(m, outside_misconceptions)}\")")

    for pat in _STUB_SENTENCES:
        m = pat.search(body)
        if m:
            problems.append(
                "a section is filled with placeholder wording rather than "
                f"content (found: \"{_quote(m, body)}\")")
            break

    m = _PLACEHOLDER.search(body)
    if m:
        problems.append(
            f"the text contains a placeholder (found: \"{_quote(m, body)}\")")

    m = _BUILD_APOLOGY.search(body)
    if m:
        problems.append(
            "the text explains a problem with the BUILD to the learner — that "
            "belongs in the course metadata, not in the lesson")

    m = _SPLIT_ARTEFACT.search(body)
    if m:
        problems.append(
            "the curriculum path contains a splitter artefact like \"Part 2\" — "
            "name the section by what it teaches")

    core = _section_body(body, "Core Explanation")
    if core and len(core.split()) < MIN_CORE_EXPLANATION_WORDS:
        problems.append(
            f"## Core Explanation is only {len(core.split())} words — it is the "
            f"section the tutor reads as the explanation and must carry the "
            f"actual teaching")

    return problems


def is_clean(markdown, title="", course_title=""):
    return not inspect(markdown, title, course_title)
