"""Gate criterion 6 (syllabus realism) must actually run during generation.

It was the ONLY criterion with external ground truth — every other one is an
LLM judging output an LLM wrote — and it was the only one that ran by hand, so
in practice it never ran at all.

These tests pin the wiring, not the judge's opinion: that it runs pre-persist,
that its verdict is recorded on the course, that a judge outage degrades to
"not measured" instead of failing the build, and that it blocks only when the
operator opts in.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

import tools.syllabus_check as sc  # noqa: E402
from services.core.course_builder import (  # noqa: E402
    CourseCreationError, SkeletonBuilder,
)


def _structure():
    return {
        "uid": "course_test", "title": "Causal Inference", "mastery": 3,
        "modules": [{
            "title": "Potential Outcomes",
            "units": [{"lessons": [{"title": "Counterfactuals",
                                    "concepts": [{"title": "SUTVA"},
                                                 {"title": "ATE"}]}]}],
        }],
    }


def _builder():
    b = SkeletonBuilder.__new__(SkeletonBuilder)
    b.status_callback = MagicMock()
    return b


class TestOutlineFromMemory(unittest.TestCase):
    """The check has to work on a structure that is not on disk yet."""

    def test_outline_renders_modules_lessons_and_concepts(self):
        out = sc.outline_of(_structure())
        self.assertIn("MODULE: Potential Outcomes", out)
        self.assertIn("Counterfactuals: SUTVA; ATE", out)

    def test_empty_structure_is_an_error_not_a_crash(self):
        self.assertIn("error", sc.check_structure({}))

    def test_instrument_failure_is_reported_not_raised(self):
        with patch.object(sc, "core_topics", side_effect=RuntimeError("judge down")):
            res = sc.check_structure(_structure())
        self.assertIn("error", res)
        self.assertIn("judge down", res["error"])


class TestVerdictIsRecordedOnTheCourse(unittest.TestCase):

    def _run(self, result, env=None):
        course = _structure()
        b = _builder()
        with patch.object(sc, "check_structure", return_value=result), \
             patch.dict(os.environ, env or {}, clear=False):
            b._record_syllabus_check(course)
        return course, b

    def test_result_is_attached_to_the_structure(self):
        course, _ = self._run({"verdict": "ADEQUATE", "coverage_pct": 88,
                               "topics_checked": 8, "missing": []})
        self.assertEqual(course["syllabus_check"]["verdict"], "ADEQUATE")
        self.assertEqual(course["syllabus_check"]["coverage_pct"], 88)

    def test_verdict_is_surfaced_to_the_status_stream(self):
        _, b = self._run({"verdict": "INADEQUATE", "coverage_pct": 40,
                          "topics_checked": 10, "missing": ["Instrumental variables"]})
        sent = " ".join(str(c) for c in b.status_callback.call_args_list)
        self.assertIn("CHECK:SYLLABUS:INADEQUATE:40%", sent)

    def test_missing_topics_are_named_in_the_log(self):
        with self.assertLogs("services.core.course_builder", level="WARNING") as log:
            self._run({"verdict": "INADEQUATE", "coverage_pct": 40,
                       "topics_checked": 10,
                       "missing": ["Instrumental variables", "Diff-in-diff"]})
        joined = " ".join(log.output)
        self.assertIn("Instrumental variables", joined,
                      "a percentage is not actionable; the absent topics are")


class TestJudgeOutageDoesNotFailTheBuild(unittest.TestCase):
    """An instrument that can take down course generation will be turned off."""

    def test_error_result_records_and_continues(self):
        course = _structure()
        b = _builder()
        with patch.object(sc, "check_structure",
                          return_value={"error": "coverage judge unavailable"}):
            b._record_syllabus_check(course)      # must not raise
        self.assertIn("error", course["syllabus_check"])
        sent = " ".join(str(c) for c in b.status_callback.call_args_list)
        self.assertIn("CHECK:SYLLABUS:SKIP", sent)

    def test_import_failure_is_swallowed(self):
        course = _structure()
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def boom(name, *a, **kw):
            if name == "tools.syllabus_check":
                raise ImportError("no tools package in this container")
            return real_import(name, *a, **kw)

        with patch("builtins.__import__", side_effect=boom):
            _builder()._record_syllabus_check(course)  # must not raise
        self.assertNotIn("syllabus_check", course)


class TestBlockingIsOptIn(unittest.TestCase):
    """The percentage is a documented LOWER BOUND — a 9B judge scores a
    complete outline at ~71%. Failing builds on a lower bound rejects good
    courses, so blocking is opt-in until a larger judge tightens the number."""

    BAD = {"verdict": "INADEQUATE", "coverage_pct": 30, "topics_checked": 10,
           "missing": ["Instrumental variables"]}

    def test_inadequate_does_not_block_by_default(self):
        with patch.object(sc, "check_structure", return_value=self.BAD), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HELGA_SYLLABUS_GATE", None)
            _builder()._record_syllabus_check(_structure())  # must not raise

    def test_inadequate_blocks_when_operator_opts_in(self):
        with patch.object(sc, "check_structure", return_value=self.BAD), \
             patch.dict(os.environ, {"HELGA_SYLLABUS_GATE": "1"}):
            with self.assertRaises(CourseCreationError) as ctx:
                _builder()._record_syllabus_check(_structure())
        self.assertIn("Instrumental variables", str(ctx.exception))

    def test_adequate_never_blocks_even_when_gating(self):
        good = {"verdict": "ADEQUATE", "coverage_pct": 90, "topics_checked": 10,
                "missing": []}
        with patch.object(sc, "check_structure", return_value=good), \
             patch.dict(os.environ, {"HELGA_SYLLABUS_GATE": "1"}):
            _builder()._record_syllabus_check(_structure())

    def test_check_can_be_disabled_entirely(self):
        course = _structure()
        with patch.object(sc, "check_structure") as m, \
             patch.dict(os.environ, {"HELGA_SYLLABUS_CHECK": "0"}):
            _builder()._record_syllabus_check(course)
        m.assert_not_called()
        self.assertNotIn("syllabus_check", course)


class TestItRunsBeforeHydration(unittest.TestCase):
    """Running it post-hydration would find an outline defect only after ~40
    minutes of generation had already been spent on that outline."""

    def test_check_precedes_persist_in_build(self):
        import inspect
        src = inspect.getsource(SkeletonBuilder._build_inner)
        self.assertLess(
            src.index("_record_syllabus_check"),
            src.index("create_course"),
            "the check must run on the skeleton, before the course is written"
        )


if __name__ == "__main__":
    unittest.main()
