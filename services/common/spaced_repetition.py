"""
Spaced Repetition Engine for Helga.

Implements SM-2 algorithm with Ebbinghaus retention modeling.
Used for scheduling reviews after unit completion.

Standard intervals: 1, 3, 7, 16, 35 days
"""

import math
import logging
from datetime import date, timedelta
from typing import Tuple, List, Dict, Optional

logger = logging.getLogger(__name__)


class SM2Engine:
    """SM-2 algorithm with Ebbinghaus retention modeling."""

    # Standard review intervals (days after learning)
    BASE_INTERVALS = [1, 3, 7, 16, 35]

    def calculate_next_review(self, repetitions: int, easiness_factor: float,
                                interval: int, grade: int) -> Tuple[int, float, int]:
        """
        SM-2 core algorithm.

        Args:
            repetitions: consecutive correct answers
            easiness_factor: current EF (starts at 2.5)
            interval: current interval in days
            grade: user grade (1-5)
                1 = complete blackout
                2 = incorrect, but upon seeing correct answer, felt familiar
                3 = correct with serious difficulty
                4 = correct after hesitation
                5 = perfect recall

        Returns:
            (new_interval, new_ef, new_repetitions)
        """
        # Grade below 3 = failure → reset
        if grade < 3:
            new_repetitions = 0
            new_interval = 1
        else:
            new_repetitions = repetitions + 1
            if new_repetitions == 1:
                new_interval = 1
            elif new_repetitions == 2:
                new_interval = 3
            elif new_repetitions == 3:
                new_interval = 7
            else:
                new_interval = max(1, round(interval * easiness_factor))

        # Update easiness factor
        new_ef = easiness_factor + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
        new_ef = max(1.3, new_ef)  # EF should never go below 1.3

        return new_interval, new_ef, new_repetitions

    def calculate_retention(self, days_since_review: float, stability: float) -> float:
        """
        Ebbinghaus forgetting curve: R = e^(-t/S)

        Args:
            days_since_review: time since last review in days
            stability: memory stability (higher = slower forgetting)

        Returns:
            Retention probability (0.0 to 1.0)
        """
        if stability <= 0:
            return 0.0
        return math.exp(-days_since_review / stability)

    def schedule_unit_reviews(self, unit_uid: str, start_date: str,
                               intervals: List[int] = None) -> List[Dict]:
        """DEPRECATED: Use FSRS-based flashcard scheduling instead.
        Generate the review schedule for a unit.

        Args:
            unit_uid: unit identifier
            start_date: ISO date string (date of completion)
            intervals: custom intervals, defaults to [1, 3, 7, 16, 35]

        Returns:
            List of {"date": "YYYY-MM-DD", "review_number": N}
        """
        if intervals is None:
            intervals = self.BASE_INTERVALS

        base = date.fromisoformat(start_date)
        schedule = []
        for i, days in enumerate(intervals, 1):
            review_date = base + timedelta(days=days)
            schedule.append({
                "unit_uid": unit_uid,
                "date": review_date.isoformat(),
                "review_number": i,
            })
        return schedule

    def handle_failed_recall(self, current_progress: dict) -> dict:
        """
        Reset interval to Review 1 on failure.

        Args:
            current_progress: dict with SM-2 fields

        Returns:
            Updated progress dict
        """
        return {
            **current_progress,
            "repetitions": 0,
            "interval_days": 1,
            "next_review_date": (date.today() + timedelta(days=1)).isoformat(),
        }

    def process_review(self, current_progress: dict, grade: int) -> dict:
        """
        Process a review attempt and return updated progress.

        Args:
            current_progress: dict with SM-2 fields (easiness_factor, interval_days, repetitions)
            grade: user grade (1-5)

        Returns:
            Updated progress dict with new SM-2 values
        """
        ef = current_progress.get("easiness_factor", 2.5)
        interval = current_progress.get("interval_days", 0)
        reps = current_progress.get("repetitions", 0)

        new_interval, new_ef, new_reps = self.calculate_next_review(reps, ef, interval, grade)

        today = date.today()
        next_review = today + timedelta(days=new_interval)

        times_reviewed = current_progress.get("times_reviewed", 0) + 1
        times_correct = current_progress.get("times_correct", 0) + (1 if grade >= 3 else 0)

        return {
            **current_progress,
            "grade": grade,
            "easiness_factor": round(new_ef, 4),
            "interval_days": new_interval,
            "repetitions": new_reps,
            "next_review_date": next_review.isoformat(),
            "last_review_date": today.isoformat(),
            "times_reviewed": times_reviewed,
            "times_correct": times_correct,
            "status": "completed" if new_reps >= 5 else "in_progress",
        }

    def get_retention_stats(self, progress_list: List[dict]) -> dict:
        """
        Calculate retention statistics from a list of progress items.

        Returns:
            {"average_retention": float, "mastered": int, "in_progress": int, "due": int}
        """
        today = date.today()
        mastered = 0
        in_progress = 0
        due = 0
        total_retention = 0.0
        count = 0

        for p in progress_list:
            if p.get("status") == "completed":
                mastered += 1
            elif p.get("status") in ("in_progress", "available"):
                in_progress += 1

            last_review = p.get("last_review_date")
            if last_review:
                days_since = (today - date.fromisoformat(last_review)).days
                stability = p.get("interval_days", 1) * p.get("easiness_factor", 2.5)
                retention = self.calculate_retention(days_since, stability)
                total_retention += retention
                count += 1

            next_review = p.get("next_review_date")
            if next_review and date.fromisoformat(next_review) <= today:
                due += 1

        avg_retention = (total_retention / count * 100) if count > 0 else 0.0

        return {
            "average_retention": round(avg_retention, 1),
            "mastered": mastered,
            "in_progress": in_progress,
            "due": due,
        }
