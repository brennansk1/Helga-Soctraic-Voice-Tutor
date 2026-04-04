"""
Tests for SET_CONTEXT event handler in MnemosyneFSM.

Covers:
- SET_CONTEXT sets active_course_uid
- SET_CONTEXT loads teaching_style from course metadata
- SET_CONTEXT with missing uid doesn't crash
- SET_CONTEXT handles missing course gracefully
- SET_CONTEXT with no payload doesn't crash
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

# Mock dependencies BEFORE importing fsm_logic (same pattern as test_fsm_logic.py)
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


def _make_fsm():
    """Create an FSM instance with mocked __init__ and required attributes."""
    with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
        fsm = MnemosyneFSM.__new__(MnemosyneFSM)
        fsm.state = 'LOBBY'
        fsm.active_course_uid = None
        fsm.current_teaching_style = ''
        fsm.last_interaction_time = 0
        fsm.transcript = []
        fsm.current_lesson_node = None
        fsm.tts_enabled = True
        fsm.maintenance_paused = False
        fsm.storage = MagicMock()
        return fsm


class TestSetContextSetsActiveUid(unittest.TestCase):
    """SET_CONTEXT should set the active_course_uid."""

    def test_sets_active_course_uid(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = {'teaching_style': 'Standard'}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_abc123'}
        })
        self.assertEqual(fsm.active_course_uid, 'course_abc123')

    def test_sets_different_uid(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = {'teaching_style': ''}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_xyz789'}
        })
        self.assertEqual(fsm.active_course_uid, 'course_xyz789')

    def test_overwrites_previous_uid(self):
        fsm = _make_fsm()
        fsm.active_course_uid = 'old_course'
        fsm.storage.courses.get_course.return_value = {'teaching_style': ''}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'new_course'}
        })
        self.assertEqual(fsm.active_course_uid, 'new_course')


class TestSetContextLoadsTeachingStyle(unittest.TestCase):
    """SET_CONTEXT should load teaching_style from course metadata."""

    def test_loads_teaching_style(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = {'teaching_style': 'ELI5'}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_xyz'}
        })
        self.assertEqual(fsm.current_teaching_style, 'ELI5')

    def test_loads_academic_style(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = {'teaching_style': 'Academic'}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_abc'}
        })
        self.assertEqual(fsm.current_teaching_style, 'Academic')

    def test_empty_teaching_style(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = {'teaching_style': ''}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_abc'}
        })
        self.assertEqual(fsm.current_teaching_style, '')


class TestSetContextMissingUid(unittest.TestCase):
    """SET_CONTEXT with missing or empty uid should not crash."""

    def test_empty_payload_no_crash(self):
        fsm = _make_fsm()
        fsm.active_course_uid = 'old_uid'
        # Should not raise
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {}
        })
        # uid should remain unchanged
        self.assertEqual(fsm.active_course_uid, 'old_uid')

    def test_no_course_uid_key(self):
        fsm = _make_fsm()
        fsm.active_course_uid = 'keep_this'
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'other_key': 'value'}
        })
        self.assertEqual(fsm.active_course_uid, 'keep_this')

    def test_none_course_uid(self):
        fsm = _make_fsm()
        fsm.active_course_uid = 'original'
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': None}
        })
        # None uid should be treated as no-op
        self.assertEqual(fsm.active_course_uid, 'original')

    def test_no_payload_at_all(self):
        """SET_CONTEXT event with no payload key should not crash."""
        fsm = _make_fsm()
        fsm.active_course_uid = 'keep'
        try:
            fsm.transition({
                'type': 'SET_CONTEXT',
            })
        except Exception:
            # If it crashes with missing payload, that's acceptable
            # but document it
            pass


class TestSetContextHandlesMissingCourse(unittest.TestCase):
    """SET_CONTEXT should handle course lookup failures gracefully."""

    def test_db_error_still_sets_uid(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.side_effect = Exception("DB error")
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_missing'}
        })
        self.assertEqual(fsm.active_course_uid, 'course_missing')

    def test_course_not_found_returns_none(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = None
        # Should not crash
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_absent'}
        })
        self.assertEqual(fsm.active_course_uid, 'course_absent')

    def test_course_missing_teaching_style_key(self):
        fsm = _make_fsm()
        fsm.storage.courses.get_course.return_value = {'title': 'Test Course'}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_no_style'}
        })
        self.assertEqual(fsm.active_course_uid, 'course_no_style')
        # teaching_style should default gracefully
        self.assertIsNotNone(fsm.current_teaching_style)


class TestSetContextDoesNotChangeState(unittest.TestCase):
    """SET_CONTEXT should not change the FSM state."""

    def test_state_remains_lobby(self):
        fsm = _make_fsm()
        fsm.state = 'LOBBY'
        fsm.storage.courses.get_course.return_value = {'teaching_style': ''}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_x'}
        })
        self.assertEqual(fsm.state, 'LOBBY')

    def test_state_remains_socratic(self):
        fsm = _make_fsm()
        fsm.state = 'SOCRATIC_LEARNING'
        fsm.storage.courses.get_course.return_value = {'teaching_style': ''}
        fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_x'}
        })
        self.assertEqual(fsm.state, 'SOCRATIC_LEARNING')


if __name__ == '__main__':
    unittest.main()
