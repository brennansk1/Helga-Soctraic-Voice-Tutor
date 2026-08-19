"""The teaching object — a concept parsed into addressable structure."""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core.teaching_object import (  # noqa: E402
    build, completeness, misconceptions, question_seeds, worked_steps)

FULL = """## Mastery Criteria
At Bloom 3 (Apply), the student demonstrates mastery by:
Grade 3 requires: correctly computing an expected value.

## Core Explanation
A d20 is uniform over 1 to 20.

## Key Facts
- A twenty-sided die is uniform across all twenty faces.
- The expected value of a d20 is 10.5.

## Real-World Examples
Step 1: let the faces be 1..20. Step 2: sum them to 210. Step 3: divide by 20 to get 10.5.

## Misconceptions
- **Belief**: Advantage adds five to the roll.
  **Correction**: Advantage takes the maximum of two independent rolls.

## Edge Cases & Limitations
- Loaded dice break the uniformity assumption.

## Socratic Hooks
- Bloom 1-2: What numbers can a d20 show?
- Bloom 3-4: How would you compute the average of a d12?
- Bloom 5-6: When does expected value mislead a designer?
"""


class TestParsing(unittest.TestCase):
    def test_worked_steps_are_ordered(self):
        s = worked_steps(FULL)
        self.assertEqual(len(s), 3)
        self.assertTrue(s[0].startswith("Step 1"))

    def test_a_bullet_pattern_must_not_eat_bold(self):
        """`[-*•]` matches the first asterisk of **Correction**, leaving
        `*Correction**` and silently dropping every pair. Found twice in this
        codebase — once in the ledger, once here."""
        self.assertEqual(len(misconceptions(FULL)), 1)
        self.assertIn("maximum", misconceptions(FULL)[0]["correction"])

    def test_question_seeds_are_keyed_by_bloom_band(self):
        """Bloom moves within a session, so the tutor needs to reach for a
        band rather than take the next item in a list."""
        self.assertEqual(sorted(question_seeds(FULL)), ["1-2", "3-4", "5-6"])

    def test_prose_is_demoted_not_discarded(self):
        obj = build(FULL, "con_a", "Dice")
        self.assertTrue(obj["prose_fallback"], "LECTURE mode still needs it")


class TestCompleteness(unittest.TestCase):
    def test_a_full_concept_scores_one(self):
        self.assertEqual(completeness(build(FULL, "c", "Dice"))["score"], 1.0)

    def test_a_hollow_concept_is_caught(self):
        """Structurally complete, substantively empty — the section template
        cannot see this by construction, because passing it IS having headings."""
        hollow = build("## Core Explanation\nIt is important and widely used.\n",
                       "c", "Vague")
        self.assertLess(completeness(hollow)["score"], 0.3)

    def test_malformed_input_yields_a_sparse_object_not_an_exception(self):
        obj = build("not markdown at all", "c", "X")
        self.assertEqual(obj["concept_uid"], "c")
        self.assertEqual(completeness(obj)["score"], 0.0)

    def test_none_input_is_survivable(self):
        self.assertIsInstance(build(None, "c", "X"), dict)
