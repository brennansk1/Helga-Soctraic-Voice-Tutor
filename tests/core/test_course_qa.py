"""The ledger checks, promoted out of a manual tool.

check_depth had NEVER RUN. It read `checked`/`total`/`missed`; the builder
writes `concepts_total`/`concepts_missing_contract`/`met_pct`. Every call
returned "depth_contract recorded no totals" on courses whose depth_contract
was fully populated, and the tool printed CONTENT_READY for a course failing
depth on 20 of 33 concepts.

It was dead a second way underneath: the fallback `d.get("missed") or
d.get("failures")` yields a LIST, so the arithmetic would have raised
TypeError had the first lookup ever succeeded.

SIZES IN THIS PIPELINE ARE RANGES. Domain packs express shape as (min, max)
bands and structures are judged by the share of items inside the band, never
against an exact number. Nothing here may assume a scalar arrived.
"""
import pytest

from services.core.course_qa import check_depth, _as_count, _count_concepts


def _course(depth, modules=None):
    return {"depth_contract": depth, "modules": modules or []}


def _structure(lesson_sizes):
    """A course whose lessons deliberately differ in size."""
    return [{"units": [{"lessons": [
        {"concepts": [{"uid": f"con_{i}{j}"} for j in range(n)]}
        for i, n in enumerate(lesson_sizes)]}]}]


# --- the bug that made it dead ---------------------------------------------

def test_reads_the_keys_the_builder_actually_writes():
    r = check_depth(_course({
        "concepts_total": 33, "concepts_missing_contract": 20, "met_pct": 39.4}))
    assert r["checked"] is True, "the check is dead again"
    assert r["concepts"] == 33 and r["passed"] == 13
    assert r["ok"] is False


def test_a_failures_list_is_counted_not_subtracted():
    """The second death: `failures` is a list, and total - list is TypeError."""
    r = check_depth(_course({"concepts_total": 10,
                             "failures": [{"t": "a"}, {"t": "b"}]}))
    assert r["checked"] is True
    assert r["passed"] == 8


def test_legacy_key_names_still_read():
    r = check_depth(_course({"total": 10, "missed": 1}))
    assert r["checked"] is True and r["passed"] == 9


def test_no_contract_is_unmeasured_never_a_pass():
    r = check_depth(_course(None))
    assert r["checked"] is False and "reason" in r
    assert "ok" not in r, "an unmeasured check must not carry a verdict"


# --- ranges, not numbers ----------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (12, 12),
    ((2, 4), 3),          # a band collapses to its midpoint
    ([2, 8], 5),
    (None, 0),
    ("many", 0),          # never invent a denominator
    ((), 0),
])
def test_counts_tolerate_bands(value, expected):
    assert _as_count(value) == expected


def test_depth_survives_a_band_where_a_count_was_expected():
    r = check_depth(_course({"concepts_total": (30, 36),
                             "concepts_missing_contract": 6}))
    assert r["checked"] is True
    assert r["concepts"] == 33      # midpoint of the band
    assert r["passed"] == 27


# --- uneven structures ------------------------------------------------------

def test_concept_count_walks_uneven_lessons():
    """2-4 concepts per lesson is a BAND. Counting must not assume uniformity."""
    assert _count_concepts({"modules": _structure([2, 3, 4, 2])}) == 11


def test_coverage_uses_the_real_course_size():
    """A resumed build records its own segment, not the course.

    SQL carries concepts_total=14 for a 95-concept course, so this reported
    "14 of 14 passed, 100%" — true of the segment and read as true of the
    course.
    """
    course = _course({"concepts_total": 4, "concepts_missing_contract": 0},
                     modules=_structure([2, 3, 4, 2]))
    r = check_depth(course)
    assert r["course_concepts"] == 11
    assert r["partial_run"] is True
    assert r["coverage"] == pytest.approx(4 / 11, abs=0.01)


def test_a_full_run_is_not_marked_partial():
    course = _course({"concepts_total": 11, "concepts_missing_contract": 0},
                     modules=_structure([2, 3, 4, 2]))
    r = check_depth(course)
    assert "coverage" not in r
    assert r["share"] == 1.0 and r["ok"] is True
