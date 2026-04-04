"""Tests for StorageManager and sub-stores (CourseStore, ProgressStore, ScheduleStore, ActivityStore, SettingsStore)."""

import os
import json
import shutil
import tempfile
import pytest
from datetime import date, timedelta

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.storage import StorageManager


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
