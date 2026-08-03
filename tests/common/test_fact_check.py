"""A1 tests: generated content must not teach falsehoods.

Measured motivation: substance_check found only 3/6 sampled concepts
SUBSTANTIVE, and the failures were FALSE technical claims, not missing rigor.
One verified by hand — "a necessary and sufficient condition for endogeneity
bias is the presence of a collider variable" — false in both directions, and it
passed every other check (right level, real citations, fluent prose).
"""

import os
import sys
import unittest
from unittest.mock import patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common import fact_check as fc  # noqa: E402


class TestAliasTolerantParsing(unittest.TestCase):
    """Small models ignore schema KEY names even when `format` is honoured."""

    def test_verdict_read_from_answer_key(self):
        """Observed live: asked for {"verdict"}, the model returned {"answer"}."""
        self.assertEqual(fc._verdict_of({"answer": "FALSE"}), "FALSE")

    def test_verdict_read_from_alternatives(self):
        for k in ("verdict", "result", "label", "judgement", "decision"):
            self.assertEqual(fc._verdict_of({k: "true"}), "TRUE", k)

    def test_verdict_is_case_insensitive_on_key_and_value(self):
        self.assertEqual(fc._verdict_of({"Verdict": "False"}), "FALSE")

    def test_field_falls_back_through_aliases(self):
        self.assertEqual(fc._field({"statement": "x"}, "claim", "statement"), "x")
        self.assertEqual(fc._field({}, "claim", default="d"), "d")

    def test_field_tolerates_non_dict(self):
        self.assertIsNone(fc._field(None, "claim"))
        self.assertIsNone(fc._field(["a"], "claim"))


class TestFalsePositiveGuard(unittest.TestCase):
    """A false positive is worse than a miss: it regenerates CORRECT content
    and invites the model to replace a true statement with a confident error."""

    def test_flagged_claim_requires_independent_confirmation(self):
        first = {"claims": [{"claim": "mediators absorb effect", "verdict": "FALSE"}]}
        with patch.object(fc, '_post', side_effect=[first, {"verdict": "TRUE"}]):
            r = fc.check_content("## Core Explanation\nSome content here.")
        self.assertEqual(r["false"], [],
                         "an unconfirmed flag must not be reported as false")
        self.assertEqual(len(r["flagged"]), 1, "but the flag is still recorded")

    def test_confirmed_claim_is_reported(self):
        first = {"claims": [{"claim": "2+2=5", "verdict": "FALSE"}]}
        with patch.object(fc, '_post', side_effect=[first, {"verdict": "FALSE"}]):
            r = fc.check_content("## Core Explanation\nSome content here.")
        self.assertEqual(len(r["false"]), 1)
        self.assertFalse(r["ok"])

    def test_unconfirmable_claim_is_not_acted_on(self):
        """If the confirmer can't answer, don't corrupt good content."""
        first = {"claims": [{"claim": "x", "verdict": "FALSE"}]}
        with patch.object(fc, '_post', side_effect=[first, None]):
            r = fc.check_content("## Core Explanation\nSome content here.")
        self.assertEqual(r["false"], [])


class TestFailsOpen(unittest.TestCase):
    def test_unavailable_checker_does_not_block_creation(self):
        with patch.object(fc, '_post', return_value=None):
            r = fc.check_content("## Core Explanation\nContent.")
        self.assertTrue(r["ok"], "a broken checker must not block course creation")
        self.assertFalse(r["checked"], "but it must not claim it checked either")

    def test_empty_content_is_not_checked(self):
        r = fc.check_content("")
        self.assertTrue(r["ok"])
        self.assertFalse(r["checked"])


class TestCorrectionHint(unittest.TestCase):
    def test_hint_names_the_specific_claim(self):
        h = fc.correction_hint([{"claim": "colliders cause endogeneity",
                                 "why": "colliders only bias on conditioning"}])
        self.assertIn("colliders cause endogeneity", h)
        self.assertIn("conditioning", h)

    def test_hint_reads_alias_keys(self):
        h = fc.correction_hint([{"statement": "bad claim", "reasoning": "because"}])
        self.assertIn("bad claim", h)

    def test_hint_warns_against_substituting_new_errors(self):
        h = fc.correction_hint([{"claim": "x", "why": "y"}])
        self.assertIn("unsure", h.lower())

    def test_no_hint_when_clean(self):
        self.assertEqual(fc.correction_hint([]), "")


class TestPipelineWiring(unittest.TestCase):
    def test_hydrator_runs_fact_check(self):
        import inspect
        from services.core.course_builder import ContentHydrator
        self.assertIn("_apply_fact_check",
                      inspect.getsource(ContentHydrator.hydrate))

    def test_course_records_a_factual_verdict(self):
        import inspect
        from services.core.course_builder import ContentHydrator
        src = inspect.getsource(ContentHydrator.hydrate)
        self.assertIn("fact_check", src)
        self.assertIn("concepts_with_false_claims", src)


if __name__ == '__main__':
    unittest.main()
