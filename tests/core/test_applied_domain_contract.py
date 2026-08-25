"""An engineering subject proves things differently from mathematics.

Mathematics rests on named theorems. An applied subject rests on a NORMATIVE
SPECIFICATION — the standard, or the implementation's own reference manual.

Measured on 2026-08-25 across 16 concepts known to be good (4 hand-written for
a query-plan course, 12 from the local SQL build): every one had a formal
definition and a worked example, and ZERO named a theorem, lemma or law. That
is not thin content failing a bar; it is a bar describing a different subject.

The bar is not lowered. At mastery 4 an applied concept still owes 500+ words,
a formal definition, a worked example, a derivation, and a primary source.
"""
import pytest

from services.core.depth_contract import (DOMAIN_APPLIED, _has, contract_for,
                                          validate_concept)

POSTGRES = "https://www.postgresql.org/docs/current/functions-window.html"
JOURNAL = "https://doi.org/10.1145/3299869"


def test_an_applied_domain_is_not_asked_to_name_a_theorem():
    req = contract_for(4, "Advanced SQL", "computer_science")["required"]
    assert "named_result" not in req


def test_mathematics_still_is():
    req = contract_for(4, "linear algebra", "formal")["required"]
    assert "named_result" in req, "this fix must not reach maths"


def test_the_rest_of_the_bar_is_untouched():
    req = contract_for(4, "Advanced SQL", "computer_science")["required"]
    for e in ("formal_definition", "worked_example", "derivation_or_proof",
              "primary_source"):
        assert e in req, f"{e} was dropped — that would be lowering the bar"
    assert contract_for(4, "Advanced SQL", "computer_science")["word_min"] >= 500


@pytest.mark.parametrize("domain,expected", [
    ("computer_science", True),
    ("engineering", True),
    ("formal", False),      # maths must not accept a vendor manual as primary
    ("narrative", False),
])
def test_normative_docs_count_as_primary_only_where_they_are(domain, expected):
    assert _has("primary_source", f"see {POSTGRES}", domain) is expected


def test_journal_literature_still_counts_everywhere():
    for domain in ("computer_science", "formal", "narrative", None):
        assert _has("primary_source", f"see {JOURNAL}", domain)


def test_a_thin_applied_concept_still_fails():
    """The calibration must not become a loophole."""
    body = f"# T\n\nSQL is useful. See {POSTGRES}\n"
    ok, problems, _ = validate_concept(body, 4, "Advanced SQL", "computer_science")
    assert not ok
    assert any("too short" in p for p in problems), problems


def test_the_applied_set_is_explicit():
    """A silent membership test over every domain would be a loophole; the
    list is short and named on purpose."""
    assert "computer_science" in DOMAIN_APPLIED
    assert "formal" not in DOMAIN_APPLIED
    assert "narrative" not in DOMAIN_APPLIED


def test_hydration_uses_the_domain_the_course_recorded():
    """The calibration is worthless if the hydrator never sees the domain.

    The skeleton resolves teaching_domain and stores it; hydrate() ignored it
    and re-guessed from the title. infer_domain('advanced sql') is None, so
    the contract fell back to the generic one and demanded a named theorem of
    every concept — the exact requirement just calibrated away for computing.
    Measured: three consecutive concepts failed on named_result while the
    course record said computer_science throughout.
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "services", "core", "course_builder.py"),
              encoding="utf-8") as f:
        src = f.read()
    i = src.find("def hydrate(self, course_uid")
    assert i > 0
    body = src[i:i + 5000]
    assert 'course.get("teaching_domain")' in body, \
        "hydrate() re-guesses the domain instead of reading what was stored"
