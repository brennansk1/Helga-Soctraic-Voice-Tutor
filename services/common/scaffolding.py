"""Switches for the small-model scaffolding.

WHY THIS EXISTS
---------------
Much of the course builder was written against a small model in a 4,096-token
window. Two things have changed: the window is now 16,384 (verified in the
runner's own command line, `-c 16384`), and the candidate models are 4B-24B
instruct models rather than a 1.5B.

A lot of that scaffolding is now actively harmful. Measured examples:

  * grounding reaching the model per concept is ~600 tokens of a 16,384-token
    window — the prompt uses 29% of the context and the rest sits idle;
  * when a model omits a section the harness writes a placeholder, so a
    45-word response becomes a structurally complete document and a weak model
    scores like a strong one;
  * the title-dedup threshold deletes `World War I` against `World War II`
    (ratio 1.00), `Left Ventricle` against `Right Ventricle` (0.50), and
    reports it to the learner as an achievement.

NOTHING IS DELETED — every helper is still here and still reachable. What
changed is the DEFAULT: lean is now on, because the two premises the
scaffolding was built for (a small model, a 4k window) are both gone.

    <unset>                      lean — the new default
    HELGA_LEAN=0                 the historical behaviour, wholesale
    HELGA_STUB_SECTIONS=1        restore ONE helper, leave the rest lean
    git diff pre-lean-overhaul   the pre-overhaul tree, tagged

An individual switch always beats the group setting, in both directions.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
The depth contract, fact-checking, level calibration, syllabus coverage and
cross-concept deduplication are NOT scaffolding. They exist because models
produce confident wrong content and cannot see across concepts — a bigger
model makes them more necessary, not less, because it is more persuasive when
wrong. None of them is switchable from here.
"""

import logging
import os

logger = logging.getLogger(__name__)


def _flag(name, default):
    """Read a switch. `HELGA_LEAN` sets the group default; a specific
    variable always wins so one helper can be restored in isolation."""
    raw = os.getenv(name)
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    # LEAN IS NOW THE DEFAULT. The pipeline was tuned for a small model in a
    # 4,096-token window; both of those premises are gone. Set HELGA_LEAN=0 to
    # restore the historical behaviour wholesale, or set an individual switch
    # to restore just that helper. The pre-overhaul tree is also tagged
    # `pre-lean-overhaul` in git.
    if os.getenv("HELGA_LEAN", "1").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return default


# --- input starvation --------------------------------------------------------
# The caps below were sized for a 4k window, where 5,000 chars of source was
# ~30% of everything available and trimming was necessary. At 16k they discard
# material the model now has room to read. Note the historical `[:3000]` never
# actually bit: `_preprocess_research` already caps `remainder` at 1,500, so
# 1,500 was the real ceiling.
SOURCE_MATERIAL_CHARS = int(os.getenv("HELGA_SOURCE_CHARS", "0")) or (
    3000 if _flag("HELGA_TRUNCATE_SOURCE", True) else 24000)
RAW_TEXT_CHARS = int(os.getenv("HELGA_RAW_TEXT_CHARS", "0")) or (
    5000 if _flag("HELGA_TRUNCATE_SOURCE", True) else 32000)
RESEARCH_REMAINDER_CHARS = int(os.getenv("HELGA_REMAINDER_CHARS", "0")) or (
    1500 if _flag("HELGA_TRUNCATE_SOURCE", True) else 20000)

# --- output repair that masks failure ---------------------------------------
# When the model omits a required section the harness appends a placeholder and
# continues. That makes a document pass structural checks it did not earn, and
# it makes a weak model indistinguishable from a strong one: over one 6-concept
# gate run, Ministral needed 36 injections and Mistral-Small needed 1, yet both
# produced "complete" documents.
STUB_MISSING_SECTIONS = _flag("HELGA_STUB_SECTIONS", True)

# Fabricated units/lessons/concepts when the model returns nothing —
# "{lesson} Part 1..N" placeholders that are then hydrated into full documents
# about nothing. A lesson with fewer concepts is honest; a lesson padded with
# stubs is not.
FABRICATE_MISSING_NODES = _flag("HELGA_FABRICATE_NODES", True)

# --- dedup aggression --------------------------------------------------------
# `_word_overlap_ratio` divides by the SMALLER token set, so a short title that
# is a subset of a longer one scores 1.00. Verified deletions at the historical
# 0.4 threshold:
#     1.00  World War I / World War II          (the tokenizer drops "I")
#     1.00  Type I Error / Type II Error
#     0.67  Newton's First Law / Newton's Second Law
#     0.50  Left Ventricle / Right Ventricle
#     0.50  Supply Curve / Demand Curve
# Parallel naming is the commonest convention in physics, law, history,
# statistics and anatomy. Raising the threshold keeps genuine near-duplicates
# (a rephrased title still scores very high) while sparing these.
TITLE_DEDUP_RATIO = float(os.getenv("HELGA_DEDUP_RATIO", "0") or 0) or (
    0.4 if _flag("HELGA_AGGRESSIVE_DEDUP", True) else 0.85)

# --- token budgets sized for 4k ---------------------------------------------
# 800 tokens for up to 11 module objects at ~60 tokens each is at or over the
# ceiling. Truncation then trips JSON repair, produces too few modules, fails
# validation and triggers a full retry whose correction header blames the model
# for our own limit.
MODULE_JSON_TOKENS = int(os.getenv("HELGA_MODULE_TOKENS", "0")) or (
    800 if _flag("HELGA_SMALL_BUDGETS", True) else 2400)


def describe():
    """One line per switch, for the build log — so a run's configuration is
    recorded next to its results rather than inferred later."""
    return (
        f"scaffolding: source={SOURCE_MATERIAL_CHARS} raw={RAW_TEXT_CHARS} "
        f"remainder={RESEARCH_REMAINDER_CHARS} stub_sections={STUB_MISSING_SECTIONS} "
        f"fabricate_nodes={FABRICATE_MISSING_NODES} dedup_ratio={TITLE_DEDUP_RATIO} "
        f"module_tokens={MODULE_JSON_TOKENS}")
