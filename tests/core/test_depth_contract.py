"""A1 tests: the depth contract must make the mastery slider mean something.

Context: on the only pre-existing course, every concept landed in a 626-876
word band (stdev 57.7) regardless of Bloom level or requested mastery, and a
course declaring mastery=4 ("Proficiency", whose own profile promises "formal
definitions, named theorems and criteria") contained named results in 1 of 36
concepts and math notation in 2 of 36. The slider was decorative.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.depth_contract import (  # noqa: E402
    DEPTH_CONTRACTS, contract_for, validate_concept, validate_course,
    regeneration_hint,
)


class TestContractEscalates(unittest.TestCase):
    def test_requirements_strictly_grow_with_mastery(self):
        prev = set()
        for level in sorted(DEPTH_CONTRACTS):
            req = set(DEPTH_CONTRACTS[level]["required"])
            self.assertTrue(
                len(req) >= len(prev),
                f"level {level} requires fewer elements than {level-1} — the "
                f"contract must escalate or the slider means nothing"
            )
            prev = req

    def test_high_levels_demand_rigor_not_just_length(self):
        for level in (4, 5):
            req = DEPTH_CONTRACTS[level]["required"]
            self.assertIn("derivation_or_proof", req)
            self.assertIn("primary_source", req)
            self.assertIn("named_result", req)

    def test_word_bands_are_bands_not_floors(self):
        """A max matters: the observed failure was every level converging on
        the same ~770 words, so 'long enough' cannot be the only test."""
        for level, c in DEPTH_CONTRACTS.items():
            self.assertLess(c["word_min"], c["word_max"])


class TestWorkedExampleDetection(unittest.TestCase):
    """The detector must not accept a described example as a worked one."""

    NARRATIVE = (
        "## Real-World Examples\n"
        "A study focusing on the efficacy of a new drug across various clinical "
        "trial sites uses causal inference techniques to estimate its overall "
        "effectiveness. This approach is crucial when combining evidence from "
        "multiple RCTs with varying patient populations."
    )
    WORKED = (
        "## Example\n"
        "Step 1: Let n = 250 be the sample size.\n"
        "Step 2: We compute the propensity score for each unit.\n"
        "Substituting into the estimator we obtain an ATE of = 0.42.\n"
    )

    def test_narrative_example_does_not_satisfy_worked_example(self):
        ok, problems, detail = validate_concept(self.NARRATIVE * 6, 4, "causal inference")
        self.assertIn(
            "worked_example", detail["missing"],
            "A prose 'Real-World Examples' section describes that an example "
            "exists; it does not work one. All 36 concepts in the sample course "
            "had this section and none had a worked example."
        )

    def test_genuine_worked_example_is_detected(self):
        _, _, detail = validate_concept(self.WORKED * 8, 4, "causal inference")
        self.assertNotIn("worked_example", detail["missing"])


class TestDomainAdaptation(unittest.TestCase):
    def test_humanities_topics_do_not_require_math_notation(self):
        req = contract_for(5, "the french revolution")["required"]
        self.assertNotIn(
            "formal_notation", req,
            "Requiring equations in a history course pushes the generator to "
            "fake them, which is worse than not requiring them."
        )

    def test_stem_topics_do_require_math_notation(self):
        self.assertIn("formal_notation", contract_for(5, "linear algebra")["required"])

    def test_humanities_still_requires_rigor(self):
        req = contract_for(5, "the french revolution")["required"]
        self.assertIn("primary_source", req)
        self.assertIn("derivation_or_proof", req)


class TestRegenerationHint(unittest.TestCase):
    def test_hint_names_the_specific_deficiency(self):
        _, problems, _ = validate_concept("too short.", 5, "linear algebra")
        hint = regeneration_hint(problems)
        self.assertTrue(hint)
        self.assertIn("derivation", hint.lower())
        self.assertIn("primary source", hint.lower())

    def test_no_hint_when_nothing_is_wrong(self):
        self.assertEqual(regeneration_hint([]), "")


class TestCourseLevelValidation(unittest.TestCase):
    def test_summary_counts_and_pass_rate(self):
        good = ("**Definition** Let A = 5 be a matrix. Theorem: it follows that "
                "we obtain the result. Step 1: we compute $Ax$. Proof: hence "
                "the claim. Exercise: try it yourself. "
                "See https://arxiv.org/abs/1234.5678 ") * 40
        r = validate_course([("a", good), ("b", "thin")], 5, "linear algebra")
        self.assertEqual(r["total"], 2)
        self.assertIn("b", r["failing_uids"])
        self.assertEqual(r["pass_rate"], 50.0)


if __name__ == '__main__':
    unittest.main()
