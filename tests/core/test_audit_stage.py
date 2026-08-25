"""Stage 4 as the pipeline runs it.

The audit reads the FINISHED course rather than one concept as it is written,
which is the only position from which the cross-concept defects are visible.
Its first run on the live library found "Reading a Query Plan" — a course
marked ready, badged "Passed its build checks", and sitting at 25% progress —
with all four concepts carrying nothing but a title and a worked example. No
Core Explanation, no Misconceptions, no Analogies: none of the sections the
tutor reads when teaching.

Every gate before this one passed it, because every gate before this one asks
about a concept at the moment it is generated, and that course was written
through a path that never met them.

A REPORT THAT CONTRADICTS ITSELF IS THE FAILURE THIS STAGE EXISTS TO CATCH,
so the counting invariants below are tests rather than assumptions.
"""
import pytest

from services.core.course_audit import audit_course


def _course(concepts, title="Test Course", domain="computer_science"):
    return {
        "title": title, "teaching_domain": domain, "mastery_level": 3,
        "modules": [{"title": "M1", "units": [{"title": "U1", "lessons": [
            {"title": "L1", "concepts": concepts}]}]}],
    }


GOOD = """# Window Functions

## Core Explanation
A window function computes a value across a set of rows related to the current
row, without collapsing them the way `GROUP BY` does. The frame is defined by
the `OVER()` clause, and `ROWS BETWEEN` controls which rows are visible to the
computation at each step. This is what separates it from an aggregate.

## Worked Example
`SELECT id, SUM(x) OVER (ORDER BY id ROWS BETWEEN UNBOUNDED PRECEDING AND
CURRENT ROW) FROM t;` produces a running total over the ordered rows.

## Misconceptions
- **Belief**: `OVER()` is optional and a window function works without it.
  **Correction**: the `OVER()` clause is what makes the function a window
  function at all; without it PostgreSQL parses `SUM(x)` as a plain aggregate
  and collapses the rows.
- **Belief**: the frame defaults to the whole partition. **Correction**: with
  an `ORDER BY` present the default frame is `RANGE UNBOUNDED PRECEDING AND
  CURRENT ROW`, which is why a running total appears rather than a constant.

## Analogies
A window function is a moving spotlight: the whole stage stays lit, but the
computation only sees the part the beam covers at that moment.

## Sources
- [PostgreSQL: Window Functions](https://www.postgresql.org/docs/16/tutorial-window.html)
"""

MISSING_SECTIONS = """# What a Query Plan Is

## Worked Example
`EXPLAIN SELECT * FROM t;` prints the plan the planner chose for that query,
one node per line, with estimated cost and row count attached to each.
"""


def test_a_concept_missing_the_tutor_sections_is_reported():
    """The live defect: title and worked example, nothing else."""
    r = audit_course(_course([{"uid": "con_1", "title": "What a Query Plan Is"}]),
                     {"con_1": MISSING_SECTIONS})
    checks = {f["check"] for f in r["findings"]}
    assert "tutor_sections" in checks
    missing = " ".join(f["detail"] for f in r["findings"])
    for heading in ("Core Explanation", "Misconceptions", "Analogies"):
        assert heading in missing, f"{heading} was not reported missing"


def test_a_well_formed_concept_is_not_flagged():
    r = audit_course(_course([{"uid": "con_1", "title": "Window Functions"}]),
                     {"con_1": GOOD},
                     sources_by_uid={"con_1": [{
                         "title": "PostgreSQL: Window Functions",
                         "url": "https://www.postgresql.org/docs/16/x.html",
                         "passage": "A window function performs a calculation "
                                    "across a set of table rows." * 3}]})
    serious = [f for f in r["findings"]
               if f["severity"] in ("blocking", "serious")]
    assert serious == [], f"good content flagged: {serious}"


# --- the counting invariants ------------------------------------------------

def test_folded_findings_still_count_their_concepts():
    """Folding must not zero the affected count.

    A 4-concept course with every concept missing three sections reported
    "0 concepts with findings" beside "serious: 16", because folding moved the
    findings out of the per-concept list and took the concept count with them.
    """
    concepts = [{"uid": f"con_{i}", "title": f"C{i}"} for i in range(4)]
    contents = {f"con_{i}": MISSING_SECTIONS for i in range(4)}
    r = audit_course(_course(concepts), contents)

    assert r["systemic"], "a defect in every concept should fold"
    assert r["concepts_with_findings"] == 4, (
        f"folded findings lost their concepts: "
        f"{r['concepts_with_findings']} with findings but "
        f"{r['by_severity']}")
    assert r["concepts_clean"] == 0


def test_a_concept_with_no_file_is_not_audited_and_says_so():
    """Absent must never read as passed."""
    r = audit_course(_course([{"uid": "con_1", "title": "Missing"}]), {})
    assert r["concepts_audited"] == 0
    assert r["concepts_not_audited"] == 1
    assert any(f["check"] == "missing_content" for f in r["findings"])


def test_severity_counts_match_the_findings_reported():
    concepts = [{"uid": "con_1", "title": "What a Query Plan Is"}]
    r = audit_course(_course(concepts), {"con_1": MISSING_SECTIONS})
    counted = sum(r["by_severity"].values())
    listed = len(r["findings"]) + sum(s["concepts"] for s in r["systemic"])
    assert counted == listed, (
        f"severity total {counted} disagrees with {listed} findings reported")


def test_checks_run_is_reported_so_silence_is_readable():
    """An empty findings list with no checks run means UNCHECKED, not clean."""
    r = audit_course(_course([{"uid": "con_1", "title": "X"}]), {"con_1": GOOD})
    assert r["checks_run"], "no record of what was actually checked"
    assert r["checks_run"].get("tutor_sections") == 1
