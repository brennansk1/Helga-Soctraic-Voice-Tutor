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
from unittest.mock import patch

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
        # an easy first answer schedules well out; nothing is due today
        self.assertEqual(self.sm.progress.get_due_reviews(), [])

    def test_completed_reviews_drop_out_of_the_schedule_source(self):
        """Completing a scheduled row removes THAT row from the queue.

        It does not make the concept permanently not-due, and should not: since
        schema v10 the authoritative concept schedule is the FSRS state on
        user_progress, and by a far-future date a healthy concept is of course
        due again. So this asserts the scheduled_review source specifically.
        """
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=3)
        for row in self.sm.schedule.get_scheduled_reviews():
            self.sm.schedule.complete_review(row["id"])
        due = self.sm.progress.get_due_reviews(target_date=FUTURE)
        self.assertFalse(
            [d for d in due if d["concept_uid"] == "con_abc12345"
             and d.get("source") == "scheduled_review"])

    def test_a_freshly_scheduled_concept_is_not_due_again_immediately(self):
        """The FSRS row must sit in the future, not re-fire the same day."""
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=3)
        self.assertFalse(
            [d for d in self.sm.progress.get_due_reviews()
             if d["concept_uid"] == "con_abc12345"])

    def test_concept_appears_once_despite_several_pending_reviews(self):
        # Two reviews of the same concept leave two pending scheduled rows.
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=1)
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=1)
        self.assertEqual(len(self.sm.schedule.get_scheduled_reviews()), 2)
        due = [d for d in self.sm.progress.get_due_reviews(target_date=FUTURE)
               if d["concept_uid"] == "con_abc12345"]
        self.assertEqual(len(due), 1, "the learner must not see one concept twice")

    def test_earliest_pending_date_is_the_one_surfaced(self):
        """A lapse (rating 1) always comes back tomorrow, so a later easy
        answer must not push the surfaced date past the overdue one."""
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=1)   # +1 day
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_other001", "Other", rating=4)        # further out
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
            bloom_level=5, next_review_date="2020-01-01")
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_both0001", "Both", rating=3)
        rows = [d for d in self.sm.progress.get_due_reviews(target_date=FUTURE)
                if d["concept_uid"] == "con_both0001"]
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0].get("source"), "scheduled_review")
        # grade is now 3 -- the latest answer, written by the scheduler -- but
        # bloom_level, which the scheduler does not touch, must survive.
        self.assertEqual(rows[0]["grade"], 3)
        self.assertEqual(rows[0]["bloom_level"], 5)


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


