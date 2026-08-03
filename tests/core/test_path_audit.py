"""Every node-path pathology detector must actually fire.

The depth contract judges each concept alone. These are the failures that only
exist BETWEEN nodes — a course can be twelve individually excellent cards and
still be a bad path. Each test builds a course that deliberately exhibits one
pathology and asserts the detector catches it, plus a clean course that must
trip nothing (a detector that fires on everything is as useless as one that
never fires).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

import tools.path_audit as pa  # noqa: E402


def _concept(title, bloom=1, prereqs=None):
    c = {"uid": "con_" + title.lower().replace(" ", "")[:10], "title": title,
         "bloom_level": bloom}
    if prereqs:
        c["prerequisites"] = prereqs
    return c


def _course(modules, title="test course", mastery=2):
    return {"uid": "course_test", "title": title, "mastery": mastery,
            "modules": modules}


def _module(title, concepts, bloom=1):
    return {"title": title, "bloom_target": bloom,
            "units": [{"title": title + " unit",
                       "lessons": [{"title": title + " lesson",
                                    "concepts": concepts}]}]}


class _Harness(unittest.TestCase):
    """Writes a course to a temp dir and audits it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = pa.COURSES_DIR
        pa.COURSES_DIR = self.dir

    def tearDown(self):
        pa.COURSES_DIR = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_audit(self, structure, bodies=None):
        cdir = os.path.join(self.dir, "course_test")
        os.makedirs(os.path.join(cdir, "content"), exist_ok=True)
        with open(os.path.join(cdir, "structure.json"), "w") as f:
            json.dump(structure, f)
        for uid, body in (bodies or {}).items():
            with open(os.path.join(cdir, "content", uid + ".md"), "w") as f:
                f.write(body)
        return pa.audit("course_test")


GOOD_BODY = ("The {t} describes how quantities relate. Step 1: let a = 3 and "
             "b = 4. Step 2: we compute the result as 5. This builds on the "
             "earlier idea of squares and triangles and grids. "
             "See https://en.wikipedia.org/wiki/Example ") * 4


class TestCleanCourseTripsNothing(_Harness):
    def test_clean_path_is_clean(self):
        cs = [_concept("Squares of numbers", 1), _concept("Triangle sides", 2),
              _concept("Grid areas", 3)]
        st = _course([_module("M1", cs[:2], 1), _module("M2", cs[2:], 2)])
        bodies = {c["uid"]: GOOD_BODY.format(t=c["title"]) for c in cs}
        r = self.run_audit(st, bodies)
        noisy = {k: v for k, v in r["counts"].items()
                 if v and k not in ("forward_refs", "isolated", "granularity")}
        self.assertEqual(noisy, {}, f"clean course tripped: {noisy}")


class TestStructuralPathologies(_Harness):
    def test_empty_container_module_unit_lesson(self):
        st = _course([{"title": "empty mod", "units": []},
                      {"title": "m2", "units": [{"title": "empty unit",
                                                 "lessons": []}]},
                      {"title": "m3", "units": [{"title": "u", "lessons": [
                          {"title": "empty lesson", "concepts": []}]}]}])
        r = self.run_audit(st)
        kinds = {e["kind"] for e in r["findings"]["empty_container"]}
        self.assertEqual(kinds, {"module", "unit", "lesson"})

    def test_exact_duplicate_titles(self):
        cs = [_concept("Same Thing"), _concept("Same Thing"), _concept("Other")]
        r = self.run_audit(_course([_module("M", cs)]))
        self.assertTrue(r["findings"]["exact_duplicate_title"])

    def test_missing_content(self):
        cs = [_concept("Taught"), _concept("Never written")]
        bodies = {cs[0]["uid"]: GOOD_BODY.format(t="Taught")}
        r = self.run_audit(_course([_module("M", cs)]), bodies)
        titles = [x["title"] for x in r["findings"]["missing_content"]]
        self.assertIn("Never written", titles)

    def test_module_imbalance(self):
        big = [_concept(f"Topic {i}") for i in range(9)]
        small = [_concept("Lonely")]
        r = self.run_audit(_course([_module("Big", big), _module("Small", small)]))
        self.assertTrue(r["findings"]["module_imbalance"])


