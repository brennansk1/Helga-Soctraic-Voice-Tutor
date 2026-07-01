"""Multi-course progress persistence — B15.7 fsm_sessions-row contract.

Rewritten from the old user_state.json file contract: per-student FSM state
now lives in the fsm_sessions row (one blob per student, courses keyed inside
it), with a one-time legacy import of user_state.json for the legacy student.
"""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import json
import tempfile
import shutil

core_deps = {
    'yaml': MagicMock(),
    'kuzu': MagicMock(),
    'libzim': MagicMock(),
}

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), 'services/core'))

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

import logging
logging.basicConfig = MagicMock()
logging.FileHandler = MagicMock()

with patch.dict('sys.modules', core_deps):
    from fsm_logic import MnemosyneFSM as FSMLogic
    from services.common.storage import StorageManager, DEFAULT_STUDENT_ID


class TestMultiCourse(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="helga_mc_")
        self.storage = StorageManager(self.data_dir)
        self.fsm = FSMLogic(storage=self.storage)
        self.fsm.data_root = self.data_dir
        self.fsm.state_file = os.path.join(self.data_dir, "user_state.json")
        self.fsm.speak = MagicMock()

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def _blob(self, student_id=DEFAULT_STUDENT_ID):
        row = self.storage.fsm.get(student_id)
        return json.loads(row["blob"]) if row else None

    def test_save_current_course_progress(self):
        self.fsm.active_course_uid = "course_123"
        self.fsm.current_lesson_node = {"uid": "node_A", "text": "Content A"}
        self.fsm.completed_topics = {"topic_1", "topic_2"}

        self.fsm._save_current_course_progress()

        data = self._blob()
        self.assertIsNotNone(data, "fsm_sessions row not written")
        self.assertIn('courses', data)
        self.assertIn('course_123', data['courses'])
        self.assertEqual(data['courses']['course_123']['current_node']['uid'], "node_A")
        self.assertEqual(data['last_active_uid'], "course_123")

    def test_load_course_progress(self):
        full_state = {
            "schema": 1,
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
        self.storage.fsm.upsert(DEFAULT_STUDENT_ID, json.dumps(full_state))

        loaded = self.fsm._load_course_progress("course_123")
        self.assertTrue(loaded)
        self.assertEqual(self.fsm.current_lesson_node['uid'], "node_A")
        self.assertIn("topic_1", self.fsm.completed_topics)

        loaded = self.fsm._load_course_progress("course_456")
        self.assertTrue(loaded)
        self.assertEqual(self.fsm.current_lesson_node['uid'], "node_B")
        self.assertEqual(len(self.fsm.completed_topics), 0)

    def test_legacy_user_state_json_imported(self):
        """The pre-multi-tenant user_state.json is adopted on first read when
        the legacy student has no fsm_sessions row yet (zero data loss)."""
        legacy = {"courses": {"course_old": {"current_node": {"uid": "node_L"},
                                             "completed_topics": ["t1"]}},
                  "last_active_uid": "course_old"}
        with open(self.fsm.state_file, "w") as f:
            json.dump(legacy, f)

        loaded = self.fsm._load_course_progress("course_old")
        self.assertTrue(loaded)
        self.assertEqual(self.fsm.current_lesson_node['uid'], "node_L")

    def test_delete_course_state(self):
        self.fsm.active_course_uid = "course_123"
        self.fsm.current_lesson_node = {"uid": "node_A"}
        self.fsm._save_current_course_progress()
        self.fsm.delete_course_state("course_123")
        data = self._blob()
        self.assertNotIn("course_123", data.get("courses", {}))
        self.assertIsNone(data.get("last_active_uid"))

    def test_per_student_blobs_are_separate(self):
        pid = self.storage.accounts.create_parent("mc@example.com", "h")
        a = self.storage.accounts.create_student(pid, "A")
        b = self.storage.accounts.create_student(pid, "B")

        fsm_a = FSMLogic(a, storage=self.storage)
        fsm_a.speak = MagicMock()
        fsm_b = FSMLogic(b, storage=self.storage)
        fsm_b.speak = MagicMock()

        fsm_a.active_course_uid = "course_x"
        fsm_a.current_lesson_node = {"uid": "node_A"}
        fsm_a._save_current_course_progress()

        fsm_b.active_course_uid = "course_x"
        fsm_b.current_lesson_node = {"uid": "node_B"}
        fsm_b._save_current_course_progress()

        self.assertEqual(self._blob(a)['courses']['course_x']['current_node']['uid'], "node_A")
        self.assertEqual(self._blob(b)['courses']['course_x']['current_node']['uid'], "node_B")


if __name__ == '__main__':
    unittest.main()