class TestConceptSchedulingIsFSRS(_DBCase):
    """Concept scheduling now runs the engine (schema v10).

    It used to be a fixed grade->interval table: grade 4 always meant [7, 30]
    days, whether it was the learner's first encounter with the concept or
    their tenth consecutive correct recall. That is the difference between
    spaced repetition and a reminder, and it held while the FSRS engine -- 48
    passing tests -- was already scheduling flashcards properly.
    """

    def _state(self, uid="con_abc12345"):
        row = self.sm.progress._get_db().execute(
            "SELECT * FROM user_progress WHERE concept_uid = ?", (uid,)).fetchone()
        return dict(row) if row else None

    def _review(self, rating=3, uid="con_abc12345"):
        self.sm.schedule.schedule_concept_review("course_x", uid, "Pythagoras",
                                                 rating=rating)
        return self._state(uid)

    def _return_after_the_gap(self, st, uid="con_abc12345"):
        """Simulate the learner coming back when the card was actually due."""
        self.sm.progress.update_progress(
            uid, "course_x",
            last_review_date=(date.today()
                              - timedelta(days=st["interval_days"])).isoformat())

    def test_memory_state_is_persisted(self):
        st = self._review()
        self.assertIsNotNone(st["stability"])
        self.assertIsNotNone(st["difficulty"])
        self.assertGreater(st["stability"], 0)

    def test_the_interval_grows_with_demonstrated_retention(self):
        """The property the fixed table could not have: four successful recalls
        push the concept progressively further out."""
        intervals = []
        for _ in range(4):
            st = self._review(rating=3)
            intervals.append(st["interval_days"])
            self._return_after_the_gap(st)
        self.assertEqual(sorted(intervals), intervals,
                         f"intervals must be non-decreasing, got {intervals}")
        self.assertGreater(
            intervals[-1], intervals[0] * 3,
            f"a fourth correct recall must schedule far beyond the first "
            f"({intervals}); under the old fixed table every entry was 3"
        )

    def test_easy_schedules_further_out_than_hard(self):
        hard = self._review(rating=2, uid="con_hard0001")
        easy = self._review(rating=4, uid="con_easy0001")
        self.assertGreater(easy["interval_days"], hard["interval_days"])

    def test_a_lapse_returns_the_concept_tomorrow_and_is_counted(self):
        st = self._review(rating=3)
        self._return_after_the_gap(st)
        lapsed = self._review(rating=1)
        self.assertEqual(lapsed["interval_days"], 1)
        self.assertEqual(lapsed["lapses"], 1)

    def test_a_lapse_does_not_increase_stability(self):
        first = self._review(rating=3)
        self._return_after_the_gap(first)
        lapsed = self._review(rating=1)
        self.assertLessEqual(lapsed["stability"], first["stability"])

    def test_one_row_per_review_not_a_pile(self):
        """The fixed table wrote TWO scheduled rows per answer, so a single
        answer created a backlog. FSRS computes the next interval at the next
        review, from what the learner does then."""
        self._review(rating=3)
        self.assertEqual(len(self.sm.schedule.get_scheduled_reviews()), 1)

    def test_the_schedule_row_matches_the_fsrs_interval(self):
        st = self._review(rating=3)
        rows = self.sm.schedule.get_scheduled_reviews()
        self.assertEqual(
            rows[0]["scheduled_date"],
            (date.today() + timedelta(days=st["interval_days"])).isoformat(),
            "the calendar and the engine must not disagree about the date"
        )

    def test_accuracy_is_recorded(self):
        """times_correct answers "what do I actually know?" and was NEVER
        written — the only code computing it lived in the SM-2 module, which
        has zero callers. Any progress surface built on it would have shown a
        flat zero for every learner."""
        for rating in (4, 3, 1, 3, 2):
            st = self._review(rating=rating)
            self._return_after_the_gap(st)
        st = self._state()
        self.assertEqual(st["times_reviewed"], 5)
        self.assertEqual(st["times_correct"], 3, "ratings 4, 3, 3 count as correct")

    def test_a_wrong_answer_does_not_count_as_correct(self):
        st = self._review(rating=1)
        self.assertEqual(st["times_reviewed"], 1)
        self.assertEqual(st["times_correct"], 0)

    def test_a_correct_first_answer_counts(self):
        self.assertEqual(self._review(rating=3)["times_correct"], 1)

    def test_review_count_increments(self):
        self.assertEqual(self._review()["times_reviewed"], 1)
        st = self._state()
        self._return_after_the_gap(st)
        self.assertEqual(self._review()["times_reviewed"], 2)

    def test_falls_back_to_fixed_intervals_when_the_engine_is_unavailable(self):
        """A scheduler that raises on an import failure loses the review."""
        import builtins
        real_import = builtins.__import__

        def boom(name, *a, **kw):
            if "fsrs_engine" in name:
                raise ImportError("engine missing")
            return real_import(name, *a, **kw)

        with patch.object(builtins, "__import__", side_effect=boom):
            self.sm.schedule.schedule_concept_review(
                "course_x", "con_fallback", "Fallback", rating=4)
        dates = sorted(r["scheduled_date"]
                       for r in self.sm.schedule.get_scheduled_reviews())
        self.assertEqual(
            dates,
            [(date.today() + timedelta(days=d)).isoformat() for d in (7, 30)],
            "with no engine, the old fixed table is the degraded path"
        )


