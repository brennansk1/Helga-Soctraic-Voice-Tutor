"""Lesson-batched hydration: grouping, prompting, and splitting the response."""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.lesson_batch import (  # noqa: E402
    MAX_PER_BATCH, batch_prompt, group_by_lesson, split_batch, usable)


def _doc(title, words=120):
    return (f"## Mastery Criteria\nGrade 3 requires: {title}.\n\n"
            f"## Core Explanation\n" + " ".join(["word"] * words) + "\n")


class TestGrouping(unittest.TestCase):
    def test_consecutive_runs_only(self):
        """Teaching order is what keeps the ledger complete for everything
        prior, so gathering a lesson out of order would trade a correctness
        guarantee for a slightly larger batch."""
        entries = [("a",), ("b",), ("c",), ("d",)]
        lessons = {"a": "L1", "b": "L2", "c": "L1", "d": "L1"}
        b = group_by_lesson(entries, lambda u: lessons[u])
        self.assertEqual([[e[0] for e in g] for g in b],
                         [["a"], ["b"], ["c", "d"]])

    def test_a_long_lesson_is_chunked(self):
        entries = [(str(i),) for i in range(9)]
        b = group_by_lesson(entries, lambda u: "L1")
        self.assertTrue(all(len(g) <= MAX_PER_BATCH for g in b))
        self.assertEqual(sum(len(g) for g in b), 9)

    def test_an_empty_list_yields_no_batches(self):
        self.assertEqual(group_by_lesson([], lambda u: "L"), [])


class TestPrompt(unittest.TestCase):
    def test_invariant_material_leads(self):
        """Same prefix-caching reason the per-concept prompt was inverted."""
        p = batch_prompt([{"title": "A", "objectives": []}], "C", "TEMPLATE")
        self.assertLess(p.index("TEMPLATE"), p.index("THIS LESSON"))

    def test_every_title_reaches_the_prompt(self):
        p = batch_prompt([{"title": "Eigenvalues", "objectives": ["x"]},
                          {"title": "Eigenvectors", "objectives": []}],
                         "Linear Algebra", "T")
        self.assertIn("Eigenvalues", p)
        self.assertIn("Eigenvectors", p)
        self.assertIn("refer to it by name rather than explaining it twice", p)


class TestSplitting(unittest.TestCase):
    def test_a_clean_batch_splits(self):
        text = _doc("A") + "\n===CONCEPT-BREAK===\n" + _doc("B")
        parts = split_batch(text, 2)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(p and "Mastery Criteria" in p for p in parts))

    def test_a_missing_marker_falls_back_to_headings(self):
        """A model that drops the separator may still have written the right
        number of documents."""
        parts = split_batch(_doc("A") + "\n" + _doc("B"), 2)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(p for p in parts))

    def test_a_wrong_count_returns_none_not_stubs(self):
        """None makes the caller re-hydrate that concept alone. A stub would
        look like content and ship."""
        self.assertEqual(split_batch(_doc("A"), 3), [None, None, None])

    def test_empty_input_is_survivable(self):
        self.assertEqual(split_batch("", 2), [None, None])

    def test_an_echoed_marker_is_stripped(self):
        text = ("<<<CONCEPT 1: A>>>\n" + _doc("A") + "\n===CONCEPT-BREAK===\n"
                + "<<<CONCEPT 2: B>>>\n" + _doc("B"))
        parts = split_batch(text, 2)
        self.assertFalse(any(p.startswith("<<<CONCEPT") for p in parts))


class TestUsable(unittest.TestCase):
    def test_a_truncated_tail_is_caught(self):
        """A batch can return the first document complete and the rest cut off
        — the failure a single-document check would miss."""
        self.assertTrue(usable(_doc("A")))
        self.assertFalse(usable("## Mastery Criteria\nGrade 3 requires: x.\n"))
        self.assertFalse(usable(None))

    def test_prose_without_the_required_heading_is_not_usable(self):
        self.assertFalse(usable(" ".join(["word"] * 300)))
