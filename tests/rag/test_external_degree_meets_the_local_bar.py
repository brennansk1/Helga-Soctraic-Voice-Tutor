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
