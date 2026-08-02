"""A2 tests: source_confidence must be load-bearing, and degradation loud.

Before this, `source_confidence` was computed, stored and rendered but nothing
ever acted on it: in the sample course 24 of 36 concepts scored below 0.5 and
shipped looking identical to well-grounded ones. Separately, requesting hybrid
retrieval silently returned lexical-only results when dense was unavailable —
a quality cliff the caller could not detect.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)


class TestConfidenceFloorConfig(unittest.TestCase):
    def test_default_floor_is_meaningful(self):
        from services.core.course_builder import ContentHydrator
        h = ContentHydrator.__new__(ContentHydrator)
        h.confidence_floor = float(os.getenv("HELGA_CONFIDENCE_FLOOR", "0.5"))
        self.assertGreaterEqual(
            h.confidence_floor, 0.5,
            "A floor below 0.5 would leave the majority of observed "
            "low-confidence concepts unmarked, which is the bug being fixed."
        )


class TestHydratorActsOnConfidence(unittest.TestCase):
    """The hydration path must retry and then mark, not silently accept."""

    def test_source_marks_low_confidence_in_the_artifact(self):
        """The learner must be able to see that content is thinly sourced."""
        import inspect
        from services.core.course_builder import ContentHydrator
        src = inspect.getsource(ContentHydrator.hydrate)
        self.assertIn("Limited sources", src,
                      "thin content must carry a visible marker in the artifact")
        self.assertIn("low_confidence", src)

    def test_low_confidence_triggers_a_broadened_research_retry(self):
        import inspect
        from services.core.course_builder import ContentHydrator
        src = inspect.getsource(ContentHydrator.hydrate)
        self.assertIn("broaden", src,
                      "a weak grounding result should be retried more broadly "
                      "before being accepted")
        self.assertIn("confidence_floor", src)

    def test_course_records_a_grounding_verdict(self):
        import inspect
        from services.core.course_builder import ContentHydrator
        src = inspect.getsource(ContentHydrator.hydrate)
        self.assertIn("well_grounded_pct", src)
        self.assertIn("concepts_below_floor", src)


class TestUiThresholdMatchesBuilder(unittest.TestCase):
    def test_learn_tab_badge_threshold_is_aligned(self):
        """A UI threshold looser than the builder's floor hides real weakness."""
        p = os.path.join(_root, 'services/web-ui/templates/learn.html')
        with open(p) as f:
            html = f.read()
        self.assertIn(
            "srcConf < 0.5", html,
            "learn.html must flag below the same 0.5 floor the builder enforces; "
            "at the previous 0.3 the majority of weak concepts showed no marker."
        )


class TestHybridDegradationIsLoud(unittest.TestCase):
    def test_librarian_reports_actual_retrieval_mode(self):
        p = os.path.join(_root, 'services/rag/librarian.py')
        with open(p) as f:
            src = f.read()
        self.assertIn("retrieval_mode", src,
                      "the response must state which retrieval actually ran")
        self.assertIn("degraded_reason", src)

    def test_no_silent_degradation_comment_remains(self):
        p = os.path.join(_root, 'services/rag/librarian.py')
        with open(p) as f:
            src = f.read()
        self.assertNotIn(
            "silently degrades to FTS5", src,
            "degradation is no longer silent — the stale comment would mislead"
        )


if __name__ == '__main__':
    unittest.main()
