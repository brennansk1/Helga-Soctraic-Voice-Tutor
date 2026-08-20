"""Course-builder guards against two silent syllabus/content losses.

1. DEDUP must not delete distinct technical terms that share a head noun
   ("Linear Regression" / "Logistic Regression"). It runs before hydration, so
   anything it removes is gone with no downstream trace.
2. HYDRATION must not publish a course as "ready" when the bodies are
   "[Hydration failed]" stubs. A stub counts as a failed concept.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Heavy/optional deps that must not be imported for these tests
sys.modules.setdefault('kuzu', MagicMock())
sys.modules.setdefault('libzim', MagicMock())
sys.modules.setdefault('sentence_transformers', MagicMock())

from services.core.course_builder import (  # noqa: E402
    ContentHydrator,
    CourseCreationError,
    SyllabusAuditor,
)


def _course_with(titles, module_title="Module One"):
    """One module / one unit / one lesson holding the given concept titles."""
    return {
        "uid": "course_test",
        "title": "Test Course",
        "modules": [{
            "uid": "mod_1",
            "title": module_title,
            "units": [{
                "uid": "unit_1",
                "title": "Unit One",
                "lessons": [{
                    "uid": "less_1",
                    "title": "Lesson One",
                    "concepts": [
                        {"uid": f"con_{i:02d}", "title": t}
                        for i, t in enumerate(titles)
                    ],
                }],
            }],
        }],
    }


def _surviving_titles(course):
    return [
        c["title"]
        for m in course["modules"]
        for u in m["units"]
        for l in u["lessons"]
        for c in l["concepts"]
    ]


class TestDedupKeepsDistinctConcepts(unittest.TestCase):
    """The measured casualties of the old |intersection| / |smaller set| ratio."""

    def setUp(self):
        self.auditor = SyllabusAuditor(db_path="dummy", storage=MagicMock())

    def test_linear_and_logistic_regression_both_survive(self):
        # Old ratio: 1/2 = 0.50 > 0.4 -> one silently deleted. Jaccard: 1/3.
        course = _course_with(["Linear Regression", "Logistic Regression"])
        deleted = self.auditor._programmatic_dedup(course)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(_surviving_titles(course)), 2)

    def test_ordinary_and_partial_differential_equations_both_survive(self):
        # Old ratio: 2/3 = 0.67 -> deleted. Jaccard: 2/4 = 0.50.
        course = _course_with([
            "Ordinary Differential Equations",
            "Partial Differential Equations",
        ])
        deleted = self.auditor._programmatic_dedup(course)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(_surviving_titles(course)), 2)

    def test_newtons_laws_all_survive(self):
        course = _course_with([
            "Newton's First Law",
            "Newton's Second Law",
            "Newton's Third Law",
        ])
        deleted = self.auditor._programmatic_dedup(course)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(_surviving_titles(course)), 3)

    def test_shared_subject_noun_module_survives(self):
        """A module whose titles all name the same subject is normal, not duplicated."""
        course = _course_with([
            "Supervised Learning Algorithms",
            "Unsupervised Learning Algorithms",
            "Reinforcement Learning Algorithms",
        ], module_title="Machine Learning")
        deleted = self.auditor._programmatic_dedup(course)
        self.assertEqual(deleted, 0)


class TestDedupStillRemovesRealDuplicates(unittest.TestCase):

    def setUp(self):
        self.auditor = SyllabusAuditor(db_path="dummy", storage=MagicMock())

    def test_article_only_difference_is_removed(self):
        course = _course_with(["The Pythagorean Theorem", "Pythagorean Theorem"])
        deleted = self.auditor._programmatic_dedup(course)
        self.assertEqual(deleted, 1)
        self.assertEqual(_surviving_titles(course), ["The Pythagorean Theorem"])

    def test_filler_only_difference_is_removed(self):
        course = _course_with(["Photosynthesis", "Introduction to Photosynthesis"])
        deleted = self.auditor._programmatic_dedup(course)
        self.assertEqual(deleted, 1)
        self.assertEqual(_surviving_titles(course), ["Photosynthesis"])

    def test_exact_duplicate_title_is_removed(self):
        course = _course_with(["Cell Division", "Cell Division"])
        deleted = self.auditor._programmatic_dedup(course)
        self.assertEqual(deleted, 1)

    def test_reordered_title_is_removed(self):
        course = _course_with(["Kinetic Energy Transfer", "Transfer of Kinetic Energy"])
        deleted = self.auditor._programmatic_dedup(course)
        self.assertEqual(deleted, 1)


class TestDedupModuleBudget(unittest.TestCase):
    """Dedup may not gut a module even when every pair looks like a duplicate."""

    def setUp(self):
        self.auditor = SyllabusAuditor(db_path="dummy", storage=MagicMock())

    def test_removal_is_capped_at_a_quarter_of_the_module(self):
        titles = [
            "Photosynthesis",
            "Photosynthesis Overview",      # filler-only difference
            "Photosynthesis Basics",        # filler-only difference
            "Photosynthesis Fundamentals",  # filler-only difference
            "Photosynthesis Essentials",    # filler-only difference
            "Chlorophyll Absorption Spectra",
            "The Calvin Cycle",
            "Stomatal Gas Exchange",
        ]
        course = _course_with(titles)
        deleted = self.auditor._programmatic_dedup(course)
        # 8 concepts -> budget of 2, despite 4 duplicate candidates.
        self.assertEqual(deleted, 2)
        self.assertEqual(len(_surviving_titles(course)), 6)


class TestSimilarityMeasure(unittest.TestCase):

    def setUp(self):
        self.auditor = SyllabusAuditor(db_path="dummy", storage=MagicMock())

    def test_ratio_divides_by_the_union(self):
        a = self.auditor._tokenize_title("Linear Regression")
        b = self.auditor._tokenize_title("Logistic Regression")
        self.assertAlmostEqual(self.auditor._word_overlap_ratio(a, b), 1 / 3)

    def test_identical_token_sets_score_one(self):
        a = self.auditor._tokenize_title("The Pythagorean Theorem")
        b = self.auditor._tokenize_title("Pythagorean Theorem")
        self.assertEqual(self.auditor._word_overlap_ratio(a, b), 1.0)

    def test_empty_tokens_are_never_duplicates(self):
        self.assertFalse(self.auditor._titles_are_duplicates(set(), {"x"}))


class TestHydrationStubGate(unittest.TestCase):
    """A course whose bodies are all "[Hydration failed]" is not "ready"."""

    STUB = "# Title\n\n## Core Explanation\n[Hydration failed]\n"
    REAL = "# Title\n\n## Core Explanation\nA real body with actual teaching content.\n"

    def _hydrator(self, course, tmpdir):
        storage = MagicMock()
        storage.courses.get_course.return_value = course
        storage.courses.get_concept_content.return_value = ""
        storage.courses.courses_dir = tmpdir
        # Env read in __init__: keep the extra verification passes (each an LLM
        # call) out of this test — the gate under test runs before them.
        env = {
            "HELGA_ENFORCE_DEPTH": "0",
            "HELGA_FACT_CHECK": "0",
            "HELGA_LEVEL_CALIBRATION": "0",
            "HELGA_CONFIDENCE_FLOOR": "0",
        }
        with patch.dict(os.environ, env):
            hydrator = ContentHydrator(providers=[], storage=storage, course_depth=2)
        hydrator._ledger_context = MagicMock(return_value="")
        hydrator._record_taught = MagicMock(return_value=None)
        hydrator._retain_sources = MagicMock(return_value=None)
        hydrator._correct_redundancy = MagicMock(side_effect=lambda md, *a, **k: md)
        return hydrator, storage

    def _run(self, hydrator, bodies):
        """Hydrate with _condense_and_structure_content returning `bodies` in order."""
        with patch("services.core.course_builder.requests.post",
                   side_effect=ConnectionError("research offline")), \
             patch.dict(sys.modules,
                        {"services.core.asset_collector": MagicMock()}):
            hydrator._condense_and_structure_content = MagicMock(side_effect=bodies)
            hydrator.hydrate("course_test")
        # Guard against a vacuous pass: the hydration path must really have run.
        self.assertEqual(
            hydrator._condense_and_structure_content.call_count, len(bodies))

    def test_all_stub_build_is_not_ready(self):
        import tempfile
        course = _course_with([f"Concept Number {i}" for i in range(4)])
        with tempfile.TemporaryDirectory() as tmpdir:
            hydrator, _ = self._hydrator(course, tmpdir)
            with self.assertRaises(CourseCreationError):
                self._run(hydrator, [self.STUB] * 4)
        self.assertNotEqual(course.get("status"), "ready")
        self.assertEqual(course.get("status"), "failed")

    def test_minority_of_stubs_marks_the_course_partial(self):
        import tempfile
        course = _course_with([f"Concept Number {i}" for i in range(4)])
        with tempfile.TemporaryDirectory() as tmpdir:
            hydrator, _ = self._hydrator(course, tmpdir)
            self._run(hydrator, [self.STUB] + [self.REAL] * 3)
        self.assertEqual(course.get("status"), "partial")
        self.assertEqual(course.get("fallback_count"), 1)

    def test_clean_build_is_ready(self):
        import tempfile
        course = _course_with([f"Concept Number {i}" for i in range(4)])
        with tempfile.TemporaryDirectory() as tmpdir:
            hydrator, _ = self._hydrator(course, tmpdir)
            self._run(hydrator, [self.REAL] * 4)
        self.assertEqual(course.get("status"), "ready")


if __name__ == "__main__":
    unittest.main()
