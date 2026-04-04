
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import json
import time

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Mock dependencies BEFORE importing fsm_logic
# Flask mock - needs .route() to return a decorator
class _MockFlaskApp:
    def __init__(self, *a, **kw): pass
    def route(self, *a, **kw):
        return lambda f: f
    def run(self, *a, **kw): pass

flask_mock = MagicMock()
flask_mock.Flask = _MockFlaskApp
flask_mock.request = MagicMock()
# fsrs_engine mock
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


class TestDetectIgnorance(unittest.TestCase):
    """Tests for the _detect_ignorance method."""
    
    def setUp(self):
        self.mock_db = MagicMock()
        # Mock the constructor dependencies
        with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
            self.fsm = MnemosyneFSM.__new__(MnemosyneFSM)
    
    def test_explicit_idk(self):
        self.assertTrue(self.fsm._detect_ignorance("I don't know"))
    
    def test_idk_shorthand(self):
        self.assertTrue(self.fsm._detect_ignorance("idk"))
    
    def test_dunno(self):
        self.assertTrue(self.fsm._detect_ignorance("dunno"))
    
    def test_not_sure(self):
        self.assertTrue(self.fsm._detect_ignorance("not sure"))
    
    def test_give_up(self):
        self.assertTrue(self.fsm._detect_ignorance("I give up"))
    
    def test_skip(self):
        self.assertTrue(self.fsm._detect_ignorance("skip"))
    
    def test_pass(self):
        self.assertTrue(self.fsm._detect_ignorance("pass"))
    
    def test_short_question(self):
        """Short questions with ? should be detected as ignorance."""
        self.assertTrue(self.fsm._detect_ignorance("Huh?"))
    
    def test_valid_answer_not_ignorance(self):
        self.assertFalse(self.fsm._detect_ignorance("Photosynthesis converts light to energy"))
    
    def test_confident_answer_not_ignorance(self):
        self.assertFalse(self.fsm._detect_ignorance("The answer is mitochondria"))
    
    def test_embedded_ignorance(self):
        """Phrases containing ignorance keywords should still match."""
        self.assertTrue(self.fsm._detect_ignorance("I have no idea about this topic"))


class TestDetectHesitation(unittest.TestCase):
    """Tests for the _detect_hesitation method."""
    
    def setUp(self):
        with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
            self.fsm = MnemosyneFSM.__new__(MnemosyneFSM)
    
    def test_hesitation_markers(self):
        self.assertTrue(self.fsm._detect_hesitation("um well maybe it's about cells", 3.0))
    
    def test_long_pause(self):
        """Long latency (>8s) should trigger hesitation."""
        self.assertTrue(self.fsm._detect_hesitation("the answer is DNA", 9.0))
    
    def test_confident_fast(self):
        self.assertFalse(self.fsm._detect_hesitation("Photosynthesis uses chlorophyll", 2.0))
    
    def test_single_marker_below_threshold(self):
        """A single marker with short pause should not trigger."""
        self.assertFalse(self.fsm._detect_hesitation("maybe it's about energy", 2.0))


