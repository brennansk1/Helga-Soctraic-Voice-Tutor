"""Anchored grading, split from its consequences, and instrumented.

The measured ±1.4/5 swing is normal small-judge behaviour, not an anomaly.
Expect Krippendorff's α around 0.4-0.6 untreated.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.grading import (  # noqa: E402
    ANCHORS, agreement, grader_health, is_clean_margin, misconception_block,
    parse_grade, rubric_block, score_entropy, to_fsrs_rating)


class TestRubric(unittest.TestCase):
    def test_the_concepts_threshold_is_the_pass_anchor(self):
        """Committing the criterion before seeing the answer turns grading into
        a comparison against a fixed target."""
        b = rubric_block("identify the limiting reagent and justify it", 3)
        self.assertIn("PASS MARK (3) FOR THIS CONCEPT", b)
        self.assertIn("limiting reagent", b)

    def test_a_missing_threshold_degrades_visibly(self):
        b = rubric_block(None)
        self.assertIn("No concept-specific threshold", b)

    def test_all_five_anchors_are_present(self):
        b = rubric_block("x")
        for score in ANCHORS:
            self.assertIn(f"  {score} — ", b)

    def test_misconceptions_become_a_closed_set(self):
        b = misconception_block([{"belief": "Heavier objects fall faster"},
                                 {"belief": "Mass and weight are the same"}])
        self.assertIn("m1:", b)
        self.assertIn("m2:", b)

    def test_no_misconceptions_yields_nothing(self):
        self.assertEqual(misconception_block([]), "")


class TestParsing(unittest.TestCase):
    def test_structured_output(self):
        self.assertEqual(parse_grade({"grade": 4, "misconception": "m2"}), (4, "m2"))

    def test_none_means_ungraded_not_a_guess(self):
        """A fabricated grade would enter FSRS as a real assessment."""
        self.assertEqual(parse_grade({}), (None, None))
        self.assertEqual(parse_grade(None), (None, None))
        self.assertEqual(parse_grade({"grade": 9}), (None, None))

    def test_a_bare_number_and_loose_text(self):
        self.assertEqual(parse_grade(3)[0], 3)
        self.assertEqual(parse_grade("I would score this a 4 overall")[0], 4)

    def test_the_string_none_is_not_a_misconception(self):
        self.assertIsNone(parse_grade({"grade": 5, "misconception": "none"})[1])


class TestSplitDownstream(unittest.TestCase):
    def test_a_bare_pass_is_not_a_clean_margin(self):
        """With a judge whose α is ~0.5, a 3 is as likely to have been a 2."""
        self.assertFalse(is_clean_margin(3))
        self.assertTrue(is_clean_margin(4))
        self.assertFalse(is_clean_margin(None))

    def test_fsrs_mapping_is_coarse_on_purpose(self):
        """So a one-point grader wobble rarely crosses a boundary."""
        self.assertEqual([to_fsrs_rating(g) for g in (1, 2, 3, 4, 5)],
                         [1, 2, 3, 3, 4])
        self.assertIsNone(to_fsrs_rating(None))


class TestInstrumentation(unittest.TestCase):
    def test_a_constant_grader_has_zero_entropy(self):
        self.assertEqual(score_entropy([3, 3, 3, 3]), 0.0)

    def test_a_varied_grader_has_high_entropy(self):
        self.assertGreater(score_entropy([1, 2, 3, 4, 5]), 2.0)

    def test_agreement_reports_spread(self):
        a = agreement([3, 3, 4])
        self.assertEqual(a["mode"], 3)
        self.assertEqual(a["spread"], 1)
        self.assertEqual(a["within_one"], 1.0)

    def test_a_stable_but_useless_grader_is_not_usable(self):
        """THE TRAP: perfect test-retest agreement, zero information."""
        h = grader_health([[3, 3, 3]] * 4)
        self.assertEqual(h["mean_exact_agreement"], 1.0)
        self.assertFalse(h["usable"])
        self.assertIn("low-entropy", h["note"])

    def test_a_discriminating_stable_grader_is_usable(self):
        h = grader_health([[2, 2, 2], [4, 4, 4], [1, 1, 1], [5, 5, 5]])
        self.assertTrue(h["usable"])

    def test_a_wildly_unstable_grader_is_not_usable(self):
        h = grader_health([[1, 5, 3], [2, 5, 1], [4, 1, 5]])
        self.assertFalse(h["usable"])

    def test_no_runs_is_not_a_pass(self):
        self.assertFalse(grader_health([]).get("ran"))
