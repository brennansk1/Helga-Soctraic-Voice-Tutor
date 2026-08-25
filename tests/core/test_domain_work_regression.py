"""One guard over the domain/grounding work of 2026-08-25.

Every item here was a measured defect fixed this session. They live in five
different modules, so a sprint item touching any one of them can quietly undo
another — which is exactly the pattern the codebase keeps producing. Run this
after each sprint change, not just at the end.

Each assertion names the measurement that justified it, so a future editor can
tell a deliberate change from a regression.
"""
import os
import sys

import pytest

# The research service runs FLAT in its container, so its modules import by
# bare name. Put that directory on the path the same way the container does.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "services", "research"))

from services.core.content_guards import inspect as guard_inspect
from services.core.depth_contract import (DOMAIN_APPLIED, _has, contract_for,
                                          teaching_text, validate_concept)

POSTGRES_DOC = "https://www.postgresql.org/docs/current/functions-window.html"
JOURNAL = "https://doi.org/10.1145/3299869"


# --- the applied-domain contract calibration --------------------------------

def test_computing_is_not_asked_for_a_named_theorem():
    """0 of 16 known-good concepts named one — 4 hand-written, 12 generated."""
    assert "named_result" not in contract_for(4, "Advanced SQL", "computer_science")["required"]


def test_mathematics_still_is():
    assert "named_result" in contract_for(4, "linear algebra", "formal")["required"]


def test_the_rest_of_the_mastery_4_bar_survives():
    req = contract_for(4, "Advanced SQL", "computer_science")["required"]
    for e in ("formal_definition", "worked_example", "derivation_or_proof",
              "primary_source", "any_source"):
        assert e in req, f"{e} was dropped — that is lowering the bar, not calibrating it"
    assert contract_for(4, "Advanced SQL", "computer_science")["word_min"] >= 500


def test_the_applied_set_stays_short_and_explicit():
    assert "computer_science" in DOMAIN_APPLIED
    assert "formal" not in DOMAIN_APPLIED and "narrative" not in DOMAIN_APPLIED
    assert len(DOMAIN_APPLIED) <= 8, "a widening list is how a calibration becomes a loophole"


# --- normative sources ------------------------------------------------------

@pytest.mark.parametrize("domain,expected", [
    ("computer_science", True), ("engineering", True),
    ("formal", False), ("narrative", False),
])
def test_vendor_docs_are_primary_only_in_applied_domains(domain, expected):
    assert _has("primary_source", f"see {POSTGRES_DOC}", domain) is expected


def test_journal_literature_counts_everywhere():
    for d in ("computer_science", "formal", "narrative", None):
        assert _has("primary_source", f"see {JOURNAL}", d)


# --- the word cap measures teaching, not citations --------------------------

def test_the_citation_list_does_not_count_toward_the_cap():
    body = "# T\n\nreal teaching\n\n## Sources\n" + \
           "\n".join(f"- [s{i}](https://e.org/{i})" for i in range(200))
    assert len(teaching_text(body).split()) < 30


def test_a_genuinely_overlong_explanation_still_fails():
    ok, problems, _ = validate_concept("# T\n\n" + ("word " * 2000), 2,
                                       "topic", "computer_science")
    assert any("too long" in p for p in problems)


# --- domain routing ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Partitioning", "Query Optimization and Constraint Design",
    "Software Architecture", "Warehouse Fact Tables",
    "Logical Validation and Null Semantics",
])
def test_computing_never_routes_to_the_humanities(text):
    """'art' is inside 'partitioning'; 'war' inside 'warehouse'."""
    from domain_sources import classify_domains
    assert classify_domains(text, "", "advanced sql") == []


@pytest.mark.parametrize("text,want", [
    ("Impressionist brushwork", "art"), ("The French Revolution", "history"),
    ("Quantum entanglement", "science"), ("Sociological method", "social"),
])
def test_the_real_signals_still_route(text, want):
    from domain_sources import classify_domains
    assert want in classify_domains(text)


# --- relevance judging ------------------------------------------------------

@pytest.fixture()
def research(tmp_path, monkeypatch):
    """research_server makedirs CACHE_DIR at import, defaulting to /app."""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "research_cache"))
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib
    import research_server
    return importlib.reload(research_server)


def test_a_three_letter_subject_is_not_discarded(research):
    """[a-z]{4,} dropped 'sql' from every query, so appending the subject to
    the relevance check added nothing at all for an SQL course."""
    assert "sql" in research._content_terms("Sequential Scan Cost advanced sql")


def test_the_subject_is_required_not_counted(research):
    med = ("Radiation oncology for prostate cancer. The sequential workup "
           "determines cost and scan protocols. ") * 30
    assert not research._is_relevant(
        "Sequential Scan Cost", "Radiation Oncology", med,
        must_include="advanced sql")


# --- content guards ---------------------------------------------------------

def test_model_deliberation_is_still_rejected():
    assert guard_inspect("*Correction:* Wait, let's verify that claim.")


def test_the_misconceptions_template_is_still_accepted():
    """'**Correction**:' IS the specified format; a guard that rejected it
    would fail 95 of 95 concepts."""
    assert not guard_inspect(
        "## Misconceptions\n- **Belief**: x is true.\n  **Correction**: it is not.\n")


# --- what hydration derives from the course ---------------------------------

def test_hydration_reads_mastery_domain_and_brief_from_the_course():
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "services", "core", "course_builder.py"),
              encoding="utf-8") as f:
        src = f.read()
    body = src[src.find("def hydrate(self, course_uid"):][:5000]
    for field in ('course.get("teaching_domain")', 'course.get("mastery")',
                  'course.get("learner_context")'):
        assert field in body, f"hydrate() no longer reads {field} from the course"
