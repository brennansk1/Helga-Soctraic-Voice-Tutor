
import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import json
import shutil

core_deps = {
    'yaml': MagicMock(),
    'kuzu': MagicMock(),
    'libzim': MagicMock(),
}

# Add services/core to path AND project root
sys.path.append(os.getcwd()) # Root for 'services' package
sys.path.append(os.path.join(os.getcwd(), 'services/core')) # For local imports in core

# Mock sklearn dependency if missing
try:
    import sklearn.metrics.pairwise
except ImportError:
    sklearn = MagicMock()
    sklearn.metrics = MagicMock()
    sklearn.metrics.pairwise = MagicMock()
    sklearn.feature_extraction = MagicMock()
    sklearn.feature_extraction.text = MagicMock()
    
    core_deps.update({
        'sklearn': sklearn,
        'sklearn.metrics': sklearn.metrics,
        'sklearn.metrics.pairwise': sklearn.metrics.pairwise,
        'sklearn.feature_extraction': sklearn.feature_extraction,
        'sklearn.feature_extraction.text': sklearn.feature_extraction.text
    })

# Mock logging to avoid file permission issues
import logging
logging.basicConfig = MagicMock()
logging.FileHandler = MagicMock()

with patch.dict('sys.modules', core_deps):
    from fsm_logic import MnemosyneFSM as FSMLogic

class TestMultiCourse(unittest.TestCase):
    def setUp(self):
        self.test_state_file = "test_user_state.json"
        
        # Instantiate FSM with test state file
        self.fsm = FSMLogic()
        self.fsm.state_file = self.test_state_file
        self.fsm.speak = MagicMock()
        
    def tearDown(self):
        if os.path.exists(self.test_state_file):
            os.remove(self.test_state_file)

    def test_save_current_course_progress(self):
        self.fsm.active_course_uid = "course_123"
        self.fsm.current_lesson_node = {"uid": "node_A", "text": "Content A"}
        self.fsm.completed_topics = {"topic_1", "topic_2"}
        
        # LRN-8: _save_current_course_progress now uses _atomic_write
        # Mock it to capture the data and write to our test file
        original_atomic = getattr(self.fsm, '_atomic_write', None)
        def mock_atomic_write(path, data):
            with open(path, 'w') as f:
                f.write(data)
            return True
        self.fsm._atomic_write = mock_atomic_write
        
        self.fsm._save_current_course_progress()
        
        # Verify file exists
        self.assertTrue(os.path.exists(self.test_state_file))
        
        with open(self.test_state_file, 'r') as f:
            data = json.load(f)
            
        self.assertIn('courses', data)
        self.assertIn('course_123', data['courses'])
        self.assertEqual(data['courses']['course_123']['current_node']['uid'], "node_A")
        self.assertEqual(data['last_active_uid'], "course_123")

    def test_load_course_progress(self):
        # Create dummy state
        full_state = {
            "courses": {
                "course_123": {
                    "current_node": {"uid": "node_A"},
                    "completed_topics": ["topic_1"]
                },
                "course_456": {
                    "current_node": {"uid": "node_B"},
                    "completed_topics": []
                }
            }
        }
        with open(self.test_state_file, 'w') as f:
            json.dump(full_state, f)
            
        # Try loading course 123
        loaded = self.fsm._load_course_progress("course_123")
        self.assertTrue(loaded)
        self.assertEqual(self.fsm.current_lesson_node['uid'], "node_A")
        self.assertIn("topic_1", self.fsm.completed_topics)
        
        # Try loading course 456
        loaded = self.fsm._load_course_progress("course_456")
        self.assertTrue(loaded)
        self.assertEqual(self.fsm.current_lesson_node['uid'], "node_B")
        self.assertEqual(len(self.fsm.completed_topics), 0)

if __name__ == '__main__':
    unittest.main()
