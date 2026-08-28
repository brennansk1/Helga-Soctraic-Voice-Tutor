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
    def test_grade_llm_outage_is_not_reported_as_a_fail(self, mock_llm_json, mock_storage):
        """An LLM outage must be a named error, never a FAIL verdict.

        Regression guard: this used to substitute grade="FAIL", score=0 and
        return 200, so a model hiccup showed the student a red X for an answer
        nobody actually graded.
        """
        mock_llm_json.return_value = None  # LLM / transport down

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q',
            'answer': 'A',
            'context': 'C',
        })
        self.assertEqual(resp.status_code, 503)
        data = json.loads(resp.data)
        self.assertEqual(data['error_code'], 'GRADING_UNAVAILABLE')
        self.assertFalse(data['graded'])
        self.assertTrue(data['retryable'])
        # Crucially: no verdict at all, so no client can render a pass/fail.
        self.assertNotIn('grade', data)
        self.assertNotIn('score', data)

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_llm_outage_does_not_touch_fsrs(self, mock_llm_json, mock_storage):
        """An LLM outage must leave the student's review schedule untouched.

        This is the data-damaging half of the old bug: the fabricated FAIL fell
        into the FAIL branch, which graded up to five EXISTING cards as
        "Again" (rating 1). A model outage permanently damaged the schedule.
        """
        mock_llm_json.return_value = None
        mock_storage.flashcards.get_cards_for_concept.return_value = [
            {'uid': 'card_1'}, {'uid': 'card_2'}, {'uid': 'card_3'},
        ]

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q',
            'answer': 'A correct answer, as it happens',
            'context': 'C',
            'concept_uid': 'con_1',
            'course_uid': 'c1',
        })

        self.assertEqual(resp.status_code, 503)
        mock_storage.flashcards.grade_card_fsrs.assert_not_called()
        mock_storage.flashcards.add_card.assert_not_called()

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_malformed_verdict_is_unavailable_not_fail(self, mock_llm_json, mock_storage):
        """JSON with no usable verdict is "we don't know", not "you were wrong"."""
        mock_llm_json.return_value = {'feedback': 'hmm', 'score': 0}
        mock_storage.flashcards.get_cards_for_concept.return_value = [{'uid': 'card_1'}]

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q', 'answer': 'A', 'context': 'C',
            'concept_uid': 'con_1', 'course_uid': 'c1',
        })

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(json.loads(resp.data)['error_code'], 'GRADING_UNAVAILABLE')
        mock_storage.flashcards.grade_card_fsrs.assert_not_called()

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_genuine_fail_is_distinguishable_from_outage(self, mock_llm_json, mock_storage):
        """A real FAIL still returns 200 with a verdict — and still downgrades."""
        mock_llm_json.return_value = {
            'grade': 'FAIL',
            'score': 20,
            'feedback': 'Incorrect.',
            'missing_concepts': [],
            'key_point': 'The thing to remember.',
        }
        mock_storage.flashcards.get_cards_for_concept.return_value = [{'uid': 'card_1'}]
        mock_storage.flashcards.add_card.return_value = 'card_new'

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q', 'answer': 'Wrong', 'context': 'C',
            'concept_uid': 'con_1', 'course_uid': 'c1',
        })

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['grade'], 'FAIL')
        self.assertTrue(data['graded'])
        self.assertNotIn('error_code', data)
        # A genuine FAIL is exactly when the schedule SHOULD move.
        mock_storage.flashcards.grade_card_fsrs.assert_called()

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grade_non_numeric_score_does_not_500(self, mock_llm_json, mock_storage):
        """A model returning a word for the score is cosmetic noise, not a failure."""
        mock_llm_json.return_value = {
            'grade': 'PASS', 'score': 'eighty-five', 'feedback': 'ok',
            'missing_concepts': None, 'key_point': '',
        }

        resp = self.client.post('/api/quiz/grade', json={
            'question': 'Q', 'answer': 'A', 'context': 'C',
        })
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['grade'], 'PASS')
        self.assertIsInstance(data['score'], int)

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


