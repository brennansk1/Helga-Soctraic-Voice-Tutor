"""HelgaBench's scoring layer must not manufacture scores it was never given.

WHY THIS EXISTS
---------------
HelgaBench reported misconception_handling = 1.6/5 while four independent
probes put the same behaviour at 94-100%. The tutor was not the problem; the
instrument was. Three separate defects stacked:

  1. A missing or null key was read as `int(data.get(d, 0))` and clamped to 1 --
     the worst possible score, manufactured out of silence.
  2. The rubric had no way to say "the student made no error", so a dialogue
     with nothing to correct scored 1, identical to praising a bluff.
  3. One judge call swung +/-2 on an IDENTICAL transcript (measured 5,3,3,5),
     so any single-sample score was noise.

These tests pin all three. They mock the model, so they assert the scoring
logic rather than the judge's taste -- the judge's taste is checked separately
by `helgabench.py --self-test`, which needs a live model.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

import tools.helgabench as hb  # noqa: E402

TOPIC = hb.TOPICS[0]
TRANSCRIPT = [
    {"role": "tutor", "text": "What could explain the association?"},
    {"role": "student", "text": "A confounder sits on the causal path."},
    {"role": "tutor", "text": "That describes a mediator, not a confounder."},
]


def _rubric(**kw):
    """A main-rubric reply. misconception_handling is deliberately absent --
    it is no longer sourced from this blob."""
    base = {"socratic": 4, "adaptation": 4, "accuracy": 5, "progression": 4,
            "worst_moment": "none"}
    base.update(kw)
    return json.dumps(base)


class TestMissingKeysAreNotScoredZero(unittest.TestCase):

    def test_absent_dimension_is_none_not_one(self):
        with patch.object(hb, "_chat", return_value=_rubric(socratic=None)):
            out = hb.judge(None, "misconception_holder", TOPIC, TRANSCRIPT,
                           samples_n=1)
        self.assertIsNone(
            out["socratic"],
            "A key the judge never returned must be None. Scoring it 1 invents "
            "the worst possible verdict from silence and drags every average "
            "toward the floor."
        )

    def test_absent_dimension_is_excluded_from_the_mean(self):
        with patch.object(hb, "_chat", return_value=_rubric(socratic=None)):
            out = hb.judge(None, "misconception_holder", TOPIC, TRANSCRIPT,
                           samples_n=1)
        vals = [out[d] for d in hb.DIMENSIONS if out.get(d)]
        self.assertNotIn(1, vals)

    def test_non_numeric_score_is_none(self):
        with patch.object(hb, "_chat", return_value=_rubric(accuracy="good")):
            out = hb.judge(None, "misconception_holder", TOPIC, TRANSCRIPT,
                           samples_n=1)
        self.assertIsNone(out["accuracy"])

    def test_unparseable_judge_reply_errors_rather_than_scoring_one(self):
        with patch.object(hb, "_chat", return_value="I cannot comply."):
            out = hb.judge(None, "misconception_holder", TOPIC, TRANSCRIPT,
                           samples_n=1)
        self.assertIn("error", out)
        self.assertNotIn(1, [out.get(d) for d in hb.DIMENSIONS])


class TestJudgeNoiseIsAveragedOut(unittest.TestCase):
    """A single judge call is not a measurement."""

    def test_median_over_samples(self):
        replies = [_rubric(socratic=5), _rubric(socratic=3),
                   _rubric(socratic=3)]
        with patch.object(hb, "_chat", side_effect=replies * 4):
            out = hb.judge(None, "misconception_holder", TOPIC, TRANSCRIPT,
                           samples_n=3)
        self.assertEqual(out["socratic"], 3)

    def test_one_unparseable_sample_does_not_poison_the_rest(self):
        replies = [_rubric(socratic=4), "garbage", _rubric(socratic=4)]
        with patch.object(hb, "_chat", side_effect=replies * 4):
            out = hb.judge(None, "misconception_holder", TOPIC, TRANSCRIPT,
                           samples_n=3)
        self.assertEqual(out["socratic"], 4)
        self.assertEqual(out["_judge_samples"], 2)


class TestMisconceptionHandlingIsJudgedSeparately(unittest.TestCase):
    """Asking for this inside the five-key blob does not survive a 9B judge:
    it scored a tutor that PRAISED a misconception as null while correctly
    describing the failure in worst_moment. Split into two calls, with the
    not-scoreable decision made in code rather than by the model."""

    def _run(self, erred, quote="a confounder sits on the causal path",
             score=5):
        def fake(_client, system, _user, **kw):
            if system is hb.ERRED_PROMPT:
                return json.dumps({"student_erred": erred, "quote": quote})
            if system.startswith("A student made an error"):
                return json.dumps({"score": score, "why": "corrected it"})
            return _rubric()
        with patch.object(hb, "_chat", side_effect=fake):
            return hb.judge(None, "misconception_holder", TOPIC, TRANSCRIPT,
                            samples_n=1)

    def test_no_student_error_is_not_scoreable(self):
        out = self._run(erred=False)
        self.assertIsNone(
            out["misconception_handling"],
            "With nothing to correct there is nothing to score. Scoring 1 "
            "punishes the tutor for the student being right -- that is how a "
            "confused_beginner dialogue with no misconception in it scored 1."
        )
        self.assertIn("not scoreable", out["_mh_note"])

    def test_not_scoreable_is_excluded_from_the_mean(self):
        out = self._run(erred=False)
        vals = [out[d] for d in hb.DIMENSIONS if out.get(d)]
        self.assertEqual(len(vals), 4, "the unscoreable dimension must drop out")

    def test_student_error_is_scored(self):
        out = self._run(erred=True, score=5)
        self.assertEqual(out["misconception_handling"], 5)

    def test_handling_score_is_a_median_of_samples(self):
        calls = {"n": 0}

        def fake(_client, system, _user, **kw):
            if system is hb.ERRED_PROMPT:
                return json.dumps({"student_erred": True, "quote": "wrong"})
            if system.startswith("A student made an error"):
                calls["n"] += 1
                return json.dumps({"score": [1, 5, 5][calls["n"] - 1]})
            return _rubric()
        with patch.object(hb, "_chat", side_effect=fake):
            out = hb.judge(None, "misconception_holder", TOPIC, TRANSCRIPT,
                           samples_n=3)
        self.assertEqual(out["misconception_handling"], 5)

    def test_erred_check_looks_only_at_the_student(self):
        self.assertIn("ONLY at the STUDENT", hb.ERRED_PROMPT)
        self.assertIn("Ignore the tutor", hb.ERRED_PROMPT)


class TestRubricKeepsScopeSeparate(unittest.TestCase):

    def test_main_rubric_no_longer_asks_for_misconception_handling(self):
        self.assertNotIn(
            "misconception_handling", hb.JUDGE_RUBRIC,
            "It is sourced from the two-call sub-judge; leaving it in the blob "
            "reintroduces the null-versus-1 collapse."
        )

    def test_handling_prompt_states_correction_is_desired(self):
        self.assertIn("DESIRED behaviour", hb.HANDLING_PROMPT)

    def test_handling_prompt_excludes_off_dimension_penalties(self):
        self.assertIn("changed topic", hb.HANDLING_PROMPT)


class TestTrajectoryCollapseMetric(unittest.TestCase):
    def test_collapse_metric_scores_whole_dialogue_independent_of_turn_scores(self):
        # A dialogue where every turn passes individually can still be flagged as collapsed
        def fake(_client, system, _user, **kw):
            if system == hb.TRAJECTORY_PROMPT:
                return json.dumps({"collapse": True, "reason": "Tutor pivoted to lecturing."})
            if system == hb.ERRED_PROMPT:
                return json.dumps({"student_erred": False})
            if system.startswith("A student made an error"):
                return json.dumps({"score": 5, "why": "Corrected."})
            return _rubric(socratic=5, adaptation=5, accuracy=5, progression=5)
            
        with patch.object(hb, "_chat", side_effect=fake):
            out = hb.judge(None, "confused_beginner", TOPIC, TRANSCRIPT, samples_n=1)
            
        # The existing 5 dimensions can all be perfect (5)
        self.assertEqual(out["socratic"], 5)
        self.assertEqual(out["progression"], 5)
        # But the trajectory metric can still flag a collapse
        self.assertTrue(out["trajectory_collapse"])
        self.assertEqual(out["_collapse_reason"], "Tutor pivoted to lecturing.")


if __name__ == "__main__":
    unittest.main()
