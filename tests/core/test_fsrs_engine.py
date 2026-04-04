"""
Tests for the FSRS Engine — verifies correct spaced repetition calculations.

Covers:
- FSRSEngine instantiation
- calculate_memory() returns correct tuple structure
- Rating 1 (Again) produces shorter interval than rating 4 (Easy)
- get_retention() returns float between 0 and 1
- Edge case: days_elapsed=0
- Retention target consistency with next_interval
"""
import sys
import os
import unittest
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/core'))

from services.core.fsrs_engine import FSRSEngine


class TestFSRSEngineInstantiation(unittest.TestCase):
    """Tests for FSRSEngine creation."""

    def test_default_retention(self):
        engine = FSRSEngine()
        self.assertEqual(engine.desired_retention, 0.9)

    def test_custom_retention(self):
        engine = FSRSEngine(desired_retention=0.85)
        self.assertEqual(engine.desired_retention, 0.85)

    def test_weights_loaded(self):
        engine = FSRSEngine()
        self.assertIsInstance(engine.w, list)
        self.assertGreater(len(engine.w), 10)


class TestCalculateMemoryReturnStructure(unittest.TestCase):
    """Verify calculate_memory returns dict-like tuple with stability, difficulty."""

    def setUp(self):
        self.engine = FSRSEngine()

    def test_first_review_returns_tuple(self):
        result = self.engine.calculate_memory(None, None, 3, 0)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_first_review_stability_positive(self):
        s, d = self.engine.calculate_memory(None, None, 3, 0)
        self.assertIsInstance(s, float)
        self.assertGreater(s, 0)

    def test_first_review_difficulty_in_range(self):
        s, d = self.engine.calculate_memory(None, None, 3, 0)
        self.assertIsInstance(d, float)
        self.assertGreaterEqual(d, 1)
        self.assertLessEqual(d, 10)

    def test_subsequent_review_returns_tuple(self):
        s, d = self.engine.calculate_memory(5.0, 5.0, 3, 5)
        self.assertIsInstance(result := (s, d), tuple)
        self.assertEqual(len(result), 2)

    def test_calculate_memory_dict_style_access(self):
        """Verify we can build the expected dict from the return values."""
        s, d = self.engine.calculate_memory(5.0, 5.0, 3, 5)
        interval = self.engine.next_interval(s)
        retention = self.engine.get_retention(s, 0)
        result = {
            'stability': s,
            'difficulty': d,
            'interval': interval,
            'retention': retention,
        }
        self.assertIn('stability', result)
        self.assertIn('difficulty', result)
        self.assertIn('interval', result)
        self.assertIn('retention', result)
        self.assertIsInstance(result['stability'], float)
        self.assertIsInstance(result['difficulty'], float)
        self.assertIsInstance(result['interval'], int)
        self.assertIsInstance(result['retention'], float)


class TestFSRSEngineFirstReview(unittest.TestCase):
    """Tests for first review (stability=None, difficulty=None)."""

    def setUp(self):
        self.engine = FSRSEngine()

    def test_first_review_again(self):
        s, d = self.engine.calculate_memory(None, None, 1, 0)
        self.assertGreater(s, 0)
        self.assertGreater(d, 0)

    def test_first_review_good(self):
        s, d = self.engine.calculate_memory(None, None, 3, 0)
        self.assertGreater(s, 1)  # Good rating should give decent stability

    def test_first_review_easy(self):
        s, d = self.engine.calculate_memory(None, None, 4, 0)
        s_good, _ = self.engine.calculate_memory(None, None, 3, 0)
        self.assertGreater(s, s_good)  # Easy > Good for initial stability

    def test_difficulty_bounded_1_10(self):
        for rating in [1, 2, 3, 4]:
            _, d = self.engine.calculate_memory(None, None, rating, 0)
            self.assertGreaterEqual(d, 1)
            self.assertLessEqual(d, 10)


