"""The judge-free coverage instrument.

Exists because criterion 6's LLM judge returned 0% INADEQUATE on a course whose
module titles literally contained four of the topics it reported missing. This
tool answers the same question with no model in it, so it cannot drift -- and
its result is verifiable by eye.
"""

import json
import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.coverage_check import (  # noqa: E402
    check_coverage, course_title_blob, structural_summary)


def _course(*module_titles, lessons_per_unit=1, concepts_per_lesson=1):
    return {"modules": [
        {"title": t, "units": [
            {"title": f"{t} unit", "lessons": [
                {"title": f"{t} lesson {i}", "concepts": [
                    {"title": f"{t} concept {i}.{j}"}
                    for j in range(concepts_per_lesson)]}
                for i in range(lessons_per_unit)]}]}
        for t in module_titles]}


class TestCoverage(unittest.TestCase):
    def test_topic_present_in_a_title_counts_as_covered(self):
        r = check_coverage(_course("Eigenvalues and Eigenvectors"),
                           {"Eigen": ["eigenvalue"], "Determinants": ["determinant"]})
        assert r["coverage_pct"] == 50
        assert r["missing"] == ["Determinants"]

    def test_punctuation_and_case_are_folded(self):
        """'Gram-Schmidt' in a title must match a 'gram schmidt' reference term,
        or the instrument reports a miss that a human would call a hit."""
        r = check_coverage(_course("Gram-Schmidt Orthogonalization"),
                           {"GS": ["gram schmidt"]})
        assert r["coverage_pct"] == 100

    def test_the_judge_defect_this_tool_exists_to_avoid(self):
        """REGRESSION for the real failure: criterion 6 declared 'Vector Spaces',
        'Basis and Dimension' and 'Linear Maps' missing from a course whose
        module titles were exactly those phrases."""
        course = _course("Vector Spaces and Linear Combinations",
                         "Basis and Dimension",
                         "Matrix-Vector Multiplication and Linear Maps")
        r = check_coverage(course, {
            "Vector spaces": ["vector space"],
            "Basis and dimension": ["basis", "dimension"],
            "Linear maps": ["linear map"]})
        assert r["coverage_pct"] == 100, r["missing"]

    def test_missing_structure_is_not_zero_coverage(self):
        """"We could not look" and "it covers nothing" are different facts. A
        malformed course must report an error, never a confident 0%."""
        r = check_coverage({}, {"Anything": ["x"]})
        assert "error" in r
        assert r.get("coverage_pct") is None or "coverage_pct" not in r

    def test_only_titles_count_not_prose(self):
        """A course that merely mentions a term in generated body text has not
        covered it; a course with a lesson named for it has."""
        course = _course("Unrelated")
        course["modules"][0]["description"] = "we briefly mention eigenvalues"
        r = check_coverage(course, {"Eigen": ["eigenvalue"]})
        assert r["coverage_pct"] == 0


class TestStructuralSummary(unittest.TestCase):
    def test_counts_every_level(self):
        s = structural_summary(_course("A", "B", lessons_per_unit=3,
                                       concepts_per_lesson=2))
        assert (s["modules"], s["units"], s["lessons"], s["concepts"]) == (2, 2, 6, 12)
        assert s["concepts_per_lesson"] == 2.0

    def test_empty_lessons_are_counted(self):
        c = _course("A", lessons_per_unit=2, concepts_per_lesson=1)
        c["modules"][0]["units"][0]["lessons"][0]["concepts"] = []
        assert structural_summary(c)["empty_lessons"] == 1


class TestRealReference(unittest.TestCase):
    def test_mit_reference_file_is_well_formed(self):
        path = os.path.join(_root, "tools/references/mit_18.06_linear_algebra.json")
        ref = json.load(open(path))
        assert ref["lectures"] == 34 and ref["sessions_per_week"] == 3
        assert len(ref["areas"]) == 10
        for area, terms in ref["areas"].items():
            assert terms and all(isinstance(t, str) for t in terms), area


if __name__ == "__main__":
    unittest.main()


class TestSequencing(unittest.TestCase):
    """Coverage cannot see ordering. A course copied from an alphabetical index
    scored 100% coverage while its modules ran Addition..., Cofactors...,
    Diagonal Matrix, Identity Matrix — every topic present, none of it teachable.
    Presence is not sequence, so it takes a second instrument."""

    def test_alphabetical_modules_are_flagged(self):
        from tools.coverage_check import sequencing_check
        c = _course("Addition and Transpose", "Cofactors and Minors",
                    "Diagonal Matrix", "Gauss-Jordan Reduction", "Identity Matrix")
        r = sequencing_check(c)
        assert r["alphabetical"] is True and r["verdict"] == "INDEX_ORDER"

    def test_a_taught_order_passes(self):
        from tools.coverage_check import sequencing_check
        c = _course("Vectors and Vector Spaces", "Solving Linear Systems",
                    "Determinants", "Eigenvalues", "Orthogonality")
        r = sequencing_check(c)
        assert r["alphabetical"] is False and r["verdict"] == "ok"

    def test_too_few_modules_is_not_judged(self):
        """Three modules can be alphabetical by chance; that is not evidence."""
        from tools.coverage_check import sequencing_check
        r = sequencing_check(_course("Alpha", "Beta", "Gamma"))
        assert r["checked"] is False
