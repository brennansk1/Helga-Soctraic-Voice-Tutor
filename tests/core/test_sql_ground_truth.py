"""The verifier must catch the audit's errors and clear correct prose.

The second half matters as much as the first. Three separate attempts at these
patterns flagged sentences that were RIGHT — "PostgreSQL defaults to NULLS LAST
for ASC and NULLS FIRST for DESC" among them. A checker that convicts correct
content does not merely waste a retry; it feeds the model a correction toward a
falsehood, so a false positive here actively makes courses worse than no check
at all. Every known false positive is pinned below as a test.
"""
import pytest

from services.core import sql_ground_truth as gt


TRUTH = {"probes": {
    "nulls_order": {"asc": "last", "desc": "first"},
    "null_equality": "NULL",
    "short_circuit": "unspecified",
    "rank_vs_dense_rank": {"rank": ["1", "1", "3"], "dense_rank": ["1", "1", "2"]},
    "count_star_vs_column": {"star": 3, "column": 2},
}}


@pytest.fixture(autouse=True)
def _measured(monkeypatch):
    monkeypatch.setattr(gt, "load_truth", lambda *a, **k: TRUTH)


# --- the errors that shipped -------------------------------------------------

@pytest.mark.parametrize("text,probe", [
    ("By default, `ORDER BY ... ASC` places NULLs first, treating them as less "
     "than any other value.", "nulls_order"),
    ("Conversely, `ORDER BY ... DESC` places NULLs last.", "nulls_order"),
    ("`ORDER BY NULL` behavior is dialect-specific (e.g., PostgreSQL puts "
     "`NULL`s first in ASC, MySQL may vary).", "nulls_order"),
    ("If `ORDER BY revenue DESC` is used, NULLs will appear last.", "nulls_order"),
    ("Standard SQL guarantees short-circuit evaluation for AND.", "short_circuit"),
    ("consistent with SQL's three-valued logic for equality in set operations, "
     "where `NULL = NULL` is true", "null_equality"),
])
def test_catches_what_shipped(text, probe):
    findings, checked = gt.check_markdown(text)
    assert probe in [f["probe"] for f in findings], f"missed: {text[:60]}"
    assert checked, "reported no probes applied"


# --- and must not convict correct prose --------------------------------------

@pytest.mark.parametrize("text,why", [
    ("PostgreSQL defaults to `NULLS LAST` for `ASC` and `NULLS FIRST` for `DESC`.",
     "correct, states both directions in one sentence"),
    ("Use `ORDER BY x ASC NULLS LAST` to make the default explicit.",
     "correct explicit syntax"),
    ("Under `DESC`, NULLs sort first unless you write `NULLS LAST`.",
     "correct, with an explicit override named"),
    ("- **Belief**: `NULL = NULL` returns TRUE because two nulls look alike.\n"
     "  **Correction**: it evaluates to UNKNOWN.",
     "a misconception section states the falsehood ON PURPOSE"),
    ("`COUNT(*)` counts rows while `COUNT(col)` skips NULLs.",
     "correct distinction"),
    ("Because evaluation order is unspecified, you may not rely on "
     "short-circuiting.", "correct, and contains the trigger words"),
])
def test_leaves_correct_prose_alone(text, why):
    findings, _ = gt.check_markdown(text)
    assert findings == [], f"false positive on {why}: {findings}"


# --- honesty about coverage --------------------------------------------------

def test_no_measurement_reports_nothing_checked(monkeypatch):
    """With no ground truth on disk the answer is "unchecked", never "clean"."""
    monkeypatch.setattr(gt, "load_truth", lambda *a, **k: None)
    findings, checked = gt.check_markdown("`ORDER BY x ASC` places NULLs first.")
    assert findings == []
    assert checked == [], "claimed probes ran with nothing measured"


def test_repeated_claim_reported_once():
    text = ("`ORDER BY ... DESC` places NULLs last. " * 3)
    findings, _ = gt.check_markdown(text)
    assert len(findings) == 1, f"duplicate reports: {findings}"


def test_finding_names_the_engine_answer():
    findings, _ = gt.check_markdown("`ORDER BY ... ASC` places NULLs first.")
    assert "LAST" in findings[0]["engine_says"]
    assert findings[0]["claim"], "no quoted sentence for the retry prompt"


