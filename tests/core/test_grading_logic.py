
import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import json

# Add services/core to path AND project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../services/core')))

# Mock Flask before importing fsm_logic
class _MockFlaskApp:
    def __init__(self, *a, **kw): pass
    def route(self, *a, **kw):
        return lambda f: f
    def run(self, *a, **kw): pass

flask_mock = MagicMock()
flask_mock.Flask = _MockFlaskApp
flask_mock.request = MagicMock()

cb_mock = MagicMock()
cb_mock.__file__ = 'mocked_course_builder.py'

core_deps = {
    'kuzu': MagicMock(),
    'libzim': MagicMock(),
    'sentence_transformers': MagicMock(),
    'psutil': MagicMock(),
    'yaml': MagicMock(),
    'fsrs_engine': MagicMock(),
    'safety': MagicMock(),
    'service_manager': MagicMock(),
    'db_manager': MagicMock(),
    'content_provider': MagicMock(),
    'course_builder': cb_mock,
}

with patch.dict('sys.modules', core_deps):
    from services.core.fsm_logic import MnemosyneFSM as FSMLogic


class TestGradingLogic(unittest.TestCase):
    def setUp(self):
        with patch.object(FSMLogic, '__init__', lambda self, *a, **kw: None):
            self.fsm = FSMLogic.__new__(FSMLogic)

        # Set all attributes needed by handle_socratic_answer
        self.fsm.state = 'SOCRATIC_LEARNING'
        self.fsm.question_start_time = 0
        self.fsm.conversation_history = []
        self.fsm.transcript = []
        self.fsm.current_context = 'Test context'
        self.fsm.current_teaching_style = ''
        self.fsm.current_misconceptions = []
        self.fsm.current_analogies = []
        self.fsm.user_profile = None
        self.fsm.completed_topics = set()
        self.fsm.syllabus_queue = []
        self.fsm._last_socratic_grade = 3

        # Socratic type progression
        self.fsm.socratic_type_index = 0
        self.fsm.socratic_retry_count = 0

        # Multi-question mastery tracking
        self.fsm.concept_correct_streak = 0
        self.fsm.concept_question_count = 0

        # Bloom's Taxonomy tracking
        self.fsm.current_bloom_level = 1
        self.fsm.bloom_correct_streak = 0
        # Bloom progression attributes
        self.fsm.course_bloom_floor = 1
        self.fsm.course_bloom_ceiling = 6
        self.fsm.concept_bloom_target = 1
        self.fsm.passed_question_types = {"SCENARIO", "MECHANISM", "CONTRAST"}
        self.fsm.prior_concepts_summary = []

        # Mock methods
        self.fsm.speak = MagicMock()
        self.fsm.play_sound = MagicMock()
        self.fsm.send_status_update = MagicMock()
        self.fsm.append_session_note = MagicMock()
        self.fsm.ask_socratic_question = MagicMock()
        self.fsm.next_syllabus_item = MagicMock()

    def test_evaluate_answer_pass(self):
        # Setup context
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"

        # To trigger "concept mastered" path, we need:
        # grade >= 4, socratic_type_index + 1 >= 6, streak >= 2, count >= 3
        self.fsm.socratic_type_index = 5  # After +1 -> 6 >= 6 (len)
        self.fsm.concept_correct_streak = 2  # After increment -> 3 >= 2
        self.fsm.concept_question_count = 3  # After increment -> 4 >= 3

        # Mock _call_llm to return a grade 5 JSON
        self.fsm._call_llm = MagicMock(return_value='{"grade": 5, "feedback": "Perfect"}')

        self.fsm.handle_socratic_answer("Correct Answer")

        # Grade 5 -> SUCCESS_CHORD
        self.fsm.play_sound.assert_called_with("SUCCESS_CHORD")
        # completed_topics should contain 'node1'
        self.assertIn('node1', self.fsm.completed_topics)

    def test_evaluate_answer_fail(self):
        # Setup context
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"

        # Mock _call_llm to return a grade 1 JSON
        self.fsm._call_llm = MagicMock(return_value='{"grade": 1, "feedback": "Wrong"}')

        self.fsm.handle_socratic_answer("Wrong Answer")

        # Grade 1 -> FRICTION_GRIND (retry_count < 3)
        self.fsm.play_sound.assert_called_with("FRICTION_GRIND")

    def test_evaluate_answer_json_parsing_robustness(self):
        # Setup context
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"

        # Mock _call_llm to return grade wrapped in markdown
        mock_text = 'Here is the JSON:\n```json\n{"grade": 4, "feedback": "Good"}\n```'
        self.fsm._call_llm = MagicMock(return_value=mock_text)

        self.fsm.handle_socratic_answer("Good Answer")

        # Grade 4 -> SUCCESS_CHORD
        self.fsm.play_sound.assert_called_with("SUCCESS_CHORD")

    def test_grading_none_does_not_pass_student(self):
        """B3.3: if the grader returns None, the answer must NOT be credited as
        correct (grade >= 3). It should fall back to partial (grade 2)."""
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"
        self.fsm.socratic_type_index = 5  # would complete the concept IF graded >= 3
        self.fsm.concept_correct_streak = 2
        self.fsm.concept_question_count = 3
        self.fsm._call_llm = MagicMock(return_value=None)

        self.fsm.handle_socratic_answer("Some answer")

        # Partial → streak reset, concept NOT marked complete, no success chord.
        self.assertEqual(self.fsm.concept_correct_streak, 0)
        self.assertNotIn('node1', self.fsm.completed_topics)
        self.fsm.play_sound.assert_called_with("FRICTION_GRIND")

    def test_grading_unparseable_does_not_pass_student(self):
        """B3.3: unparseable grader output must not silently pass the student."""
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"
        self.fsm.socratic_type_index = 5
        self.fsm.concept_correct_streak = 2
        self.fsm.concept_question_count = 3
        self.fsm._call_llm = MagicMock(return_value="the model rambled with no grade here")

        self.fsm.handle_socratic_answer("Some answer")

        self.assertEqual(self.fsm.concept_correct_streak, 0)
        self.assertNotIn('node1', self.fsm.completed_topics)

    def test_grading_requests_constrained_json_schema(self):
        """Task #2: grading must ask Ollama for schema-constrained JSON output."""
        from services.common.prompts import GRADE_JSON_SCHEMA
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"
        self.fsm._call_llm = MagicMock(return_value='{"grade": 3, "feedback": "ok"}')
        self.fsm.handle_socratic_answer("an answer")
        _, kwargs = self.fsm._call_llm.call_args
        self.assertEqual(kwargs.get('json_schema'), GRADE_JSON_SCHEMA)

    def test_ignorance_detection_bypasses_grading(self):
        """If user says 'I don't know', should bypass LLM grading and get grade 1."""
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"
        self.fsm._call_llm = MagicMock()

        self.fsm.handle_socratic_answer("I don't know")

        # Should NOT have called LLM for grading
        self.fsm._call_llm.assert_not_called()

if __name__ == '__main__':
    unittest.main()
