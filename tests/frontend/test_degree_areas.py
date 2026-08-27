"""Requirement areas on the degree page.

A prerequisite chain used to be filed wholesale under the slot of its FIRST
course, on the stated assumption that a chain never crossed a requirement area.
Real plans cross constantly: in the Data Analytics associate, College Algebra
(general education) gates Introduction to Statistics, which gates Inferential
Statistics, which gates both electives and the capstone. Union-find put all ten
in one component, so ten of twelve courses — electives and capstone included —
rendered under "General education", and the degree appeared to have no major
requirements at all.

These tests read the shipped JS rather than executing it: the guarantee worth
holding is that the grouping is driven by each course's own slot, and that the
vocabulary covers every slot the planner emits.
"""
import re
import sqlite3


def _block(source, name, open_c="{", close_c="}"):
    """The body of `name = { ... }`, matched by counting braces.

    A non-greedy regex across newlines runs to the last brace in the file, which
    made this test read the whole module and compare nonsense."""
    m = re.search(re.escape(name) + r"\s*=\s*" + re.escape(open_c), source)
    if not m:
        return ""
    i = m.end() - 1
    depth = 0
    for j in range(i, len(source)):
        if source[j] == open_c:
            depth += 1
        elif source[j] == close_c:
            depth -= 1
            if depth == 0:
                return source[i + 1:j]
    return ""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEGREE_JS = ROOT / "services" / "web-ui" / "static" / "js" / "degree.js"
DB = ROOT / "data" / "helga.db"


def _strip_comments(text):
    """Comments must go before any of this parses. "nothing here is optional:"
    inside a block comment looked exactly like an AREA_LABELS key and sent this
    test hunting a bug that was not there."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"(?<![:/\w])//[^\n]*", " ", text)


@pytest.fixture(scope="module")
def source():
    return _strip_comments(DEGREE_JS.read_text(encoding="utf-8", errors="replace"))


def test_courses_are_filed_by_their_own_slot(source):
    """The regression in one line: filing a whole track by t[0].slot."""
    assert "var slot = t[0].slot" not in source, (
        "a sequence is being filed under its first course's requirement area; "
        "a chain that crosses areas drags every later course with it"
    )
    assert re.search(r"bySlot\[slot\]\s*=\s*bySlot\[slot\]", source), \
        "courses are no longer partitioned by their own slot before grouping"


def test_sequences_are_found_within_an_area_not_across_the_plan(source):
    """tracks() must be able to run over a subset, or areas cannot have their
    own internal sequences."""
    assert re.search(r"function tracks\(plan,\s*courses\)", source), \
        "tracks() no longer accepts a course subset"
    assert "inSet[r]" in source, (
        "prerequisite edges are not restricted to the area being grouped, so "
        "the numbering inside a block counts courses it does not show"
    )


def test_every_slot_the_planner_emits_has_a_label(source):
    """An unlabelled slot renders as a raw key like 'gen_ed' at the learner."""
    labels = _block(source, "AREA_LABELS")
    assert labels, "AREA_LABELS not found"
    known = set(re.findall(r"^\s*(\w+)\s*:", labels, re.M))

    if not DB.exists():
        pytest.skip("no local database to read planner slots from")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        emitted = {r[0] for r in con.execute(
            "SELECT DISTINCT slot FROM program_courses WHERE slot IS NOT NULL")}
    except sqlite3.OperationalError:
        pytest.skip("program_courses table not present")
    finally:
        con.close()

    # A numeric slot is a malformed plan, not a requirement area; the renderer
    # humanises unknown slots rather than dropping the course, which is the
    # right fallback, but a REAL area must have a written label.
    real = {s for s in emitted if s and not s.isdigit()}
    missing = sorted(real - known)
    assert not missing, f"planner emits slots with no learner-facing label: {missing}"


def test_area_order_covers_the_labelled_areas(source):
    labels = _block(source, "AREA_LABELS")
    order = _block(source, "AREA_ORDER", "[", "]")
    assert labels and order
    known = set(re.findall(r"^\s*(\w+)\s*:", labels, re.M))
    ordered = set(re.findall(r"['\"](\w+)['\"]", order))
    assert known == ordered, (
        f"AREA_LABELS and AREA_ORDER disagree; unordered areas fall to the end "
        f"in an arbitrary place: {known ^ ordered}"
    )


def test_gate_lists_read_as_prose(source):
    """The capstone here gates on three courses; "A and B and C" is a stutter."""
    assert 'c._gates.join(" and ")' not in source
    assert "andList(c._gates)" in source
