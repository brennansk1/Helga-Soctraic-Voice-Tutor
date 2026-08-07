"""A silent judge must report NOT MEASURED, never 0% coverage.

The regression this pins: `coverage()` derives `covered` from what the judge
listed, so an empty judge response left every topic in `missing` and
`_summarise` scored it 0% / INADEQUATE. Measured on a real build — the same
course scored 0% at build time and 55% minutes later against the identical
structure, the only difference being judge availability.

With HELGA_SYLLABUS_GATE=1 that manufactured 0% would have failed the build.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import syllabus_check as sc  # noqa: E402
from services.common import fact_check  # noqa: E402

STRUCT = {
    "uid": "course_test", "title": "quantum computing", "mastery": 3,
    "modules": [{"title": "Qubits", "units": [{"lessons": [
        {"title": "Basis states", "concepts": [{"title": "Hadamard transform"}]}
    ]}]}],
}
TOPICS = ["qubit states", "Hadamard transform", "Grover search algorithm"]


class TestSilentJudge(unittest.TestCase):
    def test_empty_judge_response_is_not_measured(self):
        """Both lists empty = the judge said nothing = instrument outage."""
        with patch.object(fact_check, "_post", return_value={"topics_analysis": []}):
            self.assertIsNone(sc.coverage(TOPICS, "MODULE: Qubits"))

    def test_genuine_nothing_covered_still_scores(self):
        """A real verdict NAMES what is absent, so it must still be graded —
        otherwise a genuinely hollow course would be reported as unmeasurable."""
        with patch.object(fact_check, "_post",
                          return_value={"topics_analysis": [{"topic": t, "legacy_covered": False, "introduced": False, "practiced": False, "assessed": False, "evidence": ""} for t in TOPICS]}):
            result = sc.coverage(TOPICS, "MODULE: Unrelated")
        self.assertIsNotNone(result)
        self.assertEqual(len([t for t in result["topics_analysis"] if t["legacy_covered"]]), 0)
        self.assertEqual(len([t for t in result["topics_analysis"] if not t["legacy_covered"]]), 3)

    def test_check_structure_reports_error_not_zero(self):
        with patch.object(sc, "core_topics", return_value=TOPICS), \
             patch.object(sc, "coverage", return_value=None):
            r = sc.check_structure(STRUCT)
        self.assertIn("error", r)
        self.assertNotEqual(r.get("coverage_pct"), 0,
                            "an outage must not surface as a scored 0%")

    def test_no_topics_is_an_error_not_a_grade(self):
        with patch.object(sc, "core_topics", return_value=[]):
            r = sc.check_structure(STRUCT)
        self.assertIn("error", r)

    def test_real_coverage_still_computes(self):
        with patch.object(sc, "core_topics", return_value=TOPICS), \
             patch.object(fact_check, "_post", return_value={
                 "topics_analysis": [
                     {"topic": "Hadamard transform", "legacy_covered": True, "introduced": True, "practiced": True, "assessed": True, "evidence": ""},
                     {"topic": TOPICS[0], "legacy_covered": False, "introduced": False, "practiced": False, "assessed": False, "evidence": ""}
                 ]}):
            r = sc.check_structure(STRUCT)
        self.assertNotIn("error", r)
        self.assertGreater(r["coverage_pct"], 0)



class TestCoverageRubric(unittest.TestCase):
    def test_strict_coverage_requires_all_three_flags(self):
        """A topic merely introduced (mention-only) must NOT count as covered strictly."""
        cov = {
            "topics_analysis": [
                {"topic": "T1", "is_core": True, "legacy_covered": True, "introduced": True, "practiced": False, "assessed": False, "is_covered_strict": False},
                {"topic": "T2", "is_core": True, "legacy_covered": True, "introduced": True, "practiced": True, "assessed": True, "is_covered_strict": True}
            ],
            "sequencing": []
        }
        with patch.object(sc, "LEGACY_COVERAGE_METRIC", False):
            summary = sc._summarise(cov)
        self.assertEqual(summary["coverage_strict_pct"], 50)
        self.assertEqual(summary["coverage_pct"], 50)
        self.assertIn("T1", summary["missing"])
        self.assertNotIn("T2", summary["missing"])

    def test_legacy_coverage_metric_reproduces_single_flag_behavior(self):
        cov = {
            "topics_analysis": [
                {"topic": "T1", "is_core": True, "legacy_covered": True, "introduced": True, "practiced": False, "assessed": False, "is_covered_strict": False},
                {"topic": "T2", "is_core": True, "legacy_covered": False, "introduced": False, "practiced": False, "assessed": False, "is_covered_strict": False}
            ],
            "sequencing": []
        }
        with patch.object(sc, "LEGACY_COVERAGE_METRIC", True):
            summary = sc._summarise(cov)
        # legacy metric uses only 'legacy_covered' flag which is true for T1, false for T2
        self.assertEqual(summary["coverage_legacy_pct"], 50)
        self.assertEqual(summary["coverage_pct"], 50)
        self.assertNotIn("T1", summary["missing"])
        self.assertIn("T2", summary["missing"])

    def test_is_core_marking_drives_two_floors(self):
        cov = {
            "topics_analysis": [
                {"topic": "Core1", "is_core": True, "legacy_covered": True, "introduced": True, "practiced": True, "assessed": True, "is_covered_strict": True},
                {"topic": "Sec1", "is_core": False, "legacy_covered": False, "introduced": False, "practiced": False, "assessed": False, "is_covered_strict": False}
            ],
            "sequencing": []
        }
        with patch.object(sc, "LEGACY_COVERAGE_METRIC", False), \
             patch.object(sc, "CORE_MIN_COVERAGE", 100), \
             patch.object(sc, "SECONDARY_MIN_COVERAGE", 0):
            summary = sc._summarise(cov)
        self.assertEqual(summary["core_strict_pct"], 100)
        self.assertEqual(summary["secondary_strict_pct"], 0)
        self.assertEqual(summary["verdict"], "ADEQUATE")

        with patch.object(sc, "LEGACY_COVERAGE_METRIC", False), \
             patch.object(sc, "CORE_MIN_COVERAGE", 100), \
             patch.object(sc, "SECONDARY_MIN_COVERAGE", 50):
            summary = sc._summarise(cov)
        self.assertEqual(summary["verdict"], "INADEQUATE")

    def test_no_core_secondary_marking_works_backward_compatibility(self):
        # A list with no core/secondary marking still works.
        # tools.syllabus_check.coverage will default to True if strings are passed.
        with patch.object(fact_check, "_post", return_value={"topics_analysis": [{"topic": "Topic1", "legacy_covered": True, "introduced": True, "practiced": True, "assessed": True}]}):
            res = sc.coverage(["Topic1"], "MOD")
        self.assertTrue(res["topics_analysis"][0]["is_core"])
        self.assertTrue(res["topics_analysis"][0]["is_covered_strict"])


if __name__ == "__main__":
    unittest.main()
