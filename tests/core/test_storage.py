"""Tests for StorageManager and sub-stores (CourseStore, ProgressStore, ScheduleStore, ActivityStore, SettingsStore)."""

import os
import json
import shutil
import tempfile
import pytest
from datetime import date, timedelta

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.storage import utc_today, StorageManager, DEFAULT_STUDENT_ID


@pytest.fixture
def storage(tmp_path):
    """Create a fresh StorageManager with temp directory."""
    sm = StorageManager(str(tmp_path))
    yield sm


class TestStorageManagerInit:
    def test_creates_directories(self, storage, tmp_path):
        assert os.path.isdir(os.path.join(str(tmp_path), 'courses'))

    def test_creates_sqlite_db(self, storage, tmp_path):
        assert os.path.isfile(os.path.join(str(tmp_path), 'helga.db'))

    def test_substores_available(self, storage):
        assert storage.courses is not None
        assert storage.progress is not None
        assert storage.schedule is not None
        assert storage.activity is not None
        assert storage.settings is not None

    def test_reset_clears_data(self, storage, tmp_path):
        # Create some course data first
        storage.courses.create_course({'title': 'Test', 'uid': 'abc'})
        assert len(storage.courses.list_courses()) == 1
        storage.reset()
        assert len(storage.courses.list_courses()) == 0


class TestCourseStore:
    def test_create_and_get(self, storage):
        uid = storage.courses.create_course({'title': 'Physics 101', 'modules': []})
        assert uid is not None
        course = storage.courses.get_course(uid)
        assert course['title'] == 'Physics 101'

    def test_list_courses(self, storage):
        storage.courses.create_course({'title': 'A', 'modules': []})
        storage.courses.create_course({'title': 'B', 'modules': []})
        courses = storage.courses.list_courses()
        assert len(courses) == 2

    def test_delete_course(self, storage):
        uid = storage.courses.create_course({'title': 'Del', 'modules': []})
        storage.courses.delete_course(uid)
        assert storage.courses.get_course(uid) is None

    def test_update_course(self, storage):
        uid = storage.courses.create_course({'title': 'Old', 'modules': []})
        storage.courses.update_course(uid, {'title': 'New', 'uid': uid, 'modules': []})
        assert storage.courses.get_course(uid)['title'] == 'New'

    def test_save_and_get_concept_content(self, storage):
        uid = storage.courses.create_course({'title': 'T', 'modules': []})
        path = storage.courses.save_concept_content(uid, 'c1', '# Hello\nWorld')
        assert os.path.isfile(path)
        content = storage.courses.get_concept_content(uid, 'c1')
        assert '# Hello' in content

    def test_get_flat_concepts(self, storage):
        course = {
            'title': 'Test',
            'modules': [{
                'title': 'M1', 'uid': 'm1',
                'units': [{
                    'title': 'U1', 'uid': 'u1',
                    'lessons': [{
                        'title': 'L1', 'uid': 'l1',
                        'concepts': [
                            {'title': 'C1', 'uid': 'c1'},
                            {'title': 'C2', 'uid': 'c2'},
                        ]
                    }]
                }]
            }]
        }
        uid = storage.courses.create_course(course)
        concepts = storage.courses.get_flat_concepts(uid)
        assert len(concepts) == 2
        assert concepts[0]['title'] == 'C1'

    def test_get_course_stats(self, storage):
        course = {
            'title': 'Stats', 'modules': [{
                'title': 'M', 'uid': 'm',
                'units': [{'title': 'U', 'uid': 'u',
                    'lessons': [{'title': 'L', 'uid': 'l',
                        'concepts': [{'title': 'C', 'uid': 'c'}]}]}]
            }]
        }
        uid = storage.courses.create_course(course)
        stats = storage.courses.get_course_stats(uid)
        assert stats['modules'] == 1
        assert stats['concepts'] == 1


