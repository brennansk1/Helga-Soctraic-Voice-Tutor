"""B17.7 (affect handling), B22.4 (cosmetics), B27.3 (xAPI), B27.4 (usage
tracking) tests."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), 'services/core'))

import logging
logging.basicConfig = MagicMock()
logging.FileHandler = MagicMock()

import pytest

from services.common.storage import StorageManager
from services.common.xapi import statements_for_student

core_deps = {'yaml': MagicMock(), 'kuzu': MagicMock(), 'libzim': MagicMock()}
with patch.dict('sys.modules', core_deps):
    from fsm_logic import MnemosyneFSM
import usage_tracker


@pytest.fixture
def storage(tmp_path):
    return StorageManager(str(tmp_path))


@pytest.fixture
def student(storage):
    pid = storage.accounts.create_parent("aca@example.com", "h")
    return storage.accounts.create_student(pid, "Kid", grade_band="K-2",
                                           interests=["space", "soccer"])


# ---------------------------------------------------------------- B17.7

def _fsm(band="K-2"):
    with patch.object(MnemosyneFSM, '__init__', lambda self, *a, **kw: None):
        fsm = MnemosyneFSM.__new__(MnemosyneFSM)
    fsm.grade_band = band
    fsm.student_id = "stu_affect01"
    fsm.concept_miss_streak = 0
    fsm.concept_correct_streak = 0
    fsm.concept_question_count = 0
    fsm.current_bloom_level = 3
    fsm.bloom_correct_streak = 0
    fsm.course_bloom_floor = 1
    fsm.course_bloom_ceiling = 3
    fsm.passed_question_types = set()
    fsm.socratic_type_index = 0
    return fsm


class TestAffectHandling(unittest.TestCase):
    def _affect_note(self, fsm):
        """Run just the affect block the way ask_socratic_question does."""
        system_note = None
        affect_note = None
        if (fsm.grade_band in ("K-2", "3-5")
                and getattr(fsm, "concept_miss_streak", 0) >= 2):
            floor = fsm.course_bloom_floor or 1
            if fsm.current_bloom_level > floor:
                fsm.current_bloom_level -= 1
                fsm.bloom_correct_streak = 0
            affect_note = "AFFECT NOTE"
        if affect_note:
            system_note = affect_note
        return system_note

    def test_k2_two_misses_triggers_scaffold_and_eases_bloom(self):
        fsm = _fsm("K-2")
        fsm.concept_miss_streak = 2
        note = self._affect_note(fsm)
        assert note is not None
        assert fsm.current_bloom_level == 2   # eased toward the floor

    def test_912_never_triggers(self):
        fsm = _fsm("9-12")
        fsm.concept_miss_streak = 5
        assert self._affect_note(fsm) is None
        assert fsm.current_bloom_level == 3

    def test_single_miss_no_trigger(self):
        fsm = _fsm("3-5")
        fsm.concept_miss_streak = 1
        assert self._affect_note(fsm) is None

    def test_bloom_never_drops_below_floor(self):
        fsm = _fsm("K-2")
        fsm.current_bloom_level = 1
        fsm.concept_miss_streak = 4
        self._affect_note(fsm)
        assert fsm.current_bloom_level == 1

    def test_affect_note_text_in_fsm_source(self):
        """The real prompt text warns against pressing harder / disappointment."""
        src = open(os.path.join(os.getcwd(), 'services/core/fsm_logic.py')).read()
        assert "AFFECT NOTE" in src
        assert "never sound disappointed" in src


# ---------------------------------------------------------------- B22.4

class TestCosmetics:
    def test_interest_themed_first_and_level_gated(self, storage, student):
        state = storage.gamification.cosmetics_for(student_id=student)
        assert state["level"] == 1
        unlocked_ids = [c["id"] for c in state["unlocked"]]
        assert unlocked_ids == ["cos_star"]   # only the level-1 item
        # locked list: themed (space/soccer) items sort before untheme ones
        assert state["locked"][0]["themed"] is True

    def test_equip_unlocked_only(self, storage, student):
        assert storage.gamification.equip_cosmetic("cos_star", student_id=student)
        assert not storage.gamification.equip_cosmetic("cos_crown", student_id=student)
        assert not storage.gamification.equip_cosmetic("cos_nope", student_id=student)
        state = storage.gamification.cosmetics_for(student_id=student)
        assert state["equipped"] == "cos_star"

    def test_level_up_unlocks_themed_items(self, storage, student):
        for _ in range(10):   # 100 XP → level 2
            storage.gamification.award_xp("answer", grade=3, student_id=student)
        state = storage.gamification.cosmetics_for(student_id=student)
        ids = {c["id"] for c in state["unlocked"]}
        assert {"cos_rocket", "cos_ball"} <= ids   # both interests unlocked


# ---------------------------------------------------------------- B27.3

class TestXapi:
    def test_statements_shape_and_privacy(self, storage, student):
        storage.activity.log_activity("course_x", "socratic_answer",
                                      concept_uid="con_1", grade=3,
                                      duration_seconds=30, student_id=student)
        stmts = statements_for_student(storage, student)
        assert len(stmts) == 1
        s = stmts[0]
        assert s["verb"]["id"].endswith("/answered")
        assert s["result"]["success"] is True
        assert s["result"]["score"]["raw"] == 3
        # privacy: actor is the opaque id, no name/email anywhere
        assert s["actor"]["account"]["name"] == student
        assert "Kid" not in str(s)

    def test_exam_attempts_included(self, storage, student):
        eid = storage.exams.create_exam("checkpoint", {"slots": []})
        aid = storage.exams.create_attempt(eid, student_id=student)
        storage.exams.update_attempt(aid, student_id=student, status="graded",
                                     score=0.9, passed=1,
                                     submitted_at="2026-07-02T10:00:00")
        stmts = statements_for_student(storage, student)
        exam_stmts = [s for s in stmts if s["verb"]["display"]["en-US"] == "exam"]
        assert exam_stmts and exam_stmts[0]["result"]["score"]["scaled"] == 0.9


# ---------------------------------------------------------------- B27.4

class TestUsageTracker(unittest.TestCase):
    def setUp(self):
        usage_tracker.reset()

    def test_per_student_accumulation(self):
        usage_tracker.record("stu_a", 100, 50, 1.5)
        usage_tracker.record("stu_a", 200, 80, 2.0)
        usage_tracker.record("stu_b", 10, 5, 0.1)
        snap = usage_tracker.snapshot()
        assert snap["stu_a"]["calls"] == 2
        assert snap["stu_a"]["completion_tokens"] == 130
        assert abs(snap["stu_a"]["gpu_seconds"] - 3.5) < 1e-9
        t = usage_tracker.totals()
        assert t["students"] == 2 and t["prompt_tokens"] == 310

    def test_anon_bucket(self):
        usage_tracker.record(None, 5, 5, 0.5)
        assert "_anon" in usage_tracker.snapshot()


if __name__ == '__main__':
    unittest.main()
