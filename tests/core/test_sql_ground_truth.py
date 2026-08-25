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
