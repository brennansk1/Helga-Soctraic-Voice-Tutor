"""Tests for CLAUDE.md implementation fixes.

Covers:
- SET_CONTEXT handler (LRN-2)
- RESUME_COURSE global handler (LRN-4)
- PAUSE_SESSION handler (LRN-9)
- Atomic write for progress save (LRN-8)
- Transcript capping (PERF-5)
- repair_json utility (LLM-1)
- validate_schema utility (LLM-2)
- Column whitelist validation (BUG-7)
- Storage indexes (BUG-9)
"""

import sys
import os
import json
import tempfile
import sqlite3
import pytest

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from unittest.mock import MagicMock, patch

# Mock dependencies before importing fsm_logic
class _MockFlaskApp:
    def __init__(self, *a, **kw): pass
    def route(self, *a, **kw):
        return lambda f: f
    def run(self, *a, **kw): pass

flask_mock = MagicMock()
flask_mock.Flask = _MockFlaskApp
flask_mock.request = MagicMock()
fsrs_mock = MagicMock()
cb_mock = MagicMock()
cb_mock.__file__ = 'mocked_course_builder.py'

core_deps = {
    'kuzu': MagicMock(),
    'libzim': MagicMock(),
    'sentence_transformers': MagicMock(),
    'psutil': MagicMock(),
    'yaml': MagicMock(),
    'fsrs_engine': fsrs_mock,
    'safety': MagicMock(),
    'service_manager': MagicMock(),
    'db_manager': MagicMock(),
    'content_provider': MagicMock(),
    'course_builder': cb_mock,
}

with patch.dict('sys.modules', core_deps):
    from services.core.fsm_logic import MnemosyneFSM
    from services.common.llm_utils import repair_json, validate_schema
    from services.common.storage import StorageManager


# --- FSM Logic Tests ---

def _make_fsm():
    """Create a minimal FSM instance with mocked init."""
    with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
        fsm = MnemosyneFSM.__new__(MnemosyneFSM)
        fsm.state = 'LOBBY'
        fsm.active_course_uid = None
        fsm.current_teaching_style = ''
        fsm.storage = MagicMock()
        fsm.speak = MagicMock()
        fsm.play_sound = MagicMock()
        fsm.stop_audio = MagicMock()
        fsm.send_status_update = MagicMock()
        fsm.start_listening = MagicMock()
        fsm.last_interaction_time = 0
        fsm.current_lesson_node = None
        fsm.syllabus_queue = []
        fsm.completed_topics = set()
        fsm.transcript = []
        fsm.conversation_history = []
        fsm.tts_enabled = True
        fsm.user_settings = {"voice_id": "Vivian"}
        fsm.audio_url = 'http://mock:5000'
        fsm.web_ui_url = 'http://mock:5050'
        fsm.maintenance_paused = False
        fsm.data_root = '/tmp/test_data'
        fsm.state_file = '/tmp/test_data/user_state.json'
        fsm.socratic_type_index = 0
        fsm.concept_correct_streak = 0
        fsm.concept_question_count = 0
        fsm.current_bloom_level = 1
        fsm.bloom_correct_streak = 0
        fsm.current_locus_uid = None
        fsm.current_locus_desc = None
        fsm.last_lesson_title = None
        fsm.current_context = ""
        fsm.course_bloom_floor = 1
        fsm.course_bloom_ceiling = 6
        fsm.concept_bloom_target = None
        fsm.passed_question_types = set()
        fsm.prior_concepts_summary = []
        return fsm


class TestSetContextHandler:
    """LRN-2: SET_CONTEXT event handler."""

    def test_set_context_sets_active_course_uid(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = {
            'title': 'Test Course',
            'teaching_style': 'Socratic'
        }
        
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_abc123'}
        })
        
        assert fsm.active_course_uid == 'course_abc123'
        assert fsm.current_teaching_style == 'Socratic'

    def test_set_context_loads_teaching_style(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = {
            'title': 'Bio 101',
            'teaching_style': 'ELI5'
        }
        
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_xyz'}
        })
        
        assert fsm.current_teaching_style == 'ELI5'

    def test_set_context_handles_missing_course(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = None
        
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'nonexistent'}
        })
        
        assert fsm.active_course_uid == 'nonexistent'
        assert fsm.current_teaching_style == ''

    def test_set_context_without_uid_is_noop(self):
        fsm = _make_fsm()
        fsm.active_course_uid = 'existing'
        
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {}
        })
        
        # Should not change existing uid
        assert fsm.active_course_uid == 'existing'