# --- applicability -----------------------------------------------------------

def test_skips_courses_with_no_sql_in_them():
    from services.core.course_builder import _ground_truth_problems
    body = "`ORDER BY ... ASC` places NULLs first."
    assert _ground_truth_problems(body, "The Treaty of Westphalia",
                                  "European History") == []
    assert _ground_truth_problems(body, "Sorting and NULLs", "Advanced SQL")


def test_measured_file_matches_a_real_engine():
    """The checked-in truth must carry provenance, not just values."""
    # Read the file itself — the fixture above replaces load_truth for every
    # other test, and this is the one test that must see the real record.
    import json
    import os
    if not os.path.exists(gt.TRUTH_FILE):
        pytest.skip("no measured truth on disk")
    with open(gt.TRUTH_FILE, encoding="utf-8") as f:
        rec = json.load(f)
    assert "PostgreSQL" in rec["engine"]
    assert rec["measured_at"]
    assert rec["probes"]["nulls_order"] == {"asc": "last", "desc": "first"}


# --- blind spots the REPAIR LOOP found --------------------------------------
#
# Pass 3 was asked to fix a concept and did: it corrected every sentence these
# probes matched, and left two saying exactly the same false thing in wording
# they missed. The audit then reported zero blocking findings on a concept
# still teaching the wrong ordering.
#
# That is the failure mode of any repair loop — it optimises against the
# DETECTOR — and it makes a gap in the detector a laundering mechanism rather
# than merely a miss. These are pinned because the loop will keep finding them.

@pytest.mark.parametrize("text", [
    # No ASC/DESC token at all — the direction is a plain English word.
    "PostgreSQL defaults to `NULLS FIRST` for ascending sorts and `NULLS LAST` "
    "for descending sorts.",
    # One NULL, two position claims. The old pattern needed a NULL before each.
    "In PostgreSQL, NULLs are first in ASC and last in DESC by default.",
    # Found in the corpus only after the patterns widened.
    "PostgreSQL places `NULL`s first in ascending order, but other systems vary.",
])
def test_the_laundered_phrasings_are_caught(text):
    findings, _ = gt.check_markdown(text)
    assert findings, f"a repair could hide a falsehood in this wording: {text}"


@pytest.mark.parametrize("text", [
    "In PostgreSQL, NULLs are last in ASC and first in DESC by default.",
    "PostgreSQL defaults to NULLS LAST for ascending sorts and NULLS FIRST "
    "for descending sorts.",
    # Naming the modifiers is not asserting which is the default.
    "PostgreSQL supports `NULLS FIRST` and `NULLS LAST` clauses, which "
    "override the default behaviour.",
])
def test_widening_did_not_convict_correct_prose(text):
    findings, _ = gt.check_markdown(text)
    assert findings == [], f"false positive after widening: {findings}"


# --- the third vocabulary, also found by the repair loop --------------------
#
# After the patterns were widened once, a repaired concept came back reading
# "ORDER BY treats NULL as the LOWEST value in ascending order (in PostgreSQL)"
# — the same falsehood in a third wording, invisible again. In an ascending
# sort the lowest value comes first and the highest comes last, so magnitude
# words are position claims and have to be read as such.

@pytest.mark.parametrize("text", [
    "`ORDER BY` treats `NULL` as the lowest value in ascending order in PostgreSQL.",
    "NULLs are treated as the smallest value when sorting ascending.",
])
def test_magnitude_wording_is_read_as_position(text):
    findings, _ = gt.check_markdown(text)
    assert findings, f"a falsehood could hide in magnitude wording: {text}"


@pytest.mark.parametrize("text", [
    "`ORDER BY` treats `NULL` as the highest value in ascending order in PostgreSQL.",
    "PostgreSQL sorts NULLs last in ascending order, treating them as greater "
    "than any other value.",
    "NULL values are sorted as if they were the highest possible value in "
    "ascending order.",
])
def test_correct_magnitude_wording_is_left_alone(text):
    findings, _ = gt.check_markdown(text)
    assert findings == [], f"false positive on correct magnitude wording: {findings}"