class TestNavigateToTopicLectureMode(unittest.TestCase):
    """Verify that navigate_to_topic forces LECTURE mode for opening microlecture."""
    
    def setUp(self):
        with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
            self.fsm = MnemosyneFSM.__new__(MnemosyneFSM)
            # Set up all attributes touched by navigate_to_topic
            self.fsm.state = 'LOBBY'
            self.fsm.current_concept_uid = None
            self.fsm.current_concept_title = None
            self.fsm.current_resource_text = None
            self.fsm.current_teaching_style = 'standard'
            self.fsm.conversation_history = []
            self.fsm.question_count = 0
            self.fsm.correct_count = 0
            self.fsm.current_misconceptions = None
            self.fsm.current_analogies = None
            self.fsm.db_path = 'dummy'
            self.fsm.conn = MagicMock()
            self.fsm.speak = MagicMock()
            self.fsm.send_status_update = MagicMock()
            self.fsm.send_event = MagicMock()
            self.fsm.stop_audio = MagicMock()
            self.fsm.play_sound = MagicMock()
            self.fsm.load_topic_context = MagicMock(return_value="Loaded context text")
            self.fsm.syllabus_queue = []
            self.fsm.last_lesson_title = None
            self.fsm.transcript = []
            self.fsm.current_lesson_node = None
            self.fsm.current_context = None
            self.fsm.audio_url = 'http://mock:5000'
    
    @patch.object(MnemosyneFSM, 'ask_socratic_question')
    def test_navigate_forces_lecture_mode(self, mock_ask):
        """When navigating to a topic, initial_mode must be LECTURE."""
        # Mock get_concept_details to return a valid concept
        self.fsm.get_concept_details = MagicMock(return_value={
            'uid': 'con_123',
            'title': 'Photosynthesis',
            'resource_text': 'The process of converting light to energy'
        })
        
        self.fsm.navigate_to_topic('con_123')
        
        # Verify ask_socratic_question was called with initial_mode="LECTURE"
        mock_ask.assert_called_once()
        call_args = mock_ask.call_args
        # Check either positional or keyword argument
        if call_args.kwargs and 'initial_mode' in call_args.kwargs:
            self.assertEqual(call_args.kwargs['initial_mode'], 'LECTURE',
                           "navigate_to_topic must force LECTURE mode for opening microlecture")
        elif len(call_args.args) > 1:
            self.assertEqual(call_args.args[1], 'LECTURE',
                           "navigate_to_topic must force LECTURE mode for opening microlecture")
        else:
            self.fail("ask_socratic_question was not called with initial_mode argument")


class TestNoSecondDetectIgnorance(unittest.TestCase):
    """Verify the duplicate _detect_ignorance was removed."""
    
    def test_only_one_detect_ignorance(self):
        """There should be exactly one _detect_ignorance method in fsm_logic.py."""
        import inspect
        source_file = inspect.getfile(MnemosyneFSM)
        with open(source_file, 'r') as f:
            content = f.read()
        
        count = content.count('def _detect_ignorance(')
        self.assertEqual(count, 1, 
                        f"Expected exactly 1 _detect_ignorance definition, found {count}. "
                        "Duplicate may not have been removed.")


class TestSetContext(unittest.TestCase):
    """Tests for the SET_CONTEXT event handler."""

    def setUp(self):
        with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
            self.fsm = MnemosyneFSM.__new__(MnemosyneFSM)
            self.fsm.state = 'LOBBY'
            self.fsm.active_course_uid = None
            self.fsm.current_teaching_style = ''
            self.fsm.last_interaction_time = 0
            self.fsm.transcript = []
            self.fsm.current_lesson_node = None
            self.fsm.tts_enabled = True
            self.fsm.maintenance_paused = False
            self.fsm.storage = MagicMock()

    def test_set_context_sets_active_uid(self):
        """SET_CONTEXT should set the active_course_uid."""
        self.fsm.storage.courses.get_course.return_value = {'teaching_style': 'Academic'}
        self.fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_abc123'}
        })
        self.assertEqual(self.fsm.active_course_uid, 'course_abc123')

    def test_set_context_loads_teaching_style(self):
        """SET_CONTEXT should load teaching_style from course metadata."""
        self.fsm.storage.courses.get_course.return_value = {'teaching_style': 'ELI5'}
        self.fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_xyz'}
        })
        self.assertEqual(self.fsm.current_teaching_style, 'ELI5')

    def test_set_context_no_uid_is_noop(self):
        """SET_CONTEXT with no course_uid should not change state."""
        self.fsm.active_course_uid = 'old_uid'
        self.fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {}
        })
        self.assertEqual(self.fsm.active_course_uid, 'old_uid')

    def test_set_context_handles_missing_course(self):
        """SET_CONTEXT should still set UID even if course lookup fails."""
        self.fsm.storage.courses.get_course.side_effect = Exception("DB error")
        self.fsm.transition({
            'type': 'SET_CONTEXT',
            'payload': {'course_uid': 'course_missing'}
        })
        self.assertEqual(self.fsm.active_course_uid, 'course_missing')


if __name__ == '__main__':
    unittest.main()