class TestResumeCoursGlobal:
    """LRN-4: RESUME_COURSE as global handler (works in any state)."""

    def test_resume_course_from_lobby(self):
        fsm = _make_fsm()
        fsm.state = 'LOBBY'
        fsm.resume_course = MagicMock()
        
        fsm.transition({
            'type': 'RESUME_COURSE',
            'payload': {'uid': 'course_123', 'title': 'Physics'}
        })
        
        fsm.resume_course.assert_called_once_with('course_123', 'Physics')

    def test_resume_course_from_socratic_learning(self):
        fsm = _make_fsm()
        fsm.state = 'SOCRATIC_LEARNING'
        fsm.active_course_uid = 'old_course'
        fsm._save_current_course_progress = MagicMock()
        fsm.resume_course = MagicMock()
        
        fsm.transition({
            'type': 'RESUME_COURSE',
            'payload': {'uid': 'new_course', 'title': 'Chemistry'}
        })
        
        fsm._save_current_course_progress.assert_called_once()
        fsm.resume_course.assert_called_once_with('new_course', 'Chemistry')

    def test_resume_course_from_spaced_repetition(self):
        fsm = _make_fsm()
        fsm.state = 'SPACED_REPETITION'
        fsm.resume_course = MagicMock()
        
        fsm.transition({
            'type': 'RESUME_COURSE',
            'payload': {'uid': 'course_456', 'title': 'Math'}
        })
        
        fsm.resume_course.assert_called_once_with('course_456', 'Math')


class TestPauseSessionHandler:
    """LRN-9: PAUSE_SESSION event handler."""

    def test_pause_session_saves_progress(self):
        fsm = _make_fsm()
        fsm.state = 'SOCRATIC_LEARNING'
        fsm.active_course_uid = 'course_123'
        fsm._save_current_course_progress = MagicMock()
        
        fsm.transition({
            'type': 'PAUSE_SESSION',
            'payload': {}
        })
        
        fsm._save_current_course_progress.assert_called_once()
        fsm.stop_audio.assert_called_once()

    def test_pause_session_in_lobby_is_noop(self):
        fsm = _make_fsm()
        fsm.state = 'LOBBY'
        fsm._save_current_course_progress = MagicMock()
        
        fsm.transition({
            'type': 'PAUSE_SESSION',
            'payload': {}
        })
        
        fsm._save_current_course_progress.assert_not_called()


class TestTranscriptCapping:
    """PERF-5: Transcript should be capped at 50 entries."""

    def test_speak_caps_transcript(self):
        fsm = _make_fsm()
        fsm.tts_enabled = False  # Skip TTS to avoid network calls
        
        # Fill transcript beyond limit
        fsm.transcript = [{'sender': 'helga', 'text': f'msg {i}'} for i in range(55)]
        
        # Call the REAL speak method (not the mock)
        MnemosyneFSM.speak(fsm, "New message")
        
        assert len(fsm.transcript) == 50
        # Most recent message should be the new one
        assert fsm.transcript[-1]['text'] == 'New message'


class TestAtomicWrite:
    """B15.7 (supersedes LRN-8): progress save is a single-row fsm_sessions
    upsert in WAL — atomic by construction, no file write at all."""

    def test_save_upserts_fsm_session_row(self):
        fsm = _make_fsm()
        fsm.student_id = 'stu_test0001'
        fsm.grade_band = '9-12'
        fsm.state = 'SOCRATIC_LEARNING'
        fsm.active_course_uid = 'course_abc'
        fsm.current_lesson_node = {'uid': 'con_1', 'title': 'Test'}
        fsm.syllabus_queue = []
        fsm.completed_topics = set(['con_0'])
        fsm.transcript = []
        fsm.conversation_history = []
        fsm.socratic_type_index = 0
        fsm.concept_correct_streak = 0
        fsm.concept_question_count = 0
        fsm.current_bloom_level = 1
        fsm.bloom_correct_streak = 0
        fsm.current_locus_uid = None
        fsm.current_locus_desc = ''
        fsm.concept_bloom_target = None
        fsm.passed_question_types = set()
        fsm.prior_concepts_summary = []
        fsm.course_bloom_floor = 1
        fsm.course_bloom_ceiling = 6
        fsm.storage.fsm.get.return_value = None
        fsm.state_file = '/nonexistent/user_state.json'

        fsm._save_current_course_progress()

        fsm.storage.fsm.upsert.assert_called_once()
        sid, blob = fsm.storage.fsm.upsert.call_args[0]
        assert sid == 'stu_test0001'
        data = json.loads(blob)
        assert data['courses']['course_abc']['current_node']['uid'] == 'con_1'
        assert data['last_active_uid'] == 'course_abc'


# --- LLM Utils Tests ---

