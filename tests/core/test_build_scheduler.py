"""When the next course gets built, at what priority, and yielding to whom.

The constraint that shapes all of it: this box serves one model at a time, a
build is hundreds of LLM calls at a MEASURED 90 s per concept, and a tutoring
turn is one call at ~18 s. A build running during a session makes the learner
experience the university building itself as latency inside their own lesson.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core import build_scheduler as bs  # noqa: E402


def _state(**kw):
    base = {"progress": 0.5, "seconds_since_turn": 10_000,
            "next_course_chosen": True, "next_course_built": False,
            "builds_in_flight": 0, "build_paused": False}
    base.update(kw)
    return base


class TestSessionsAlwaysWin(unittest.TestCase):
    def test_an_active_session_blocks_every_build(self):
        d = bs.decide(_state(seconds_since_turn=5))
        assert d["action"] == "wait"
        assert d["priority"] == bs.PRIORITY_INTERACTIVE

    def test_an_active_session_blocks_even_a_paused_build(self):
        """Resuming is cheaper than restarting, but not at a learner's expense."""
        d = bs.decide(_state(seconds_since_turn=5, build_paused=True))
        assert d["action"] == "wait"

    def test_idle_long_enough_allows_building(self):
        d = bs.decide(_state(seconds_since_turn=bs.SESSION_IDLE_SECONDS + 1))
        assert d["action"] == "start_build"
        assert d["priority"] == bs.PRIORITY_BACKGROUND

    def test_never_having_had_a_turn_is_not_an_active_session(self):
        assert bs.decide(_state(seconds_since_turn=None))["action"] == "start_build"


class TestResumeBeforeRestart(unittest.TestCase):
    def test_a_paused_build_resumes(self):
        """A 3-hour build that restarts on every interruption never finishes for
        an active learner."""
        d = bs.decide(_state(build_paused=True))
        assert d["action"] == "resume_build"


class TestElectiveIsOnTheCriticalPath(unittest.TestCase):
    def test_too_early_to_ask(self):
        d = bs.decide(_state(progress=0.3, next_course_chosen=False))
        assert d["action"] == "wait"

    def test_asks_before_the_course_ends(self):
        """Asking at 100% leaves no study time to build inside."""
        d = bs.decide(_state(progress=bs.ELECTIVE_PROMPT_AT,
                             next_course_chosen=False))
        assert d["action"] == "prompt_elective"

    def test_unanswered_prompt_does_not_stall_the_pipeline(self):
        d = bs.decide(_state(progress=0.95, next_course_chosen=False))
        assert d["action"] == "autoselect_elective"
        assert "can still change it" in d["reason"]

    def test_the_prompt_comes_before_the_autoselect(self):
        assert bs.ELECTIVE_PROMPT_AT < bs.ELECTIVE_AUTOSELECT_AT < 1.0


class TestLookahead(unittest.TestCase):
    def test_never_builds_past_the_cap(self):
        """Building ahead spends hours on choices not yet made, and a learner who
        abandons has then wasted at most one build."""
        d = bs.decide(_state(builds_in_flight=bs.MAX_LOOKAHEAD))
        assert d["action"] == "wait"

    def test_lookahead_is_one(self):
        assert bs.MAX_LOOKAHEAD == 1

    def test_already_built_does_nothing(self):
        assert bs.decide(_state(next_course_built=True))["action"] == "wait"


class TestPassingACourseTriggers(unittest.TestCase):
    def test_completion_is_an_event_not_an_estimate(self):
        d = bs.on_course_passed(_state(progress=0.4))
        assert d["action"] == "start_build"

    def test_passing_still_yields_to_an_active_session(self):
        d = bs.on_course_passed(_state(progress=0.4, seconds_since_turn=5))
        assert d["action"] == "wait"


class TestUnbuiltCourseIsHonest(unittest.TestCase):
    def test_normal_case_reassures_without_lying(self):
        msg = bs.describe_unbuilt("Linear Algebra II")
        assert "still being prepared" in msg and "Linear Algebra II" in msg

    def test_degraded_build_is_disclosed(self):
        """Silently accepting a degraded build lets a throttling storm become
        permanent curriculum."""
        msg = bs.describe_unbuilt("Calculus II", degraded=True)
        assert "sources were unavailable" in msg and "rebuilt" in msg

    def test_repeated_failure_stops_pretending(self):
        msg = bs.describe_unbuilt("Topology", failures=3)
        assert "could not be prepared" in msg
        assert "shortly" not in msg


if __name__ == "__main__":
    unittest.main()
