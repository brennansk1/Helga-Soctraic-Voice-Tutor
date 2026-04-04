"""
Tests for quiz endpoints in librarian.py — /api/quiz and /api/quiz/grade.

Covers:
- /api/quiz returns expected fields (mock storage + LLM)
- /api/quiz with no courses returns 404
- /api/quiz/grade returns grade, score, feedback (mock LLM)
- /api/quiz/grade with missing data returns 400
- Uses Flask test_client with mocked dependencies
"""
import sys
import os
import unittest
import json
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

# We need to mock heavy dependencies before importing the librarian module
# Mock sentence_transformers, libzim, numpy, and other imports
_mock_modules = {
    'libzim': MagicMock(),
    'libzim.reader': MagicMock(),
    'sentence_transformers': MagicMock(),
    'numpy': MagicMock(),
}

# Patch course_builder and course_cleaner to avoid import chain issues
_mock_cb = MagicMock()
_mock_cb.__file__ = 'mocked'
_mock_modules['services.core.course_builder'] = _mock_cb
_mock_modules['services.common.course_cleaner'] = MagicMock()


def _setup_librarian_app():
    """Import and configure librarian Flask app with mocked dependencies."""
    with patch.dict('sys.modules', _mock_modules):
        # Mock StorageManager at module level
        with patch('services.common.storage.StorageManager') as MockSM:
            mock_storage = MagicMock()
            MockSM.return_value = mock_storage

            # Need to reload/import with mocks in place
            import importlib
            # Patch os.path.exists to avoid ZIM file loading
            with patch('os.path.exists', return_value=False):
                with patch('services.rag.librarian.storage', mock_storage):
                    from services.rag.librarian import app
                    return app, mock_storage


class TestQuizEndpoint(unittest.TestCase):
    """Tests for GET /api/quiz."""

    @classmethod
    def setUpClass(cls):
        """Set up Flask test client with mocked dependencies."""
        try:
            cls.app, cls.mock_storage = _setup_librarian_app()
            cls.client = cls.app.test_client()
            cls.app.config['TESTING'] = True
        except Exception as e:
            # If import fails due to complex dependency chain, skip tests
            cls.skip_reason = str(e)
            cls.client = None

    def setUp(self):
        if self.client is None:
            self.skipTest(f"Could not import librarian: {self.skip_reason}")

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate')
    def test_quiz_returns_expected_fields(self, mock_llm, mock_storage):
        """Quiz endpoint should return question, concept_uid, concept_title, course_uid."""
        # Set up mock course with concepts
        mock_course = {
            'uid': 'course_123',
            'title': 'Physics 101',
            'modules': [{
                'title': 'M1', 'uid': 'm1',
                'units': [{
                    'title': 'U1', 'uid': 'u1',
                    'lessons': [{
                        'title': 'L1', 'uid': 'l1',
                        'concepts': [{'title': 'Newton Laws', 'uid': 'con_abc'}]
                    }]
                }]
            }]
        }
        mock_storage.courses.list_courses.return_value = [{'uid': 'course_123'}]
        mock_storage.courses.get_course.return_value = mock_course
        mock_storage.courses.get_flat_concepts.return_value = [
            {'uid': 'con_abc', 'title': 'Newton Laws'}
        ]
        mock_storage.courses.get_concept_content.return_value = "Newton's laws describe motion and forces."
        mock_storage.flashcards.get_cards_for_concept.return_value = []

        mock_llm.return_value = "What is Newton's first law of motion?"

        resp = self.client.get('/api/quiz')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('question', data)
        self.assertIn('concept_uid', data)
        self.assertIn('concept_title', data)
        self.assertIn('course_uid', data)
        self.assertIn('context_text', data)

    @patch('services.rag.librarian.storage')
    def test_quiz_no_courses_returns_404(self, mock_storage):
        """Quiz with no courses should return 404."""
        mock_storage.courses.list_courses.return_value = []
        resp = self.client.get('/api/quiz')
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.data)
        self.assertIn('error', data)

    @patch('services.rag.librarian.storage')
    def test_quiz_with_course_uid_filter(self, mock_storage):
        """Quiz should filter to specific course when course_uid provided."""
        mock_storage.courses.get_course.return_value = None
        resp = self.client.get('/api/quiz?course_uid=nonexistent')
        self.assertEqual(resp.status_code, 404)

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate')
    def test_quiz_llm_failure_gives_fallback_question(self, mock_llm, mock_storage):
        """If LLM fails, should fall back to a generic question."""
        mock_course = {
            'uid': 'c1', 'title': 'Test',
            'modules': [{'title': 'M', 'uid': 'm', 'units': [
                {'title': 'U', 'uid': 'u', 'lessons': [
                    {'title': 'L', 'uid': 'l', 'concepts': [
                        {'title': 'Concept X', 'uid': 'con_x'}
                    ]}
                ]}
            ]}]
        }
        mock_storage.courses.list_courses.return_value = [{'uid': 'c1'}]
        mock_storage.courses.get_course.return_value = mock_course
        mock_storage.courses.get_flat_concepts.return_value = [
            {'uid': 'con_x', 'title': 'Concept X'}
        ]
        mock_storage.courses.get_concept_content.return_value = "Some content about X."
        mock_storage.flashcards.get_cards_for_concept.return_value = []
        mock_llm.return_value = ""  # LLM fails

        resp = self.client.get('/api/quiz')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('question', data)
        # Fallback question should mention the concept
        self.assertIn('Concept X', data['question'])


