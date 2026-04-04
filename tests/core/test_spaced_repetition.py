"""Tests for SM2Engine spaced repetition algorithm."""

import os
import sys
import pytest
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.spaced_repetition import SM2Engine


@pytest.fixture
def engine():
    return SM2Engine()


class TestSM2Algorithm:
    def test_correct_answer_increments_reps(self, engine):
        interval, ef, reps = engine.calculate_next_review(0, 2.5, 0, 4)
        assert reps == 1
        assert interval == 1

    def test_second_correct_answer(self, engine):
        interval, ef, reps = engine.calculate_next_review(1, 2.5, 1, 4)
        assert reps == 2
        assert interval == 3

    def test_third_correct_answer(self, engine):
        interval, ef, reps = engine.calculate_next_review(2, 2.5, 3, 4)
        assert reps == 3
        assert interval == 7

    def test_fourth_correct_uses_ef(self, engine):
        interval, ef, reps = engine.calculate_next_review(3, 2.5, 7, 4)
        assert reps == 4
        assert interval == round(7 * 2.5)

    def test_failure_resets_reps(self, engine):
        interval, ef, reps = engine.calculate_next_review(5, 2.5, 35, 2)
        assert reps == 0
        assert interval == 1

    def test_ef_never_below_1_3(self, engine):
        # Repeatedly fail to drive EF down
        ef = 2.5
        for _ in range(20):
            _, ef, _ = engine.calculate_next_review(0, ef, 1, 1)
        assert ef >= 1.3

    def test_perfect_score_increases_ef(self, engine):
        _, ef, _ = engine.calculate_next_review(3, 2.5, 7, 5)
        assert ef > 2.5


class TestRetention:
    def test_retention_at_zero_days(self, engine):
        r = engine.calculate_retention(0, 10)
        assert r == pytest.approx(1.0)

    def test_retention_decreases_over_time(self, engine):
        r1 = engine.calculate_retention(1, 10)
        r7 = engine.calculate_retention(7, 10)
        assert r1 > r7

    def test_retention_with_zero_stability(self, engine):
        r = engine.calculate_retention(5, 0)
        assert r == 0.0


class TestScheduling:
    def test_default_schedule(self, engine):
        schedule = engine.schedule_unit_reviews('u1', date.today().isoformat())
        assert len(schedule) == 5
        assert schedule[0]['review_number'] == 1
        assert schedule[4]['review_number'] == 5

    def test_custom_intervals(self, engine):
        schedule = engine.schedule_unit_reviews('u1', '2026-01-01', intervals=[1, 7, 30])
        assert len(schedule) == 3
        assert schedule[0]['date'] == '2026-01-02'
        assert schedule[1]['date'] == '2026-01-08'
        assert schedule[2]['date'] == '2026-01-31'

    def test_dates_are_correct(self, engine):
        start = '2026-01-10'
        schedule = engine.schedule_unit_reviews('u1', start)
        # Intervals: 1, 3, 7, 16, 35 days from Jan 10
        expected_dates = ['2026-01-11', '2026-01-13', '2026-01-17', '2026-01-26', '2026-02-14']
        for s, expected in zip(schedule, expected_dates):
            assert s['date'] == expected


class TestProcessReview:
    def test_correct_review_advances(self, engine):
        progress = {'easiness_factor': 2.5, 'interval_days': 0, 'repetitions': 0,
                     'times_reviewed': 0, 'times_correct': 0}
        result = engine.process_review(progress, 4)
        assert result['repetitions'] == 1
        assert result['interval_days'] == 1
        assert result['times_reviewed'] == 1
        assert result['times_correct'] == 1

    def test_failed_review_resets(self, engine):
        progress = {'easiness_factor': 2.5, 'interval_days': 7, 'repetitions': 3,
                     'times_reviewed': 3, 'times_correct': 3}
        result = engine.process_review(progress, 2)
        assert result['repetitions'] == 0
        assert result['interval_days'] == 1
        assert result['times_correct'] == 3  # No increment

    def test_mastery_after_5_reps(self, engine):
        progress = {'easiness_factor': 2.5, 'interval_days': 35, 'repetitions': 4,
                     'times_reviewed': 4, 'times_correct': 4}
        result = engine.process_review(progress, 5)
        assert result['status'] == 'completed'
        assert result['repetitions'] == 5


class TestHandleFailedRecall:
    def test_resets_interval(self, engine):
        progress = {'repetitions': 5, 'interval_days': 35}
        result = engine.handle_failed_recall(progress)
        assert result['repetitions'] == 0
        assert result['interval_days'] == 1


class TestRetentionStats:
    def test_empty_list(self, engine):
        stats = engine.get_retention_stats([])
        assert stats['mastered'] == 0
        assert stats['due'] == 0

    def test_mixed_stats(self, engine):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        items = [
            {'status': 'completed', 'last_review_date': today, 'interval_days': 7,
             'easiness_factor': 2.5, 'next_review_date': (date.today() + timedelta(days=7)).isoformat()},
            {'status': 'in_progress', 'last_review_date': yesterday, 'interval_days': 1,
             'easiness_factor': 2.5, 'next_review_date': today},
        ]
        stats = engine.get_retention_stats(items)
        assert stats['mastered'] == 1
        assert stats['in_progress'] == 1
        assert stats['due'] >= 1