class TestRepairJson:
    """LLM-1: repair_json function."""

    def test_trailing_comma_in_array(self):
        result = repair_json('[{"title": "A"}, {"title": "B"},]')
        parsed = json.loads(result)
        assert len(parsed) == 2

    def test_trailing_comma_in_object(self):
        result = repair_json('{"title": "A", "uid": "B",}')
        parsed = json.loads(result)
        assert parsed['title'] == 'A'

    def test_python_true_false_none(self):
        result = repair_json('[{"active": True, "deleted": False, "data": None}]')
        parsed = json.loads(result)
        assert parsed[0]['active'] is True
        assert parsed[0]['deleted'] is False
        assert parsed[0]['data'] is None

    def test_single_quotes_to_double(self):
        result = repair_json("[{'title': 'Hello World'}]")
        parsed = json.loads(result)
        assert parsed[0]['title'] == 'Hello World'

    def test_valid_json_unchanged(self):
        original = '{"title": "Test"}'
        result = repair_json(original)
        assert json.loads(result) == json.loads(original)

    def test_empty_input(self):
        assert repair_json('') == ''
        assert repair_json(None) is None


class TestValidateSchema:
    """LLM-2: validate_schema function."""

    def test_valid_list_schema(self):
        data = [{'title': 'A', 'uid': 'a'}, {'title': 'B', 'uid': 'b'}]
        schema = {'type': 'list', 'items': {'required_keys': ['title', 'uid']}}
        assert validate_schema(data, schema) is True

    def test_missing_required_key(self):
        data = [{'title': 'A'}]  # Missing 'uid'
        schema = {'type': 'list', 'items': {'required_keys': ['title', 'uid']}}
        assert validate_schema(data, schema) is False

    def test_valid_dict_schema(self):
        data = {'title': 'Course', 'modules': []}
        schema = {'type': 'dict', 'required_keys': ['title']}
        assert validate_schema(data, schema) is True

    def test_wrong_type(self):
        data = "not a list"
        schema = {'type': 'list'}
        assert validate_schema(data, schema) is False

    def test_none_schema_passes(self):
        assert validate_schema([1, 2, 3], None) is True

    def test_empty_list_passes(self):
        data = []
        schema = {'type': 'list', 'items': {'required_keys': ['title']}}
        assert validate_schema(data, schema) is True


# --- Storage Tests ---

class TestStorageIndexes:
    """BUG-9: Critical indexes exist after init."""

    def test_indexes_created(self, tmp_path):
        sm = StorageManager(str(tmp_path))
        conn = sqlite3.connect(os.path.join(str(tmp_path), 'helga.db'))
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        expected_indexes = [
            'idx_progress_course', 'idx_progress_review', 'idx_progress_status',
            'idx_activity_course', 'idx_activity_created',
            'idx_schedule_course', 'idx_schedule_date', 'idx_schedule_status',
            'idx_flashcards_course', 'idx_flashcards_concept', 'idx_flashcards_review',
            'idx_courses_status'
        ]
        for idx in expected_indexes:
            assert idx in indexes, f"Missing index: {idx}"


class TestColumnWhitelist:
    """BUG-7: Column whitelist prevents SQL injection."""

    def test_progress_rejects_invalid_columns(self, tmp_path):
        sm = StorageManager(str(tmp_path))
        # This should silently drop 'malicious_col' and not raise
        sm.progress.update_progress('c1', 'course1', grade=4, malicious_col='DROP TABLE')
        
        p = sm.progress.get_progress('c1')
        assert p is not None
        assert p['grade'] == 4

    def test_flashcard_rejects_invalid_columns(self, tmp_path):
        sm = StorageManager(str(tmp_path))
        uid = sm.flashcards.add_card('c1', 'con1', 'Front', 'Back')
        # This should silently drop 'evil_col'
        sm.flashcards.update_card(uid, status='reviewing', evil_col='bad_data')
        
        cards = sm.flashcards.get_cards_for_concept('con1')
        assert cards[0]['status'] == 'reviewing'


class TestUpsertProgress:
    """PERF-1: INSERT OR REPLACE upsert pattern."""

    def test_upsert_new_then_update(self, tmp_path):
        sm = StorageManager(str(tmp_path))
        
        # First call creates
        sm.progress.update_progress('c1', 'course1', grade=3, status='in_progress')
        p = sm.progress.get_progress('c1')
        assert p['grade'] == 3
        
        # Second call updates
        sm.progress.update_progress('c1', 'course1', grade=5, status='completed')
        p = sm.progress.get_progress('c1')
        assert p['grade'] == 5
        assert p['status'] == 'completed'


class TestDuplicateListCoursesRemoved:
    """Verify the duplicate 'list_courses' line was removed from LOBBY handler."""

    def test_no_duplicate_list_courses(self):
        source_file = os.path.join(
            os.path.dirname(__file__), '..', '..', 'services', 'core', 'fsm_logic.py'
        )
        with open(source_file, 'r') as f:
            content = f.read()
        
        # Find the LOBBY block and count 'list_courses' references within the transition method
        # There should be no consecutive duplicate lines
        lines = content.split('\n')
        for i in range(len(lines) - 1):
            if 'self.list_courses()' in lines[i] and 'self.list_courses()' in lines[i + 1]:
                pytest.fail("Found duplicate self.list_courses() on consecutive lines")