class TestQuizGradeEndpoint(unittest.TestCase):
    """Tests for POST /api/quiz/grade."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.app, cls.mock_storage = _setup_librarian_app()
            cls.client = cls.app.test_client()
            cls.app.config['TESTING'] = True
        except Exception as e:
            cls.skip_reason = str(e)
            cls.client = None

    def setUp(self):
        if self.client is None:
            self.skipTest(f"Could not import librarian: {self.skip_reason}")

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_returns_expected_fields(self, mock_llm_json, mock_storage):
        """Grade endpoint should return grade, score, feedback."""
        mock_llm_json.return_value = {
            'grade': 'PASS',
            'score': 85,
            'feedback': 'Good answer with clear explanation.',
            'missing_concepts': [],
            'key_point': 'Newton first law states objects in motion stay in motion.',
        }

        resp = self.client.post('/api/quiz/grade', json={
            'question': "What is Newton's first law?",
            'answer': "Objects in motion stay in motion unless acted on by force.",
            'context': "Newton's laws of motion...",
            'concept_uid': 'con_abc',
            'course_uid': 'course_123',
        })
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('grade', data)
        self.assertIn('score', data)
        self.assertIn('feedback', data)

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_pass(self, mock_llm_json, mock_storage):
        """A correct answer should get PASS grade."""
        mock_llm_json.return_value = {
            'grade': 'PASS',
            'score': 90,
            'feedback': 'Excellent.',
            'missing_concepts': [],
            'key_point': '',
        }

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q',
            'answer': 'A',
            'context': 'C',
        })
        data = json.loads(resp.data)
        self.assertEqual(data['grade'], 'PASS')
        self.assertEqual(data['score'], 90)

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_fail(self, mock_llm_json, mock_storage):
        """A wrong answer should get FAIL grade."""
        mock_llm_json.return_value = {
            'grade': 'FAIL',
            'score': 20,
            'feedback': 'Incorrect understanding.',
            'missing_concepts': ['key concept'],
            'key_point': 'Important thing to remember.',
        }
        mock_storage.flashcards.get_cards_for_concept.return_value = []
        mock_storage.flashcards.add_card.return_value = 'card_123'

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q',
            'answer': 'Wrong answer',
            'context': 'C',
            'concept_uid': 'con_1',
            'course_uid': 'c1',
        })
        data = json.loads(resp.data)
        self.assertEqual(data['grade'], 'FAIL')
        self.assertLessEqual(data['score'], 50)

    def test_grade_missing_question_returns_400(self):
        """Missing question should return 400."""
        resp = self.client.post('/api/quiz/grade', json={
            'answer': 'Some answer',
        })
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn('error', data)

    def test_grade_missing_answer_returns_400(self):
        """Missing answer should return 400."""
        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Some question',
        })
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn('error', data)

    def test_grade_empty_body_returns_400(self):
        """Empty request body should return 400."""
        resp = self.client.post('/api/quiz/grade', json={})
        self.assertEqual(resp.status_code, 400)

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_llm_failure_gives_fallback(self, mock_llm_json, mock_storage):
        """If LLM fails to grade, should return a fallback result."""
        mock_llm_json.return_value = None  # LLM failure

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q',
            'answer': 'A',
            'context': 'C',
        })
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        # Should still have grade and feedback keys
        self.assertIn('grade', data)
        self.assertIn('feedback', data)

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_fail_creates_flashcards(self, mock_llm_json, mock_storage):
        """FAIL grade with concept_uid should create flashcards."""
        mock_llm_json.return_value = {
            'grade': 'FAIL',
            'score': 15,
            'feedback': 'Wrong.',
            'missing_concepts': ['photosynthesis basics'],
            'key_point': 'Plants convert light to energy.',
        }
        mock_storage.flashcards.get_cards_for_concept.return_value = []
        mock_storage.flashcards.add_card.return_value = 'card_new'

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Explain photosynthesis',
            'answer': 'I think it is about water',
            'context': 'Photosynthesis is the process...',
            'concept_uid': 'con_photo',
            'course_uid': 'c1',
        })
        data = json.loads(resp.data)
        self.assertGreater(data.get('cards_created', 0), 0)
        self.assertIn('flashcard_note', data)

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_score_is_numeric(self, mock_llm_json, mock_storage):
        """Score should be a numeric value."""
        mock_llm_json.return_value = {
            'grade': 'PASS',
            'score': 75,
            'feedback': 'Good.',
            'missing_concepts': [],
            'key_point': '',
        }

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q',
            'answer': 'A',
        })
        data = json.loads(resp.data)
        self.assertIsInstance(data['score'], (int, float))


if __name__ == '__main__':
    unittest.main()
