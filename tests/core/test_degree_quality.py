"""A programme fails in ways no course-level check can see.

skeleton_qa grades one course. A degree can have a capstone in term 1, a
prerequisite nobody can satisfy, nine courses in one term and one in another, or
twenty courses that are the same subject renamed -- none of which a course-level
instrument can detect.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.degree_quality import (  # noqa: E402
    assess, check_prerequisite_specificity)


def _plan(courses, terms=4, subject="X", template="associate"):
    return {"subject": subject, "template": template, "terms": terms,
            "courses": courses}


def _c(title, term, slot="core", requires=None):
    return {"title": title, "term": term, "slot": slot,
            "requires": requires or []}


def _healthy():
    return _plan([
        _c("ENG 101: Composition", 1, "gen_ed"),
        _c("BIO 101: Biology", 1, "gen_ed"),
        _c("MAT 101: Algebra", 1, "gen_ed"),
        _c("NUR 101: Intro Nursing", 2, "core"),
        _c("NUR 102: Foundations", 2, "core"),
        _c("PSY 101: Psychology", 2, "gen_ed"),
        _c("NUR 201: Med-Surg", 3, "core", ["NUR 101: Intro Nursing"]),
        _c("NUR 202: Pharmacology", 3, "core", ["NUR 102: Foundations"]),
        _c("SOC 101: Sociology", 3, "gen_ed"),
        _c("NUR 301: Community", 4, "elective", ["NUR 201: Med-Surg"]),
        _c("NUR 490: Capstone", 4, "capstone"),
    ])


class TestHealthyProgramme(unittest.TestCase):
    def test_a_well_formed_degree_passes(self):
        assert assess(_healthy())["verdict"] == "DEGREE_SHAPED"


class TestDefectsACourseCheckCannotSee(unittest.TestCase):
    def test_a_capstone_in_the_wrong_term_fails(self):
        p = _healthy()
        next(c for c in p["courses"] if c["slot"] == "capstone")["term"] = 1
        r = assess(p)
        assert "capstone" in r["failed"]

    def test_a_prerequisite_not_in_the_programme_fails(self):
        p = _healthy()
        p["courses"][6]["requires"] = ["NUR 999: Does Not Exist"]
        assert "prerequisites" in assess(p)["failed"]

    def test_a_prerequisite_in_the_same_term_fails(self):
        p = _healthy()
        p["courses"][6]["requires"] = ["NUR 201: Med-Surg"]   # itself, same term
        assert "prerequisites" in assess(p)["failed"]

    def test_a_programme_with_no_prerequisites_at_all_fails(self):
        """Not validated -- unstructured. A real degree has levelled sequences."""
        p = _healthy()
        for c in p["courses"]:
            c["requires"] = []
        assert "prerequisites" in assess(p)["failed"]

    def test_an_empty_term_fails(self):
        p = _healthy()
        for c in p["courses"]:
            if c["term"] == 2:
                c["term"] = 1
        assert "term_balance" in assess(p)["failed"]

    def test_one_subject_repeated_is_not_a_degree(self):
        p = _plan([_c(f"NUR {100+i}: Nursing {i}", (i % 4) + 1) for i in range(12)])
        assert "breadth" in assess(p)["failed"]

    def test_placeholder_titles_fail(self):
        """"Nursing: gen_ed 1" is the shape produced when nothing filled the
        slots -- the degree-level equivalent of a generic section title."""
        p = _healthy()
        p["courses"][0]["title"] = "Nursing: gen_ed 1"
        r = assess(p)
        assert "titles" in r["failed"]


class TestRealPlans(unittest.TestCase):
    """Hermetic: no model. A transcribed curriculum needs none, and the
    no-curriculum path without a model is SUPPOSED to produce placeholders."""

    def _built(self, subject):
        from services.core.program import plan_degree
        return plan_degree(subject, "associate")

    def test_a_transcribed_curriculum_is_degree_shaped_with_no_model(self):
        """Economics has a reference file, so it needs no LLM at all."""
        assert assess(self._built("Economics"))["verdict"] == "DEGREE_SHAPED"

    def test_the_no_model_fallback_is_caught_not_passed(self):
        """With no curriculum AND no model, plan_degree fills slots with
        "Subject: gen_ed 1" placeholders — by design, so a build never crashes.
        The instrument must catch that rather than call it a degree, or the
        fallback would be indistinguishable from a real programme.

        The live path — no curriculum, WITH a model — is verified separately in
        docs/TASK0_RESULTS.md and reaches DEGREE_SHAPED."""
        r = assess(self._built("Underwater Basket Weaving"))
        assert r["verdict"] == "NOT_DEGREE_SHAPED"
        assert "titles" in r["failed"] and "breadth" in r["failed"]


if __name__ == "__main__":
    unittest.main()


class TestPrerequisiteSpecificity(unittest.TestCase):
    """A term barrier satisfies check_prerequisites perfectly.

    Every edge resolves and every edge points earlier, so the original check
    passes — while the graph says nothing at all. Measured on real plans: a
    model-proposed Dungeon Mastering associate carried 37 edges across 20
    courses against Economics' 7, because four term-2 cores declared the
    identical set of five term-1 cores.
    """

    def _plan(self, courses):
        return {"subject": "X", "template": "associate", "terms": 2,
                "courses": courses}

    def test_a_term_barrier_is_caught(self):
        prior = [f"C{i}" for i in range(1, 6)]
        courses = [{"title": t, "term": 1, "slot": "core"} for t in prior]
        courses += [{"title": f"D{i}", "term": 2, "slot": "core",
                     "requires": list(prior)} for i in range(1, 5)]
        r = check_prerequisite_specificity(self._plan(courses))
        self.assertTrue(r["checked"])
        self.assertFalse(r["ok"], "four identical requirement sets is a barrier")
        self.assertEqual(r["shared_with_sibling"], 4)

    def test_specific_prerequisites_pass(self):
        courses = [
            {"title": "Financial Accounting", "term": 1, "slot": "core"},
            {"title": "Calculus I", "term": 1, "slot": "core"},
            {"title": "Managerial Accounting", "term": 2, "slot": "core",
             "requires": ["Financial Accounting"]},
            {"title": "Calculus II", "term": 2, "slot": "core",
             "requires": ["Calculus I"]},
        ]
        r = check_prerequisite_specificity(self._plan(courses))
        self.assertTrue(r["ok"])
        self.assertEqual(r["shared_with_sibling"], 0)

    def test_density_alone_is_not_the_defect(self):
        """A dense graph can be legitimate. Duplication is the tell, not volume."""
        courses = [{"title": f"C{i}", "term": 1, "slot": "core"} for i in range(1, 5)]
        courses += [
            {"title": "D1", "term": 2, "slot": "core", "requires": ["C1", "C2", "C3"]},
            {"title": "D2", "term": 2, "slot": "core", "requires": ["C2", "C3", "C4"]},
            {"title": "D3", "term": 2, "slot": "core", "requires": ["C1", "C4"]},
        ]
        r = check_prerequisite_specificity(self._plan(courses))
        self.assertTrue(r["ok"], "distinct dense sets are informative")
        self.assertGreater(r["mean_edges_per_gated"], 2)

    def test_no_edges_is_not_run_rather_than_passed(self):
        courses = [{"title": "C1", "term": 1, "slot": "core"}]
        r = check_prerequisite_specificity(self._plan(courses))
        self.assertFalse(r["checked"])
        self.assertNotIn("ok", r)