class TestContentPathologies(_Harness):
    def test_duplicate_body_copy_paste(self):
        cs = [_concept("Alpha"), _concept("Beta")]
        same = GOOD_BODY.format(t="identical content here")
        r = self.run_audit(_course([_module("M", cs)]),
                           {cs[0]["uid"]: same, cs[1]["uid"]: same})
        self.assertTrue(r["findings"]["duplicate_body"],
                        "identical teaching under two titles must be caught")

    def test_repetition_near_duplicate_titles(self):
        cs = [_concept("Finding the hypotenuse length"),
              _concept("Hypotenuse length finding")]
        r = self.run_audit(_course([_module("M", cs)]))
        self.assertTrue(r["findings"]["repetition"])

    def test_under_covered_concept(self):
        cs = [_concept("Eigenvalue decomposition spectra")]
        body = "This lesson is about something else entirely. " * 20
        r = self.run_audit(_course([_module("M", cs)]), {cs[0]["uid"]: body})
        self.assertTrue(r["findings"]["under_covered"])

    def test_granularity_outliers(self):
        cs = [_concept(f"Topic {i}") for i in range(5)]
        bodies = {c["uid"]: "word " * 300 for c in cs}
        bodies[cs[0]["uid"]] = "word " * 20      # thin
        bodies[cs[1]["uid"]] = "word " * 900     # oversized
        r = self.run_audit(_course([_module("M", cs)]), bodies)
        kinds = {g["kind"] for g in r["findings"]["granularity"]}
        self.assertIn("thin", kinds)
        self.assertIn("oversized", kinds)


class TestOrderingPathologies(_Harness):
    def test_prerequisite_declared_after_the_concept(self):
        cs = [_concept("Advanced", prereqs=["Basics"]), _concept("Basics")]
        r = self.run_audit(_course([_module("M", cs)]))
        self.assertTrue(r["findings"]["ordering"])

    def test_dangling_prerequisite(self):
        cs = [_concept("Thing", prereqs=["Nonexistent concept"])]
        r = self.run_audit(_course([_module("M", cs)]))
        self.assertTrue(r["findings"]["dangling_prereq"])

    def test_circular_prerequisites(self):
        cs = [_concept("A", prereqs=["B"]), _concept("B", prereqs=["A"])]
        r = self.run_audit(_course([_module("M", cs)]))
        self.assertTrue(r["findings"]["circular_prereq"],
                        "A needs B needs A strands the learner")

    def test_forward_reference_in_body(self):
        cs = [_concept("Early node"), _concept("Quaternion rotation matrices")]
        bodies = {cs[0]["uid"]: "We rely on quaternion rotation matrices here. " * 12,
                  cs[1]["uid"]: GOOD_BODY.format(t="quaternion rotation matrices")}
        r = self.run_audit(_course([_module("M", cs)]), bodies)
        self.assertTrue(r["findings"]["forward_refs"])


class TestProgressionPathologies(_Harness):
    def test_bloom_regression(self):
        st = _course([_module("M1", [_concept("a")], bloom=3),
                      _module("M2", [_concept("b")], bloom=1)])
        r = self.run_audit(st)
        self.assertTrue(r["findings"]["bloom_regression"],
                        "cognitive level going backwards must be caught")

    def test_bloom_flat_across_course(self):
        st = _course([_module(f"M{i}", [_concept(f"c{i}")], bloom=2)
                      for i in range(4)])
        r = self.run_audit(st)
        self.assertTrue(r["findings"]["bloom_flat"])

    def test_healthy_ramp_is_not_flagged(self):
        st = _course([_module(f"M{i}", [_concept(f"c{i}")], bloom=i + 1)
                      for i in range(4)])
        r = self.run_audit(st)
        self.assertFalse(r["findings"]["bloom_regression"])
        self.assertFalse(r["findings"]["bloom_flat"])


class TestRobustness(_Harness):
    def test_missing_course_reports_error(self):
        self.assertIn("error", pa.audit("course_does_not_exist"))

    def test_course_with_no_concepts(self):
        r = self.run_audit(_course([_module("M", [])]))
        self.assertTrue(r.get("error") or r["counts"]["empty_container"])


if __name__ == '__main__':
    unittest.main()
