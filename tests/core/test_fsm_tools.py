"""Tests for the FSM ↔ tutor-tool wiring (B14.8 / Task #14).

Covers the objective-verification pass and the data-pull callbacks. The full
FSM is heavy (config/threads/signals), so we build a bare instance via __new__
and set only the attributes these methods touch — matching test_fsm_logic.py.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

core_deps = {
    'kuzu': MagicMock(), 'libzim': MagicMock(), 'sentence_transformers': MagicMock(),
    'psutil': MagicMock(), 'yaml': MagicMock(), 'fsrs_engine': MagicMock(),
    'safety': MagicMock(), 'service_manager': MagicMock(), 'db_manager': MagicMock(),
    'content_provider': MagicMock(), 'course_builder': MagicMock(),
}
with patch.dict('sys.modules', core_deps):
    from services.core.fsm_logic import MnemosyneFSM


def _bare_fsm():
    with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
        fsm = MnemosyneFSM.__new__(MnemosyneFSM)
    fsm._tutor_tools_enabled = False
    fsm._tutor_tools_registry = None
    fsm.active_course_uid = "course_abc"
    fsm.llm_client = MagicMock()
    fsm.storage = MagicMock()
    return fsm


class TestObjectiveVerification(unittest.TestCase):
    def test_disabled_returns_empty_and_never_calls_model(self):
        fsm = _bare_fsm()  # flag off (default)
        note = fsm._verify_answer_objectively("What is 2+2?", "5")
        self.assertEqual(note, "")
        fsm.llm_client.chat_with_tools.assert_not_called()

    def test_enabled_with_tool_use_returns_note(self):
        fsm = _bare_fsm()
        fsm._tutor_tools_enabled = True
        fsm.llm_client.chat_with_tools.return_value = {
            "text": "The tool shows 2+2=4, so the student's 5 is incorrect.",
            "tool_calls": [{"name": "math_check", "args": {}, "result": {"ok": True}}],
        }
        note = fsm._verify_answer_objectively("What is 2+2?", "5")
        self.assertTrue(note.startswith("[TOOL CHECK"))
        self.assertIn("incorrect", note)

    def test_enabled_but_no_tool_used_returns_empty(self):
        fsm = _bare_fsm()
        fsm._tutor_tools_enabled = True
        fsm.llm_client.chat_with_tools.return_value = {"text": "anything", "tool_calls": []}
        self.assertEqual(fsm._verify_answer_objectively("Why is the sky blue?", "Rayleigh"), "")

    def test_no_tool_sentinel_returns_empty(self):
        fsm = _bare_fsm()
        fsm._tutor_tools_enabled = True
        fsm.llm_client.chat_with_tools.return_value = {
            "text": "NO_TOOL", "tool_calls": [{"name": "x", "args": {}, "result": {"ok": True}}],
        }
        self.assertEqual(fsm._verify_answer_objectively("Discuss themes", "love"), "")

    def test_never_raises_on_model_error(self):
        fsm = _bare_fsm()
        fsm._tutor_tools_enabled = True
        fsm.llm_client.chat_with_tools.side_effect = RuntimeError("ollama down")
        self.assertEqual(fsm._verify_answer_objectively("q", "a"), "")  # swallowed


class TestRegistryAndCallbacks(unittest.TestCase):
    def test_registry_is_none_when_disabled(self):
        self.assertIsNone(_bare_fsm()._get_tutor_tools())

    def test_registry_built_and_cached_when_enabled(self):
        fsm = _bare_fsm()
        fsm._tutor_tools_enabled = True
        reg1 = fsm._get_tutor_tools()
        self.assertIsNotNone(reg1)
        # data-pull tools registered because callbacks were injected
        self.assertIsNotNone(reg1.get("search_concepts"))
        self.assertIs(fsm._get_tutor_tools(), reg1)  # cached, not rebuilt

    def test_search_callback_shapes_results(self):
        fsm = _bare_fsm()
        fsm.storage.search.search.return_value = [
            {"title": "Photosynthesis", "concept_uid": "con_1", "content": "x" * 500}
        ]
        out = fsm._tool_search_concepts("plants")
        self.assertEqual(out[0]["title"], "Photosynthesis")
        self.assertLessEqual(len(out[0]["snippet"]), 240)

    def test_callbacks_never_raise(self):
        fsm = _bare_fsm()
        fsm.storage.search.search.side_effect = RuntimeError("db")
        fsm.storage.courses.get_concept_content.side_effect = RuntimeError("io")
        fsm.storage.progress.get_progress.side_effect = RuntimeError("db")
        self.assertEqual(fsm._tool_search_concepts("q"), [])
        self.assertEqual(fsm._tool_concept_content("con_1"), "")
        self.assertEqual(fsm._tool_get_mastery("con_1"), {})


if __name__ == "__main__":
    unittest.main()