class TestDueConceptsEndpoint(unittest.TestCase):
    """Tests for GET /api/due_concepts — failure must not look like "caught up"."""

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
    def test_total_failure_is_named_error_not_empty_list(self, mock_storage):
        """Both sources down must 503, not render as "you're all caught up"."""
        mock_storage.flashcards.get_due_cards.side_effect = RuntimeError('db locked')
        mock_storage.schedule.get_scheduled_reviews.side_effect = RuntimeError('db locked')

        resp = self.client.get('/api/due_concepts')

        self.assertEqual(resp.status_code, 503)
        data = json.loads(resp.data)
        self.assertEqual(data['error_code'], 'DUE_REVIEWS_UNAVAILABLE')
        self.assertNotIn('concepts', data)
        self.assertCountEqual(
            data['failed_sources'], ['flashcards', 'scheduled_reviews'])

    @patch('services.rag.librarian.storage')
    def test_genuine_empty_is_still_a_200(self, mock_storage):
        """Actually having nothing due must stay distinguishable from a failure."""
        mock_storage.flashcards.get_due_cards.return_value = []
        mock_storage.schedule.get_scheduled_reviews.return_value = []

        resp = self.client.get('/api/due_concepts')

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['concepts'], [])
        self.assertNotIn('degraded', data)
        self.assertNotIn('error_code', data)

    @patch('services.rag.librarian.storage')
    def test_partial_failure_is_flagged_degraded(self, mock_storage):
        """One source down: serve what we have, but say the list is incomplete."""
        mock_storage.flashcards.get_due_cards.side_effect = RuntimeError('db locked')
        mock_storage.schedule.get_scheduled_reviews.return_value = [{
            'unit_uid': 'con_1',
            'course_uid': 'c1',
            'unit_title': 'Photosynthesis',
            'scheduled_date': '2026-01-01',
            'status': 'pending',
        }]

        resp = self.client.get('/api/due_concepts')

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(len(data['concepts']), 1)
        self.assertTrue(data['degraded'])
        self.assertEqual(data['degraded_sources'], ['flashcards'])


if __name__ == '__main__':
    unittest.main()


class TestQuizGradeAsksForAnObject(unittest.TestCase):
    """The defect the mocks above could not see.

    Every grade test sets `mock_llm_json.return_value` to a dict, so they all
    passed while the real call was steering the model to produce a LIST:
    llm_generate_json defaults to expected_type="list", the endpoint never
    overrode it, and the next line did `(result or {}).get("grade")`. A
    non-empty list is truthy, so live grading raised

        'list' object has no attribute 'get'

    on every single answer. It stayed hidden because the handler correctly
    refuses to call an infrastructure failure a wrong answer, so the learner
    saw a polite "not graded" note rather than an error.
    """

    @classmethod
    def setUpClass(cls):
        _setup_librarian_app()

    def setUp(self):
        from services.rag.librarian import app
        app.config['TESTING'] = True
        self.client = app.test_client()

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_grader_requests_a_dict_not_a_list(self, mock_llm_json, mock_storage):
        mock_llm_json.return_value = {'grade': 'PASS', 'score': 80,
                                      'feedback': 'ok'}
        self.client.post('/api/quiz/grade',
                         json={'question': 'Q', 'answer': 'A', 'context': 'C'})
        self.assertTrue(mock_llm_json.called)
        kwargs = mock_llm_json.call_args.kwargs
        self.assertEqual(kwargs.get('expected_type'), 'dict',
                         "the grader must ask for an object; asking for the "
                         "default list is what broke live grading")
        self.assertIsNotNone(kwargs.get('schema'),
                             "grading output should be schema-constrained")

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_a_list_wrapped_verdict_is_still_graded(self, mock_llm_json,
                                                    mock_storage):
        """Models wrap objects in a one-element array. The skeleton builder
        already tolerates that; the grader must too."""
        mock_llm_json.return_value = [{'grade': 'PASS', 'score': 75,
                                       'feedback': 'fine'}]
        resp = self.client.post('/api/quiz/grade',
                                json={'question': 'Q', 'answer': 'A',
                                      'context': 'C'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data)['grade'], 'PASS')

    @patch('services.rag.librarian.storage')
    @patch('services.common.llm_utils.llm_generate_json')
    def test_an_unusable_shape_is_never_a_failing_grade(self, mock_llm_json,
                                                        mock_storage):
        """A shape we cannot read means "we do not know", which must never be
        served as "the student was wrong" -- that path downgrades real FSRS
        cards."""
        mock_llm_json.return_value = ['not', 'a', 'verdict']
        resp = self.client.post('/api/quiz/grade',
                                json={'question': 'Q', 'answer': 'A',
                                      'context': 'C'})
        self.assertNotEqual(resp.status_code, 500)
        body = json.loads(resp.data)
        self.assertNotEqual(body.get('grade'), 'FAIL')
        self.assertFalse(body.get('graded', False))