class TestProgressStore:
    def test_update_and_get_progress(self, storage):
        storage.progress.update_progress('c1', 'course1', grade=4, status='in_progress')
        p = storage.progress.get_progress('c1')
        assert p is not None
        assert p['grade'] == 4

    def test_mark_completed(self, storage):
        storage.progress.update_progress('c2', 'course1')
        storage.progress.mark_completed('c2', 'course1')
        p = storage.progress.get_progress('c2')
        assert p['status'] == 'completed'

    def test_get_course_progress(self, storage):
        storage.progress.update_progress('c1', 'cx', grade=3)
        storage.progress.update_progress('c2', 'cx', grade=5)
        progress = storage.progress.get_course_progress('cx')
        assert len(progress) == 2

    def test_empty_course_uid_preserves_existing_link(self, storage):
        # B5.5: a review-only update that omits course_uid must NOT orphan the
        # concept from its course (INSERT OR REPLACE rewrites the whole row).
        storage.progress.update_progress('c9', 'real_course', grade=3, status='in_progress')
        storage.progress.update_progress('c9', '', grade=5, status='reviewed')
        p = storage.progress.get_progress('c9')
        assert p['course_uid'] == 'real_course'
        assert p['grade'] == 5
        assert len(storage.progress.get_course_progress('real_course')) == 1


class TestActivityStreak:
    # Anchored on UTC, not local: get_streak compares against
    # DATE(created_at), which SQLite evaluates in UTC. Using date.today()
    # here made these tests fail for the hours between UTC midnight and
    # local midnight — the same mismatch that was breaking real streaks.
    @staticmethod
    def _add_activity_on(storage, day):
        conn = storage.activity._get_db()
        conn.execute(
            "INSERT INTO activity_log (course_uid, concept_uid, activity_type, details, created_at, student_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("c", "n", "review", "{}", f"{day.isoformat()}T12:00:00", DEFAULT_STUDENT_ID),
        )
        conn.commit()

    def test_no_activity_zero(self, storage):
        assert storage.activity.get_streak() == 0

    def test_today_only(self, storage):
        self._add_activity_on(storage, utc_today())
        assert storage.activity.get_streak() == 1

    def test_consecutive_days(self, storage):
        for d in range(3):
            self._add_activity_on(storage, utc_today() - timedelta(days=d))
        assert storage.activity.get_streak() == 3

    def test_yesterday_only_today_not_logged(self, storage):
        self._add_activity_on(storage, utc_today() - timedelta(days=1))
        assert storage.activity.get_streak() == 1

    def test_gap_breaks_streak(self, storage):
        # Today + 2-days-ago, but NOT yesterday -> streak is 1, not 2.
        # (The old logic over-counted across the gap and returned 2.)
        self._add_activity_on(storage, utc_today())
        self._add_activity_on(storage, utc_today() - timedelta(days=2))
        assert storage.activity.get_streak() == 1

    def test_stale_activity_zero(self, storage):
        # Most recent activity is older than yesterday -> streak broken.
        self._add_activity_on(storage, utc_today() - timedelta(days=3))
        assert storage.activity.get_streak() == 0


class TestScheduleStore:
    def test_schedule_unit_reviews(self, storage):
        today = date.today().isoformat()
        storage.schedule.schedule_unit_reviews('course1', 'u1', 'Unit 1', today)
        reviews = storage.schedule.get_scheduled_reviews(course_uid='course1')
        assert len(reviews) == 5  # default 5 intervals

    def test_complete_review(self, storage):
        today = date.today().isoformat()
        storage.schedule.schedule_unit_reviews('cx', 'u1', 'U', today)
        reviews = storage.schedule.get_scheduled_reviews(course_uid='cx')
        storage.schedule.complete_review(reviews[0]['id'])
        updated = storage.schedule.get_scheduled_reviews(course_uid='cx')
        completed = [r for r in updated if r['status'] == 'completed']
        assert len(completed) == 1

    def test_mark_overdue(self, storage):
        past = (date.today() - timedelta(days=5)).isoformat()
        storage.schedule.schedule_unit_reviews('cx', 'u1', 'U', past, intervals=[1])
        storage.schedule.mark_overdue()
        reviews = storage.schedule.get_scheduled_reviews(course_uid='cx')
        assert any(r['status'] == 'overdue' for r in reviews)

    def test_get_upcoming_count(self, storage):
        today = date.today().isoformat()
        storage.schedule.schedule_unit_reviews('cx', 'u1', 'U', today, intervals=[1, 3])
        count = storage.schedule.get_upcoming_count(days=7)
        assert count >= 1


class TestActivityStore:
    def test_log_and_get_activity(self, storage):
        storage.activity.log_activity('cx', 'concept_completed', concept_uid='c1')
        logs = storage.activity.get_activities(course_uid='cx')
        assert len(logs) >= 1


