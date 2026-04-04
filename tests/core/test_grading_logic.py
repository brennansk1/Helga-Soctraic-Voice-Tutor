
import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import json

# Add services/core to path AND project root
sys.path.append(os.getcwd()) # Root for 'services' package
sys.path.append(os.path.join(os.getcwd(), 'services/core')) # For local imports in core

# Mock sklearn dependency if missing
try:
    import sklearn.metrics.pairwise
except ImportError:
    from unittest.mock import MagicMock
    import sys
    import types
    
    # Create mock module structure
    sklearn = MagicMock()
    sklearn.metrics = MagicMock()
    sklearn.metrics.pairwise = MagicMock()
    
    # Set helper to allow import
    sys.modules['sklearn'] = sklearn
    sys.modules['sklearn.metrics'] = sklearn.metrics
    sys.modules['sklearn.metrics.pairwise'] = sklearn.metrics.pairwise
    # Also patch feature_extraction just in case
    sklearn.feature_extraction = MagicMock()
    sklearn.feature_extraction.text = MagicMock()
    sys.modules['sklearn.feature_extraction'] = sklearn.feature_extraction
    sys.modules['sklearn.feature_extraction.text'] = sklearn.feature_extraction.text

# Mock logging to avoid file permission issues
import logging
logging.basicConfig = MagicMock()
logging.FileHandler = MagicMock()

# Mock dependencies before import
core_deps = {
    'yaml': MagicMock(),
    'threading': MagicMock(),
    'psutil': MagicMock(),
    'subprocess': MagicMock(),
    'subprocess': MagicMock(),
    'libzim': MagicMock(),
    'kuzu': MagicMock(),
    'docker': MagicMock(),
    'docker.errors': MagicMock(),
}
with patch.dict('sys.modules', core_deps):
    from fsm_logic import MnemosyneFSM as FSMLogic

class TestGradingLogic(unittest.TestCase):
    def setUp(self):
        # Mock dependencies
        self.mock_llm_url = "http://mock-llm"
        self.mock_rag_url = "http://mock-rag"
        self.mock_user_settings = {"voice_id": "Vivian"}
        
        # Instantiate FSM
        # MnemosyneFSM init doesn't take args in current code? 
        # Line 48: def __init__(self):
        # It reads config internally.
        
        # We need to mock the internal calls or just init it.
        # It sets self.llm_service_url from os.getenv or config.
        
        self.fsm = FSMLogic()
        
        # Manually inject mocks
        self.fsm.llm_service_url = self.mock_llm_url
        self.fsm.rag_service_url = self.mock_rag_url
        
        self.fsm.speak = MagicMock()
        self.fsm.play_sound = MagicMock()
        self.fsm.listen_and_transcribe = MagicMock(return_value="User Answer")
        
        # Mock LLM response
        self.fsm.query_llm = MagicMock()

    @patch('requests.post')
    def test_evaluate_answer_pass(self, mock_post):
        # Setup context
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"
        self.fsm.completed_topics = set()
        self.fsm.syllabus_queue = []
        
        # Mock LLM Response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"text": '{"grade": "5", "feedback": "Perfect"}'}]}
        mock_post.return_value = mock_response
        
        self.fsm.handle_socratic_answer("Correct Answer")
        
        # Verify Side Effects
        # Grade 5 -> SUCCESS_CHORD + "Exactly right."
        self.fsm.play_sound.assert_called_with("SUCCESS_CHORD")
        # completed_topics should contain 'node1'
        self.assertIn('node1', self.fsm.completed_topics)

    @patch('requests.post')
    def test_evaluate_answer_fail(self, mock_post):
        # Setup context
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"
        
        # Mock LLM Response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"text": '{"grade": "1", "feedback": "Wrong"}'}]}
        mock_post.return_value = mock_response
        
        self.fsm.handle_socratic_answer("Wrong Answer")
        
        # Verify Side Effects
        # Grade 1 -> FRICTION_GRIND
        self.fsm.play_sound.assert_called_with("FRICTION_GRIND")

    @patch('requests.post')
    def test_evaluate_answer_json_parsing_robustness(self, mock_post):
        # Setup context
        self.fsm.current_lesson_node = {'uid': 'node1', 'title': 'Test Node', 'text': 'Context'}
        self.fsm.last_question = "What is X?"
        
        # Mock LLM Response with markdown
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_text = 'Here is the JSON:\n```json\n{"grade": "4", "feedback": "Good"}\n```'
        mock_response.json.return_value = {"choices": [{"text": mock_text}]}
        mock_post.return_value = mock_response
        
        self.fsm.handle_socratic_answer("Good Answer")
        
        # Grade 4 -> SUCCESS_CHORD
        self.fsm.play_sound.assert_called_with("SUCCESS_CHORD")

if __name__ == '__main__':
    unittest.main()
