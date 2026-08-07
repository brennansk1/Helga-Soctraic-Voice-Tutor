import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Mock dependencies before importing fsm_logic
fsrs_mock = MagicMock()
cb_mock = MagicMock()

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
    from services.common.visual_aids import AidStore

class TestA1VisualAids(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
            self.fsm = MnemosyneFSM.__new__(MnemosyneFSM)
        self.fsm.storage = MagicMock()
        self.fsm.aid_store = AidStore(capacity=64)
        self.fsm._visual_aids_enabled = True
        self.fsm.current_context = ""
        self.fsm.current_lesson_node = {"uid": "concept_1"}
        self.fsm.transcript = []
        self.fsm.concept_question_count = 0
        self.fsm.socratic_retry_count = 0
        self.fsm._last_socratic_grade = 0
        self.fsm.current_bloom_level = 1
        self.fsm.grade_band = "6-8"
        self.fsm.concept_correct_streak = 0
        self.fsm._turns_since_aid = 99
        self.fsm._aid_kinds_this_concept = []
        self.fsm._aid_ids_this_concept = set()

    def test_a1_manifest_surfaces_aids_on_concept_enter(self):
        # Setup a dummy manifest with pre-built aids
        self.fsm._asset_manifest = {
            "concepts": {
                "concept_1": {
                    "opening": {
                        "id": "abc12345",
                        "kind": "number_line",
                        "spec": {"min": 0, "max": 10},
                        "stage": 0,
                        "title": "A number line"
                    }
                }
            }
        }
        
        # When _reset_aid_budget runs (which happens when entering a concept)
        self.fsm._reset_aid_budget()
        
        # The phase 3 aid should be available for reuse
        self.assertIn("opening", self.fsm._concept_aids)
        self.assertEqual(self.fsm._concept_aids["opening"]["kind"], "number_line")

    def test_a1_course_with_no_manifest_degrades_silently(self):
        self.fsm._asset_manifest = None
        
        # No manifest, shouldn't crash
        self.fsm._reset_aid_budget()
        
        self.assertEqual(self.fsm._concept_aids, {})

    @patch('services.common.aid_policy.decide')
    def test_a1_aid_reaches_transcript_render_path(self, mock_decide):
        # Setup aid policy to return a reuse action
        mock_decide.return_value = MagicMock(action="reuse", slot="opening", reason="test")
        
        self.fsm._concept_aids = {
            "opening": {
                "id": "def67890",
                "kind": "geometry",
                "spec": {},
                "title": "A triangle",
                "stage": 0,
                "caption": "",
                "alt": "A visual aid"
            }
        }
        
        # Call the logic that surfaces the aid
        decision = self.fsm._decide_visual_aid("QUESTION")
        
        self.assertEqual(decision.action, "reuse")
        self.assertEqual(len(self.fsm._pending_aids), 1)
        self.assertEqual(self.fsm._pending_aids[0]["id"], "def67890")
        
        # Test it reaches the transcript via add_message
        self.fsm.add_message("Here is a question.", record=True)
        self.assertEqual(len(self.fsm.transcript), 1)
        self.assertIn("aids", self.fsm.transcript[0])
        
        # Transcript should contain the aid descriptor
        descriptor = self.fsm.transcript[0]["aids"][0]
        self.assertEqual(descriptor["id"], "def67890")
        self.assertEqual(descriptor["kind"], "geometry")
