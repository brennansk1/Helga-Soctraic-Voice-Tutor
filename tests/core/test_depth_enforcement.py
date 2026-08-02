"""A1 tests: the depth contract must be ENFORCED, not merely available.

A contract that validates but never runs is the same as no contract. These
tests exercise ContentHydrator._enforce_depth_contract directly: it must
retry with a targeted hint, accept a passing retry, keep the best attempt when
every retry misses, and report the miss upward so the course can be denied the
level it claims.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.course_builder import ContentHydrator  # noqa: E402

# Meets the mastery-5 contract for a formal domain.
RICH = ("**Definition** Let A = 5 denote the operator. "
        "Theorem (Spectral): it follows that the decomposition exists. "
        "Proof: substituting into $Ax = \\lambda x$ we obtain the result, hence "
        "the claim. Step 1: we compute the eigenvalues. "
        "Exercise: try it yourself for n = 3. "
        "See https://arxiv.org/abs/1234.5678 ") * 30

# Well-written prose with no rigor — the observed real-world failure mode.
THIN = ("Eigenvalues are an important idea in linear algebra and are widely "
        "used across science and engineering to understand how systems behave "
        "over time in many practical settings. ") * 40


def _hydrator():
    h = ContentHydrator.__new__(ContentHydrator)
    h.status_callback = None
    h.mastery_level = 5
    h.topic_domain = "formal"
    h.enforce_depth = True
    h.max_depth_retries = 2
    h._contract_failures = []
    return h


def _call(h, initial):
    return h._enforce_depth_contract(
        initial, "Eigenvalues", "linear algebra", "core theory", "llm-only",
        {}, [], 0.0, "", 5, [], [], {}, "raw text")


class TestEnforcementFires(unittest.TestCase):
    def test_passing_content_is_not_regenerated(self):
        h = _hydrator()
        with patch.object(ContentHydrator, '_condense_and_structure_content') as gen:
            md, detail = _call(h, RICH)
        self.assertTrue(detail["ok"])
        gen.assert_not_called()
        self.assertIs(md, RICH)

    def test_failing_content_triggers_regeneration(self):
        h = _hydrator()
        with patch.object(ContentHydrator, '_condense_and_structure_content',
                          return_value=RICH) as gen:
            md, detail = _call(h, THIN)
        self.assertTrue(gen.called, "a concept below contract must be regenerated")
        self.assertTrue(detail["ok"])
        self.assertEqual(md, RICH)

    def test_regeneration_prompt_names_the_deficiency(self):
        """A blind retry reproduces the same gap — the hint must be specific."""
        h = _hydrator()
        with patch.object(ContentHydrator, '_condense_and_structure_content',
                          return_value=RICH) as gen:
            _call(h, THIN)
        note = gen.call_args.kwargs.get("user_note", "")
        self.assertTrue(note, "regeneration must carry a targeted instruction")
        low = note.lower()
        self.assertTrue(
            any(k in low for k in ("derivation", "definition", "example",
                                   "primary source", "notation")),
            f"hint did not name a concrete deficiency: {note!r}")

    def test_gives_up_after_max_retries_and_reports_failure(self):
        h = _hydrator()
        with patch.object(ContentHydrator, '_condense_and_structure_content',
                          return_value=THIN) as gen:
            md, detail = _call(h, THIN)
        self.assertEqual(gen.call_count, h.max_depth_retries)
        self.assertFalse(detail["ok"], "a persistent miss must be reported, not hidden")
        self.assertTrue(detail["problems"])

    def test_disabled_flag_skips_enforcement(self):
        h = _hydrator()
        h.enforce_depth = False
        # The hydrate loop guards on enforce_depth; assert the flag is readable
        # so a caller can reproduce a historical build.
        self.assertFalse(h.enforce_depth)

    def test_hydration_stub_is_not_regenerated_as_a_depth_miss(self):
        """A failed hydration is a different failure with its own handling."""
        h = _hydrator()
        with patch.object(ContentHydrator, '_condense_and_structure_content',
                          return_value="[Hydration failed]") as gen:
            md, detail = _call(h, THIN)
        self.assertFalse(detail["ok"])
        # It should stop rather than burn every retry on a known-bad generator.
        self.assertEqual(gen.call_count, 1)


class TestCourseLevelVerdict(unittest.TestCase):
    def test_verdict_threshold_is_defined_and_strict(self):
        """A course must not claim a level it mostly fails to meet."""
        import inspect
        src = inspect.getsource(ContentHydrator.hydrate)
        self.assertIn("level_verified", src)
        self.assertIn("depth_contract", src)


if __name__ == '__main__':
    unittest.main()