class TestSettingsStore:
    def test_set_and_get_setting(self, storage):
        storage.settings.set('voice_id', 'Vivian')
        assert storage.settings.get('voice_id') == 'Vivian'

    def test_get_default(self, storage):
        val = storage.settings.get('nonexistent', 'default')
        assert val == 'default'


class TestFlashcardStore:
    def test_add_and_get_cards(self, storage):
        uid = storage.flashcards.add_card("course1", "concept1", "Front", "Back")
        assert uid.startswith("card_")
        cards = storage.flashcards.get_cards_for_concept("concept1")
        assert len(cards) == 1
        assert cards[0]["front"] == "Front"
        assert cards[0]["back"] == "Back"

    def test_get_due_cards(self, storage):
        # Card with no next_review_date should be due
        storage.flashcards.add_card("course1", "concept1", "F1", "B1")
        # Card with past date should be due
        uid2 = storage.flashcards.add_card("course1", "concept2", "F2", "B2")
        past_date = (date.today() - timedelta(days=1)).isoformat()
        storage.flashcards.update_card(uid2, next_review_date=past_date)
        
        # Card with future date should NOT be due
        uid3 = storage.flashcards.add_card("course1", "concept3", "F3", "B3")
        future_date = (date.today() + timedelta(days=1)).isoformat()
        storage.flashcards.update_card(uid3, next_review_date=future_date)

        due = storage.flashcards.get_due_cards("course1")
        assert len(due) == 2
        uids = [c["uid"] for c in due]
        assert uid2 in uids
        assert uid3 not in uids

    def test_update_card(self, storage):
        uid = storage.flashcards.add_card("c1", "co1", "F", "B")
        storage.flashcards.update_card(uid, status="reviewing", interval_days=5)
        cards = storage.flashcards.get_cards_for_concept("co1")
        assert cards[0]["status"] == "reviewing"
        assert cards[0]["interval_days"] == 5

    def test_get_cards_for_course(self, storage):
        storage.flashcards.add_card("course-a", "con1", "F1", "B1")
        storage.flashcards.add_card("course-a", "con2", "F2", "B2")
        storage.flashcards.add_card("course-b", "con3", "F3", "B3")
        
        cards_a = storage.flashcards.get_cards_for_course("course-a")
        assert len(cards_a) == 2
        cards_b = storage.flashcards.get_cards_for_course("course-b")
        assert len(cards_b) == 1


# ---------------------------------------------------------------------------
# ONE DEFINITION OF "DONE".
#
# There were two. The course list counted completed/reviewed/mastered; the
# course-structure endpoint that draws the learn path counted "completed"
# alone. With four concepts sitting at `reviewed`, the SQL course read
# "4 of 95 concepts · 4%" on Courses and "0 of 95 complete · 0%" on Learn —
# the same course, the same learner, two screens one click apart, telling
# someone who had worked through four concepts that they had done nothing.
# ---------------------------------------------------------------------------

def test_reviewed_and_mastered_count_as_done():
    from services.common.storage import ProgressStore
    for status in ("completed", "reviewed", "mastered"):
        assert ProgressStore.is_done(status), status


def test_unfinished_states_do_not_count_as_done():
    from services.common.storage import ProgressStore
    for status in ("in_progress", "locked", "", None, "started"):
        assert not ProgressStore.is_done(status), repr(status)


def test_case_and_whitespace_do_not_change_the_answer():
    from services.common.storage import ProgressStore
    assert ProgressStore.is_done("  Reviewed ")
    assert ProgressStore.is_done("COMPLETED")


def test_a_reviewed_concept_is_done_on_both_paths(storage):
    """The two surfaces read the same rows; they must agree on what they mean."""
    from services.common.storage import ProgressStore
    storage.progress.update_progress('c_rev', 'course_x', status='reviewed')
    rows = storage.progress.get_course_progress('course_x')
    by_uid = {r['concept_uid']: r for r in rows}
    assert ProgressStore.is_done(by_uid['c_rev']['status']), \
        "a reviewed concept must count as done wherever it is read"


