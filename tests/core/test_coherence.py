"""A sourceless course must face an equally hard bar, not a shorter one.

With a matched textbook the gate can ask "does this cover the source, in the
source's order?". Without one those criteria cannot run. Marking them N/A is
correct -- scoring a missing reference as 0 would make a sourceless course look
identical to one that FAILED against its source -- but N/A must not mean easier,
or "no source" becomes the way to pass.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.coherence import (  # noqa: E402
    applicable_criteria, check_coherence, gate_summary)


def _course(*module_concepts):
    return {"modules": [
        {"title": f"M{i}", "units": [{"title": "u", "lessons": [
            {"title": "l", "concepts": [{"title": t} for t in titles]}]}]}
        for i, titles in enumerate(module_concepts)]}


class TestForwardReferences(unittest.TestCase):
    def test_a_well_ordered_course_passes(self):
        c = _course(["Vectors and Spans", "Vector Addition"],
                    ["Matrix Basics", "Matrix Multiplication"],
                    ["Eigenvalue Definition", "Eigenvalue Computation"])
        assert check_coherence(c)["verdict"] == "ok"

    def test_using_an_idea_before_teaching_it_is_caught(self):
        """'Diagonalizing with Eigenvalues' in module 1 when eigenvalues are
        first taught in module 3 means the learner meets the word before the
        idea."""
        c = _course(["Diagonalizing with Eigenvalue Methods", "Vector Addition"],
                    ["Matrix Basics", "Matrix Multiplication"],
                    ["Eigenvalue Definition", "Eigenvalue Computation"])
        r = check_coherence(c)
        assert r["forward_references"] >= 1
        assert any(e["term"].startswith("eigenvalue") for e in r["examples"])

    def test_a_short_course_is_not_judged(self):
        assert check_coherence(_course(["A", "B"]))["checked"] is False

    def test_a_term_used_once_is_not_evidence(self):
        """A word appearing a single time is passing prose, not a curriculum
        dependency."""
        c = _course(["Vectors", "Spans", "Bases"], ["Matrices", "Products"],
                    ["Determinants", "Cofactors"])
        assert check_coherence(c)["verdict"] == "ok"


class TestGateConfiguration(unittest.TestCase):
    def test_a_sourceless_gate_drops_only_the_source_criteria(self):
        sourced = set(applicable_criteria(True))
        sourceless = set(applicable_criteria(False))
        assert sourced - sourceless == {"source_coverage", "sequencing"}

    def test_coherence_runs_in_both_configurations(self):
        """It is the replacement, so it must not be conditional on the thing it
        replaces."""
        for has_source in (True, False):
            assert "internal_coherence" in applicable_criteria(has_source)

    def test_depth_and_fact_check_run_in_both(self):
        """These carry the actual quality bar when no source exists."""
        for has_source in (True, False):
            crit = applicable_criteria(has_source)
            assert "depth_contract" in crit and "fact_check" in crit

    def test_a_sourceless_course_is_not_graded_more_leniently(self):
        """Same PROPORTION of a smaller set -- the same standard on the questions
        that can honestly be asked."""
        results = {c: True for c in applicable_criteria(False)}
        results["fact_check"] = False
        s = gate_summary(results, has_source=False)
        assert s["pass_rate"] < 1.0
        assert "fact_check" in s["failed"]

    def test_a_criterion_that_did_not_run_is_reported_not_hidden(self):
        """A gate that silently drops criteria reports a clean pass on a weaker
        test."""
        s = gate_summary({"depth_contract": True}, has_source=True)
        assert s["complete"] is False
        assert "source_coverage" in s["criteria_not_run"]
        assert "source_coverage" not in s["passed"]

    def test_the_configuration_is_named_in_the_summary(self):
        assert gate_summary({}, True)["configuration"] == "sourced"
        assert gate_summary({}, False)["configuration"] == "sourceless"


if __name__ == "__main__":
    unittest.main()
