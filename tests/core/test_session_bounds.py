"""A lesson must end for every learner, not only for progressing ones.

Measured 2026-08-18: adult sessions ran to a 25-turn cap on a SINGLE concept and
never completed, because completion needs a streak of grade >= 3 and a stalled
learner never builds one. The tutor was right to decline advancing them; nothing
bounded the session.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
os.environ.setdefault("DATA_ROOT", "/tmp/helga_test_data")
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core import fsm_logic  # noqa: E402
from services.core.fsm_logic import MnemosyneFSM  # noqa: E402


def _fsm(questions, streak=0):
    f = MnemosyneFSM.__new__(MnemosyneFSM)
    f.concept_question_count = questions
    f.concept_correct_streak = streak
    return f


class TestTurnCap(unittest.TestCase):
    def test_under_the_cap_does_not_park(self):
        assert _fsm(fsm_logic.CONCEPT_TURN_CAP - 1)._should_park_concept() is False

    def test_at_the_cap_parks(self):
        assert _fsm(fsm_logic.CONCEPT_TURN_CAP)._should_park_concept() is True

    def test_parking_is_not_mastery(self):
        """The cap must never satisfy the mastery gate. Crediting mastery nobody
        demonstrated is the same error the fallback grade avoids by never being
        a passing grade — parking is the opposite move."""
        import inspect
        src = inspect.getsource(MnemosyneFSM._check_mastery_gate)
        assert "CONCEPT_TURN_CAP" not in src, \
            "the turn cap leaked into the mastery gate"
        assert "_should_park_concept" not in src


class TestEscalationThresholds(unittest.TestCase):
    def test_ease_comes_before_the_offer_to_move_on(self):
        """Change the explanation first; offer the exit only after that failed."""
        assert fsm_logic.ADULT_EASE_AFTER < fsm_logic.ADULT_OFFER_PARK_AFTER

    def test_the_exit_is_offered_before_the_hard_cap(self):
        """The learner should be given the choice before the system takes it."""
        assert fsm_logic.ADULT_OFFER_PARK_AFTER < fsm_logic.CONCEPT_TURN_CAP

    def test_cap_is_within_one_lesson(self):
        """A lesson is ~3 concepts in a 50-minute session; a cap of 20 questions
        on ONE concept is already generous."""
        assert 8 <= fsm_logic.CONCEPT_TURN_CAP <= 25


if __name__ == "__main__":
    unittest.main()