class TestUpsertDoesNotWipeUnsuppliedColumns(_DBCase):
    """update_progress was INSERT OR REPLACE, which deletes the row and writes
    a new one -- so every column the caller did not pass reverted to its
    default. update_progress(status="completed") erased grade, bloom_level and
    the review schedule, and the next call erased what that one set. Both are
    real call sites in fsm_logic. FSRS memory state would have been wiped the
    same way, which is how this was found."""

    def test_a_later_partial_update_preserves_earlier_fields(self):
        self.sm.progress.update_progress(
            "con_keep0001", "course_x", status="completed", grade=4,
            bloom_level=5, times_correct=7)
        self.sm.progress.update_progress("con_keep0001", "course_x",
                                         status="reviewed")
        row = dict(self.sm.progress._get_db().execute(
            "SELECT * FROM user_progress WHERE concept_uid = 'con_keep0001'"
        ).fetchone())
        self.assertEqual(row["status"], "reviewed")
        self.assertEqual(row["grade"], 4)
        self.assertEqual(row["bloom_level"], 5)
        self.assertEqual(row["times_correct"], 7)

    def test_fsrs_state_survives_an_unrelated_progress_update(self):
        self.sm.schedule.schedule_concept_review(
            "course_x", "con_abc12345", "Pythagoras", rating=3)
        before = dict(self.sm.progress._get_db().execute(
            "SELECT stability, difficulty FROM user_progress "
            "WHERE concept_uid = 'con_abc12345'").fetchone())
        self.sm.progress.update_progress("con_abc12345", "course_x",
                                         status="completed")
        after = dict(self.sm.progress._get_db().execute(
            "SELECT stability, difficulty FROM user_progress "
            "WHERE concept_uid = 'con_abc12345'").fetchone())
        self.assertEqual(before, after,
                         "marking a concept completed must not erase its memory")

    def test_the_row_is_still_created_when_absent(self):
        self.sm.progress.update_progress("con_new00001", "course_x",
                                         status="completed")
        row = self.sm.progress._get_db().execute(
            "SELECT status FROM user_progress WHERE concept_uid = 'con_new00001'"
        ).fetchone()
        self.assertEqual(row["status"], "completed")

    def test_schema_is_at_v10(self):
        v = self.sm.progress._get_db().execute(
            "SELECT version FROM schema_version").fetchone()["version"]
        self.assertGreaterEqual(v, 10)


class TestMigrationFromV9(_DBCase):
    """A migration is only real if it runs on a database that already has a
    learner's history in it. Fresh-create passing proves nothing."""

    def _downgrade_to_v9(self):
        """Strip the v10 columns and rewind the version, as a real pre-v10 DB."""
        import sqlite3
        path = os.path.join(self.dir, "helga.db")
        conn = sqlite3.connect(path)
        for col in ("stability", "difficulty", "lapses"):
            try:
                conn.execute(f"ALTER TABLE user_progress DROP COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        conn.execute("UPDATE schema_version SET version = 9")
        conn.commit()
        conn.close()

    def test_existing_progress_survives_the_migration(self):
        self.sm.progress.update_progress(
            "con_legacy01", "course_x", status="completed", grade=4,
            bloom_level=3, times_correct=2)
        self._downgrade_to_v9()

        reopened = StorageManager(data_dir=self.dir)
        conn = reopened.progress._get_db()
        self.assertGreaterEqual(
            conn.execute("SELECT version FROM schema_version").fetchone()["version"],
            10)
        row = dict(conn.execute(
            "SELECT * FROM user_progress WHERE concept_uid = 'con_legacy01'"
        ).fetchone())
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["grade"], 4)
        self.assertEqual(row["bloom_level"], 3)
        self.assertEqual(row["times_correct"], 2)

    def test_a_pre_v10_concept_has_null_memory_and_takes_the_first_review_path(self):
        self.sm.progress.update_progress(
            "con_legacy01", "course_x", status="completed", grade=4)
        self._downgrade_to_v9()

        reopened = StorageManager(data_dir=self.dir)
        conn = reopened.progress._get_db()
        row = dict(conn.execute(
            "SELECT stability, lapses FROM user_progress "
            "WHERE concept_uid = 'con_legacy01'").fetchone())
        self.assertIsNone(row["stability"],
                          "a concept never reviewed under FSRS has no memory yet")
        self.assertEqual(row["lapses"], 0)

        reopened.schedule.schedule_concept_review(
            "course_x", "con_legacy01", "Legacy", rating=3)
        after = dict(conn.execute(
            "SELECT stability, interval_days FROM user_progress "
            "WHERE concept_uid = 'con_legacy01'").fetchone())
        self.assertIsNotNone(after["stability"])
        self.assertGreater(after["interval_days"], 0)

    def test_migration_is_idempotent(self):
        self._downgrade_to_v9()
        StorageManager(data_dir=self.dir)
        StorageManager(data_dir=self.dir)  # must not raise on the second pass
        conn = StorageManager(data_dir=self.dir).progress._get_db()
        self.assertGreaterEqual(
            conn.execute("SELECT version FROM schema_version").fetchone()["version"],
            10)


if __name__ == "__main__":
    unittest.main()