def test_neither_surface_hardcodes_its_own_status_list():
    """They drifted because each carried its own tuple. If a literal list
    reappears in librarian they can drift again, and the symptom — 0% beside
    4% — reads to a learner as lost progress."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "services" / "rag" / "librarian.py")
    text = src.read_text()
    for literal in ('"reviewed", "mastered"', "'reviewed', 'mastered'",
                    '"completed", "reviewed"', "'completed', 'reviewed'"):
        assert literal not in text, (
            "librarian.py hardcodes a done-status list again; call "
            "storage.progress.is_done() instead")


# ---------------------------------------------------------------------------
# HYDRATION PROGRESS REACHES THE DATABASE MORE THAN ONCE.
#
# hydrated_count was persisted exactly once — as the literal 1, when the first
# concept landed — and not again until the phase ended. The course card renders
# "N of M concepts" from that column, so a 136-concept build showed "0 of 136"
# for hours with 18 markdown files already on disk.
# ---------------------------------------------------------------------------

def test_hydrated_count_can_be_updated_repeatedly(storage):
    uid = "course_hyd0001"
    storage.courses.create_course({"uid": uid, "title": "Hydration",
                                   "modules": []})
    for n in (1, 7, 42):
        assert storage.courses.set_hydrated_count(uid, n) is True
        assert storage.courses.get_course(uid).get("hydrated_count") == n or \
            _row_count(storage, uid) == n


def _row_count(storage, uid):
    conn = storage.courses._get_db()
    row = conn.execute("SELECT hydrated_count FROM courses WHERE uid = ?",
                       (uid,)).fetchone()
    return row[0] if row else None


def test_a_counter_failure_never_raises(storage):
    """A progress number must not be able to fail a build."""
    assert storage.courses.set_hydrated_count("course_does_not_exist", 3) in (
        True, False)          # no exception either way


def test_the_builder_updates_the_counter_past_the_first_concept():
    """The bug was structural: the only write sat behind `if
    hydrated_count == 1`. Guard the shape, since reproducing a 136-concept
    hydration in a unit test is not practical."""
    import inspect
    from services.core import course_builder
    src = inspect.getsource(course_builder)
    assert "set_hydrated_count(" in src, (
        "the hydration loop no longer reports progress per concept")


# ---------------------------------------------------------------------------
# PROGRESS MUST CARRY THE COURSE IT BELONGS TO.
#
# The flashcard review path wrote `course_uid=self.active_course_uid or ""`.
# A review session interleaves courses, so active_course_uid is normally None,
# and every row written that way landed under course_uid="".
# get_course_progress() filters ON that column, so the work was invisible to
# the course: the learn path never turned the node green and the progress
# percentage never counted it.
#
# Found after a 20-question Socratic session on SELECT Clause Syntax: the row
# read course_uid='' with times_reviewed=38, sitting outside the course.
# ---------------------------------------------------------------------------

def test_progress_written_without_a_course_is_invisible_to_that_course(storage):
    """The mechanism, so the fix has something to be measured against."""
    storage.progress.update_progress("con_orphan", "", status="reviewed")
    assert storage.progress.get_course_progress("course_x") == [], \
        "a row with an empty course_uid must not appear under a real course"


def test_progress_written_with_its_course_is_visible(storage):
    storage.progress.update_progress("con_ok", "course_x", status="reviewed")
    rows = storage.progress.get_course_progress("course_x")
    assert [r["concept_uid"] for r in rows] == ["con_ok"]


def test_the_review_path_resolves_a_course_before_writing():
    """Guards the shape: the progress write must not fall back to "".

    Read as TEXT rather than imported — fsm_logic imports fsrs_engine by a
    container-relative path and cannot be imported on the host, and skipping
    the check there would leave it unguarded where it is usually run.

    Scoped to the update_progress call on purpose. `active_course_uid or ""`
    also appears in the Memory Palace's log_activity call, which is a different
    table with a much smaller consequence; banning the string outright would
    make this fail for the wrong reason and invite weakening it.
    """
    import pathlib
    import re
    from services.common.storage import CourseStore

    src = (pathlib.Path(__file__).resolve().parents[2]
           / "services" / "core" / "fsm_logic.py").read_text()

    for m in re.finditer(r"update_progress\((?:[^()]|\([^()]*\))*\)", src):
        call = m.group(0)
        assert 'active_course_uid or ""' not in call, (
            "a progress write falls back to an empty course_uid, which "
            f"orphans the row from its course: {call[:120]}")

    assert "find_concept_across_courses" in src, \
        "no fallback lookup when no course is active"
    assert hasattr(CourseStore, "find_concept_across_courses"), \
        "the fallback calls a method that does not exist"
