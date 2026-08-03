"""The spaced-repetition loop, exercised end to end against a real SQLite DB.

Criterion 4 ("reviewed on schedule") was BUILT but never run end to end, and
that gap hid a defect no unit test could see: the FSRS engine had 48 passing
tests, storage had its own, and the loop between them was broken.

`get_due_reviews()` read only `user_progress.next_review_date`. Nothing in the
system writes that column — reviews are scheduled into `scheduled_reviews` —
so it stayed NULL, and `NULL <= '<date>'` is NULL in SQL. Every row was
filtered out. Measured against a real DB before the fix: two scheduled reviews
present, zero returned, even with target_date set to 2099.

The user-visible effect: the FSM's spoken review mode answered "No cards due
for review right now" no matter how much the learner had studied, and the
parent dashboard reported a permanent zero.

These tests drive real stores on a temp DB rather than mocks, because mocks are
exactly what let the two halves drift apart.
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common.storage import StorageManager  # noqa: E402
from services.core.fsrs_engine import FSRSEngine  # noqa: E402

FUTURE = "2099-01-01"


class _DBCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sm = StorageManager(data_dir=self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestScheduledReviewsReachTheDueQueue(_DBCase):
    """The regression that made the whole mode dead."""

    def test_a_scheduled_concept_becomes_due(self):
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=3)
        due = self.sm.progress.get_due_reviews(target_date=FUTURE)
        self.assertTrue(
            any(d["concept_uid"] == "con_abc12345" for d in due),
            "A review the system itself scheduled must appear in the due "
            "queue. It did not, which is why the spoken review mode always "
            "reported nothing due."
        )

    def test_due_queue_is_not_permanently_empty(self):
        for i in range(3):
            self.sm.schedule.schedule_concept_review(
                "course_x", f"con_{i:08d}", f"Concept {i}", rating=2)
        self.assertGreaterEqual(
            len(self.sm.progress.get_due_reviews(target_date=FUTURE)), 3)

    def test_nothing_is_due_before_its_scheduled_date(self):
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=4)
        # grade 4 schedules at +7 and +30 days; nothing is due today
        self.assertEqual(self.sm.progress.get_due_reviews(), [])

    def test_completed_reviews_drop_out(self):
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=3)
        for row in self.sm.schedule.get_scheduled_reviews():
            self.sm.schedule.complete_review(row["id"])
        due = self.sm.progress.get_due_reviews(target_date=FUTURE)
        self.assertFalse([d for d in due if d["concept_uid"] == "con_abc12345"])

    def test_concept_appears_once_despite_several_pending_reviews(self):
        # schedule_concept_review writes TWO rows (a first and a follow-up)
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=1)
        due = [d for d in self.sm.progress.get_due_reviews(target_date=FUTURE)
               if d["concept_uid"] == "con_abc12345"]
        self.assertEqual(len(due), 1, "the learner must not see one concept twice")

    def test_earliest_pending_date_is_the_one_surfaced(self):
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=1)  # +1 and +3 days
        due = [d for d in self.sm.progress.get_due_reviews(target_date=FUTURE)
               if d["concept_uid"] == "con_abc12345"][0]
        self.assertEqual(due["next_review_date"],
                         (date.today() + timedelta(days=1)).isoformat())

    def test_reviews_are_scoped_to_the_student(self):
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=3,
            student_id="student_a")
        other = self.sm.progress.get_due_reviews(
            target_date=FUTURE, student_id="student_b")
        self.assertFalse([d for d in other if d["concept_uid"] == "con_abc12345"])

    def test_rows_carry_what_the_caller_needs(self):
        """The FSM reads concept_uid off each row and looks the concept up."""
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=3)
        row = self.sm.progress.get_due_reviews(target_date=FUTURE)[0]
        for key in ("concept_uid", "course_uid", "next_review_date"):
            self.assertIn(key, row)
        self.assertEqual(row["course_uid"], "course_x")


class TestProgressRowsStillWork(_DBCase):
    """Unioning the second source must not break the original one."""

    def test_progress_row_with_a_due_date_is_returned(self):
        self.sm.progress.update_progress(
            "con_prog0001", "course_x", status="reviewed",
            next_review_date="2020-01-01")
        due = self.sm.progress.get_due_reviews()
        self.assertTrue(any(d["concept_uid"] == "con_prog0001" for d in due))

    def test_locked_concepts_are_never_due(self):
        self.sm.progress.update_progress(
            "con_locked01", "course_x", status="locked",
            next_review_date="2020-01-01")
        due = self.sm.progress.get_due_reviews()
        self.assertFalse([d for d in due if d["concept_uid"] == "con_locked01"])

    def test_null_review_date_is_not_treated_as_due(self):
        self.sm.progress.update_progress(
            "con_null0001", "course_x", status="completed")
        due = self.sm.progress.get_due_reviews(target_date=FUTURE)
        self.assertFalse(
            [d for d in due if d.get("source") != "scheduled_review"
             and d["concept_uid"] == "con_null0001"],
            "an unscheduled concept is not a due review"
        )

    def test_progress_row_wins_over_the_bare_schedule_row(self):
        """It carries grade and bloom history the schedule row does not."""
        self.sm.progress.update_progress(
            "con_both0001", "course_x", status="reviewed", grade=4,
            next_review_date="2020-01-01")
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_both0001", "Both", rating=3)
        rows = [d for d in self.sm.progress.get_due_reviews(target_date=FUTURE)
                if d["concept_uid"] == "con_both0001"]
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0].get("source"), "scheduled_review")
        self.assertEqual(rows[0]["grade"], 4)


class TestFlashcardFSRSRoundTrip(_DBCase):
    """Card -> due -> graded -> rescheduled -> persisted, on a real DB.

    This is the half of criterion 4 that IS genuinely FSRS: stability and
    difficulty are computed by the engine and written back, so the schedule
    depends on review history rather than on the last grade alone.
    """

    def setUp(self):
        super().setUp()
        self.engine = FSRSEngine()
        self.uid = self.sm.flashcards.add_card(
            "course_x", "con_abc12345", "a^2 + b^2 = ?", "c^2")

    def test_a_new_card_is_due(self):
        due = self.sm.flashcards.get_due_cards(course_uid="course_x")
        self.assertTrue(any(c["uid"] == self.uid for c in due))

    def test_grading_reschedules_into_the_future(self):
        res = self.sm.flashcards.grade_card_fsrs(self.uid, 3, self.engine)
        self.assertGreater(res["interval_days"], 0)
        self.assertGreater(res["next_review_date"], date.today().isoformat())

    def test_graded_card_leaves_the_due_queue(self):
        self.sm.flashcards.grade_card_fsrs(self.uid, 4, self.engine)
        due = self.sm.flashcards.get_due_cards(course_uid="course_x")
        self.assertFalse([c for c in due if c["uid"] == self.uid])

    def test_memory_state_is_persisted_not_just_returned(self):
        self.sm.flashcards.grade_card_fsrs(self.uid, 3, self.engine)
        row = self.sm.flashcards.get_due_cards(
            course_uid="course_x", target_date=FUTURE)
        card = [c for c in row if c["uid"] == self.uid][0]
        self.assertIsNotNone(card["stability"])
        self.assertIsNotNone(card["difficulty"])
        self.assertGreater(card["stability"], 0)

    def test_a_lapse_brings_the_card_back_tomorrow(self):
        res = self.sm.flashcards.grade_card_fsrs(self.uid, 1, self.engine)
        self.assertEqual(res["interval_days"], 1)
        self.assertEqual(res["lapses"], 1)

    def test_easy_schedules_further_out_than_hard(self):
        hard = self.sm.flashcards.add_card("course_x", "con_h", "h", "h")
        easy = self.sm.flashcards.add_card("course_x", "con_e", "e", "e")
        h = self.sm.flashcards.grade_card_fsrs(hard, 2, self.engine)
        e = self.sm.flashcards.grade_card_fsrs(easy, 4, self.engine)
        self.assertGreater(e["interval_days"], h["interval_days"])

    def test_same_day_re_review_does_not_inflate_the_schedule(self):
        """Grading a card twice in one day must NOT double its interval.

        FSRS gains stability from recall after time has passed; with
        days_elapsed = 0 retrievability is ~1, so there is almost nothing to
        learn from the second answer. A scheduler that stretched the interval
        anyway would let a learner game the queue by re-answering.
        """
        first = self.sm.flashcards.grade_card_fsrs(self.uid, 3, self.engine)
        second = self.sm.flashcards.grade_card_fsrs(self.uid, 3, self.engine)
        self.assertEqual(second["interval_days"], first["interval_days"])

    def test_recall_after_a_delay_lengthens_the_interval(self):
        """The property that distinguishes FSRS from a fixed grade->days map:
        the schedule depends on accumulated history, not just this grade."""
        first = self.sm.flashcards.grade_card_fsrs(self.uid, 3, self.engine)
        # Simulate the learner actually coming back later, which is the only
        # condition under which recall is evidence of durable memory.
        self.sm.flashcards.update_card(
            self.uid,
            last_review_date=(date.today() - timedelta(days=10)).isoformat())
        second = self.sm.flashcards.grade_card_fsrs(self.uid, 3, self.engine)
        self.assertGreater(
            second["interval_days"], first["interval_days"],
            "recalling a card after 10 days must push it further out"
        )
        self.assertGreater(second["stability"], first["stability"])


class TestConceptSchedulingIsNotYetFSRS(_DBCase):
    """Honest boundary marker, not an aspiration.

    Concept-level scheduling uses a fixed grade->interval table; only the
    flashcard path runs the engine. Anything claiming criterion 4 is
    "FSRS end to end" is overclaiming, so the boundary is asserted rather than
    left to be rediscovered.
    """

    def test_concept_intervals_come_from_the_fixed_table(self):
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=4)
        dates = sorted(r["scheduled_date"]
                       for r in self.sm.schedule.get_scheduled_reviews())
        self.assertEqual(
            dates,
            [(date.today() + timedelta(days=d)).isoformat() for d in (7, 30)],
            "grade 4 maps to a hardcoded [7, 30]; if this ever becomes "
            "history-dependent, criterion 4 can finally claim FSRS end to end"
        )

    def test_concept_scheduling_ignores_review_history(self):
        for _ in range(3):
            self.sm.schedule.schedule_concept_review(
                "course_x", "con_abc12345", "Pythagoras", rating=3)
        offsets = {r["scheduled_date"] for r in self.sm.schedule.get_scheduled_reviews()}
        self.assertEqual(
            len(offsets), 2,
            "three reviews produce the same two dates -- proof the interval "
            "does not depend on history the way the flashcard path does"
        )


if __name__ == "__main__":
    unittest.main()
