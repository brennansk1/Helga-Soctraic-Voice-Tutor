"""An externally authored degree must clear the bar a local one clears.

`validate` catches what makes a programme UNTEACHABLE — cycles, prerequisites
that do not resolve or do not come earlier. It says nothing about whether the
result is SHAPED like a degree, and /api/program refuses a locally planned
programme on exactly those extra grounds. The external route ran only
`validate`, so the more capable model was held to the lower bar.
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_the_route_runs_the_same_shape_gate():
    src = _read("services", "rag", "pipeline_api.py")
    i = src.find("def create_program_plan")
    assert i > 0
    body = src[i:i + 6000]
    assert "degree_quality import assess" in body, \
        "an external degree never faces the shape gate"
    assert "not_degree_shaped" in body


def test_the_gate_can_be_imported_where_the_route_runs():
    """It lives in tools/, which was not mounted into the rag container — so
    the gate would have been silently skipped in production while passing in
    a test run from the repo root."""
    compose = _read("docker-compose.yml")
    i = compose.find("./services/rag:/app/services/rag")
    assert i > 0
    assert "./tools:/app/tools" in compose[i:i + 400], \
        "tools/ is not mounted into rag-engine; the gate cannot run there"


def test_the_term_count_is_stored_not_just_returned():
    """Both the term-balance and capstone checks read plan["terms"]. With it
    absent, term_balance declined to run and a correctly placed capstone was
    reported misplaced because term 4 != 0."""
    src = _read("services", "rag", "pipeline_api.py")
    i = src.find('"authored_by": data.get("model") or AUTHOR_EXTERNAL')
    assert i > 0
    assert '"terms"' in src[i:i + 700], "the plan is stored without its term count"


@pytest.mark.parametrize("plan,expect_shaped", [
    # Even terms, distinct prerequisite sets, capstone last.
    ({"terms": 2, "courses": [
        {"title": "Composition", "term": 1, "slot": "gen_ed", "requires": []},
        {"title": "Algebra", "term": 1, "slot": "gen_ed", "requires": []},
        {"title": "Statistics", "term": 2, "slot": "core", "requires": ["Algebra"]},
        {"title": "Capstone Seminar", "term": 2, "slot": "capstone",
         "requires": ["Composition"]},
     ]}, True),
    # The capstone is not at the end.
    ({"terms": 2, "courses": [
        {"title": "Capstone Seminar", "term": 1, "slot": "capstone", "requires": []},
        {"title": "Composition", "term": 1, "slot": "gen_ed", "requires": []},
        {"title": "Algebra", "term": 2, "slot": "gen_ed", "requires": []},
        {"title": "Statistics", "term": 2, "slot": "core", "requires": ["Composition"]},
     ]}, False),
])
def test_the_gate_actually_discriminates(plan, expect_shaped):
    from tools.degree_quality import assess
    got = assess(plan)["verdict"] == "DEGREE_SHAPED"
    assert got is expect_shaped, assess(plan)["failed"]


# ---------------------------------------------------------------------------
# teachable_count MUST COUNT TEACHABILITY, NOT FILES.
#
# The course list published this field with a comment saying it counts
# "Concepts the TUTOR CAN TEACH FROM, by the same check the audit gate uses
# (course_audit.is_teachable) — not files on disk", and returned
# hydrated_count, which is files on disk. count_teachable() was written for
# that line and had NO callers anywhere.
#
# Measured: Advanced SQL reported 83 with 74 teachable — nine concepts of
# 1,100-1,700 words each carry no "## Core Explanation", so no lesson can be
# run from them, and the course is `ready`. The gate misses it by design: it
# blocks only when more than half a course is affected.
# ---------------------------------------------------------------------------

def test_the_course_list_counts_teachability_not_written_files():
    import inspect
    from services.rag import librarian
    src = inspect.getsource(librarian)
    assert '"teachable_count": course.get("hydrated_count")' not in src, (
        "teachable_count is back to counting written files; a course with "
        "content under the wrong headings will overstate what can be taught")
    assert "count_teachable" in src, (
        "the course list no longer uses course_audit.count_teachable")


def test_count_teachable_has_a_caller():
    """It had none. A checker nothing calls cannot catch anything."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "services"
    callers = []
    for py in root.rglob("*.py"):
        if py.name == "course_audit.py":
            continue                     # its own definition
        if "count_teachable(" in py.read_text(encoding="utf-8", errors="replace"):
            callers.append(py.name)
    assert callers, "count_teachable is dead code again"


def test_a_concept_without_core_explanation_is_not_teachable():
    """The specific shape found in Advanced SQL: plenty of words, plenty of
    sections, and not the one the tutor reads first.

    Sections must also carry MIN_SECTION_WORDS of body — a heading with a
    sentence under it is not a lesson — so the fixture is written at that
    length rather than as placeholders.
    """
    from services.core.course_audit import is_teachable

    filler = ("The planner keeps per-column statistics and uses them to "
              "estimate how many rows a predicate will match, which is what "
              "decides between a sequential scan and an index scan for any "
              "given query shape on this table today.")
    md = (
        "# Extended Statistics\n\n"
        "## Metadata\nx\n\n"
        f"## Misconceptions\n- **Belief**: {filler}\n  **Correction**: {filler}\n\n"
        f"## Analogies\n- **Simple**: {filler}\n\n"
        f"## Governing Result\n{filler}\n"
    )
    assert not is_teachable(md), \
        "a concept with no Core Explanation must not count as teachable"

    fixed = md.replace("## Governing Result",
                       f"## Core Explanation\n{filler}\n\n## Governing Result")
    assert is_teachable(fixed), \
        "adding the section the tutor reads should make it teachable"
