"""The tutor's awareness of how well-sourced the concept in front of it is.

The build already knew. `source_confidence` is computed per concept by the
grounding pass — and re-tried once against a broadened query when it comes back
thin — while `llm_fallback` marks a title generated to pad an empty lesson.
Both were written into structure.json and read by nobody, so a 0.12-confidence
concept was taught in exactly the same confident voice as a 0.9 one.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


class _MockFlaskApp:
    def __init__(self, *a, **kw): pass
    def route(self, *a, **kw): return lambda f: f
    def run(self, *a, **kw): pass


flask_mock = MagicMock()
flask_mock.Flask = _MockFlaskApp
flask_mock.request = MagicMock()
cb_mock = MagicMock()
cb_mock.__file__ = "mocked_course_builder.py"

core_deps = {
    "kuzu": MagicMock(), "libzim": MagicMock(),
    "sentence_transformers": MagicMock(), "psutil": MagicMock(),
    "yaml": MagicMock(), "fsrs_engine": MagicMock(), "safety": MagicMock(),
    "service_manager": MagicMock(), "db_manager": MagicMock(),
    "content_provider": MagicMock(), "course_builder": cb_mock,
}

with patch.dict("sys.modules", core_deps):
    from services.core.fsm_logic import MnemosyneFSM


def _fsm(node):
    with patch.object(MnemosyneFSM, "__init__", lambda self, *a, **kw: None):
        fsm = MnemosyneFSM.__new__(MnemosyneFSM)
        fsm.current_lesson_node = node
        return fsm


class TestGroundingNote(unittest.TestCase):
    def test_well_sourced_concept_gets_no_note(self):
        """Silence is the common case — the note must not become boilerplate the
        model learns to ignore."""
        self.assertEqual(_fsm({"source_confidence": 0.82})._grounding_note(), "")

    def test_thin_grounding_produces_a_caution(self):
        note = _fsm({"source_confidence": 0.12})._grounding_note()
        self.assertIn("GROUNDING", note)
        self.assertIn("0.12", note)

    def test_the_caution_names_what_not_to_assert(self):
        """A vague 'be careful' changes nothing. Naming figures, dates and
        results is what stops fluent invention."""
        note = _fsm({"source_confidence": 0.2})._grounding_note()
        for banned in ("figures", "dates"):
            self.assertIn(banned, note)

    def test_a_padded_stub_concept_is_flagged_even_without_a_score(self):
        """`llm_fallback` concepts never went through research at all, so they
        have no confidence number to fall below a threshold."""
        note = _fsm({"llm_fallback": True})._grounding_note()
        self.assertIn("GROUNDING", note)
        self.assertIn("no researched source material", note)

    def test_fallback_takes_precedence_over_a_score(self):
        note = _fsm({"llm_fallback": True, "source_confidence": 0.9})._grounding_note()
        self.assertIn("no researched source material", note)

    def test_unknown_confidence_is_not_treated_as_zero(self):
        """A course built before the field existed must not have every concept
        hedged."""
        self.assertEqual(_fsm({"source_confidence": None})._grounding_note(), "")
        self.assertEqual(_fsm({})._grounding_note(), "")

    def test_no_current_concept_does_not_raise(self):
        self.assertEqual(_fsm(None)._grounding_note(), "")


class TestQueueEntry(unittest.TestCase):
    """One builder for what used to be four copy-pasted dict literals."""

    def test_carries_the_quality_signals(self):
        entry = MnemosyneFSM._queue_entry(
            {"uid": "c1", "title": "Mitosis", "source_confidence": 0.3,
             "llm_fallback": True},
            "# Mitosis\n")
        self.assertEqual(entry["source_confidence"], 0.3)
        self.assertIs(entry["llm_fallback"], True)

    def test_keeps_the_pedagogy_fields_the_call_sites_relied_on(self):
        entry = MnemosyneFSM._queue_entry(
            {"uid": "c1", "title": "Mitosis", "bloom_level": 4,
             "learning_objectives": ["explain anaphase"],
             "complexity_role": "core", "module_bloom_target": 5},
            "body")
        self.assertEqual(entry["bloom_level"], 4)
        self.assertEqual(entry["learning_objectives"], ["explain anaphase"])
        self.assertEqual(entry["complexity_role"], "core")
        self.assertEqual(entry["module_bloom_target"], 5)

    def test_missing_content_becomes_empty_string_not_none(self):
        """Callers do `node["text"][:10000]`, which would raise on None."""
        self.assertEqual(
            MnemosyneFSM._queue_entry({"uid": "c1", "title": "T"}, None)["text"], "")


if __name__ == "__main__":
    unittest.main()