class TestRatingAgainVsEasyIntervals(unittest.TestCase):
    """Rating 1 (Again) must produce shorter interval than rating 4 (Easy)."""

    def setUp(self):
        self.engine = FSRSEngine()

    def test_again_shorter_than_easy_first_review(self):
        s_again, _ = self.engine.calculate_memory(None, None, 1, 0)
        s_easy, _ = self.engine.calculate_memory(None, None, 4, 0)
        i_again = self.engine.next_interval(s_again)
        i_easy = self.engine.next_interval(s_easy)
        self.assertLess(i_again, i_easy,
                        "Again rating should produce shorter interval than Easy")

    def test_again_shorter_than_easy_subsequent_review(self):
        s_again, _ = self.engine.calculate_memory(5.0, 5.0, 1, 5)
        s_easy, _ = self.engine.calculate_memory(5.0, 5.0, 4, 5)
        i_again = self.engine.next_interval(s_again)
        i_easy = self.engine.next_interval(s_easy)
        self.assertLess(i_again, i_easy,
                        "Again rating should produce shorter interval than Easy on subsequent review")

    def test_again_shorter_than_good(self):
        s_again, _ = self.engine.calculate_memory(5.0, 5.0, 1, 5)
        s_good, _ = self.engine.calculate_memory(5.0, 5.0, 3, 5)
        i_again = self.engine.next_interval(s_again)
        i_good = self.engine.next_interval(s_good)
        self.assertLess(i_again, i_good)

    def test_hard_shorter_than_easy(self):
        s_hard, _ = self.engine.calculate_memory(5.0, 5.0, 2, 5)
        s_easy, _ = self.engine.calculate_memory(5.0, 5.0, 4, 5)
        i_hard = self.engine.next_interval(s_hard)
        i_easy = self.engine.next_interval(s_easy)
        self.assertLessEqual(i_hard, i_easy)


class TestFSRSEngineSubsequentReview(unittest.TestCase):
    """Tests for subsequent reviews with existing stability/difficulty."""

    def setUp(self):
        self.engine = FSRSEngine()

    def test_stability_increases_on_good(self):
        new_s, _ = self.engine.calculate_memory(5.0, 5.0, 3, 5)
        self.assertGreater(new_s, 5.0)

    def test_stability_again_less_than_good(self):
        s_again, _ = self.engine.calculate_memory(5.0, 5.0, 1, 5)
        s_good, _ = self.engine.calculate_memory(5.0, 5.0, 3, 5)
        self.assertLess(s_again, s_good)

    def test_difficulty_decreases_on_easy(self):
        _, new_d = self.engine.calculate_memory(5.0, 5.0, 4, 5)
        self.assertLess(new_d, 5.0)

    def test_difficulty_increases_on_again(self):
        _, new_d = self.engine.calculate_memory(5.0, 5.0, 1, 5)
        self.assertGreater(new_d, 5.0)

    def test_error_handling_returns_safe_values(self):
        s, d = self.engine.calculate_memory(0.0, 5.0, 3, 5)
        self.assertIsNotNone(s)
        self.assertIsNotNone(d)


class TestFSRSNextInterval(unittest.TestCase):
    """Tests for next_interval calculation."""

    def setUp(self):
        self.engine = FSRSEngine()

    def test_minimum_interval_is_1(self):
        self.assertEqual(self.engine.next_interval(0.1), 1)

    def test_none_stability_returns_1(self):
        self.assertEqual(self.engine.next_interval(None), 1)

    def test_zero_stability_returns_1(self):
        self.assertEqual(self.engine.next_interval(0), 1)

    def test_negative_stability_returns_1(self):
        self.assertEqual(self.engine.next_interval(-5.0), 1)

    def test_interval_increases_with_stability(self):
        i1 = self.engine.next_interval(1.0)
        i5 = self.engine.next_interval(5.0)
        i20 = self.engine.next_interval(20.0)
        self.assertLessEqual(i1, i5)
        self.assertLessEqual(i5, i20)

    def test_reasonable_intervals(self):
        interval = self.engine.next_interval(3.0)
        self.assertGreaterEqual(interval, 1)
        self.assertLessEqual(interval, 15)

    def test_high_stability_gives_weeks(self):
        interval = self.engine.next_interval(30.0)
        self.assertGreater(interval, 7)


class TestFSRSRetention(unittest.TestCase):
    """Tests for get_retention (forgetting curve)."""

    def setUp(self):
        self.engine = FSRSEngine()

    def test_retention_at_zero_days(self):
        """Just reviewed — should be near 1.0."""
        r = self.engine.get_retention(5.0, 0)
        self.assertAlmostEqual(r, 1.0)

    def test_retention_at_zero_days_is_exactly_one(self):
        """days_elapsed=0 edge case: retention must be 1.0."""
        r = self.engine.get_retention(10.0, 0)
        self.assertEqual(r, 1.0)

    def test_retention_at_zero_days_various_stabilities(self):
        """days_elapsed=0 should give 1.0 regardless of stability."""
        for s in [0.5, 1.0, 5.0, 50.0, 100.0]:
            r = self.engine.get_retention(s, 0)
            self.assertEqual(r, 1.0, f"Retention at day 0 should be 1.0 for stability={s}")

    def test_retention_decreases_over_time(self):
        r1 = self.engine.get_retention(5.0, 1)
        r5 = self.engine.get_retention(5.0, 5)
        r30 = self.engine.get_retention(5.0, 30)
        self.assertGreater(r1, r5)
        self.assertGreater(r5, r30)

    def test_retention_bounded_0_1(self):
        for days in [0, 1, 5, 30, 100, 365]:
            r = self.engine.get_retention(5.0, days)
            self.assertGreaterEqual(r, 0.0)
            self.assertLessEqual(r, 1.0)

    def test_higher_stability_means_slower_decay(self):
        r_low = self.engine.get_retention(2.0, 10)
        r_high = self.engine.get_retention(20.0, 10)
        self.assertGreater(r_high, r_low)

    def test_none_stability_returns_zero(self):
        self.assertEqual(self.engine.get_retention(None, 5), 0.0)

    def test_zero_stability_returns_zero(self):
        self.assertEqual(self.engine.get_retention(0, 5), 0.0)

    def test_retention_is_float(self):
        r = self.engine.get_retention(5.0, 3)
        self.assertIsInstance(r, float)

    def test_retention_after_one_day_less_than_one(self):
        r = self.engine.get_retention(5.0, 1)
        self.assertLess(r, 1.0)
        self.assertGreater(r, 0.0)


class TestFSRSEdgeCaseDaysElapsedZero(unittest.TestCase):
    """Dedicated edge case tests for days_elapsed=0."""

    def setUp(self):
        self.engine = FSRSEngine()

    def test_calculate_memory_days_zero_first_review(self):
        """First review with days_elapsed=0 should work fine."""
        s, d = self.engine.calculate_memory(None, None, 3, 0)
        self.assertGreater(s, 0)
        self.assertGreater(d, 0)

    def test_calculate_memory_days_zero_subsequent_review(self):
        """Subsequent review with days_elapsed=0 should not crash."""
        s, d = self.engine.calculate_memory(5.0, 5.0, 3, 0)
        self.assertIsNotNone(s)
        self.assertIsNotNone(d)
        self.assertGreater(s, 0)

    def test_retention_days_zero(self):
        """get_retention with days_elapsed=0 returns exactly 1.0."""
        self.assertEqual(self.engine.get_retention(5.0, 0), 1.0)

    def test_next_interval_from_zero_elapsed_review(self):
        """After a review at day 0, next_interval should still give valid result."""
        s, d = self.engine.calculate_memory(5.0, 5.0, 3, 0)
        interval = self.engine.next_interval(s)
        self.assertGreaterEqual(interval, 1)


class TestFSRSRetentionTarget(unittest.TestCase):
    """Verify that next_interval and get_retention are consistent."""

    def setUp(self):
        self.engine = FSRSEngine(desired_retention=0.9)

    def test_retention_at_interval_is_target(self):
        """Retention at the scheduled interval should be approximately desired_retention."""
        stability = 10.0
        interval = self.engine.next_interval(stability)
        retention = self.engine.get_retention(stability, interval)
        self.assertAlmostEqual(retention, 0.9, delta=0.05)

    def test_retention_target_various_stabilities(self):
        """Retention at interval should be near target for various stability values."""
        for stability in [2.0, 5.0, 15.0, 30.0]:
            interval = self.engine.next_interval(stability)
            retention = self.engine.get_retention(stability, interval)
            self.assertAlmostEqual(retention, 0.9, delta=0.05,
                                   msg=f"Failed for stability={stability}")


if __name__ == '__main__':
    unittest.main()
