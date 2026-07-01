"""B18.1–B18.5 tests: exam generation, theming + validity guard, grading,
scoring/thresholds, gating, anti-gaming (spec 05 §10.1 acceptance criteria).

The LLM boundary is a scripted fake; everything else is real (v6 tables,
ExamStore, standards layer, progress/enrollments).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.storage import StorageManager
from services.exam.exam_engine import (
    ExamEngine, ITEM_SCHEMA, VERIFY_SCHEMA, structural_ok, validate_item_shape,
    grade_objective, score_attempt, civics_display, strip_answer_key,
)


BASE_MCQ = {
    "item_type": "mcq",
    "prompt": "A recipe needs 3 cups flour to 2 cups sugar. What is the ratio of flour to sugar?",
    "choices": ["3:2", "2:3", "5:1", "1:5"],
    "answer_index": 0,
    "standard_code": "6.RP", "bloom": 2, "difficulty": "medium",
}

THEMED_MCQ = {
    "item_type": "mcq",
    "prompt": "A striker scored 3 goals for every 2 assists. What is the ratio of goals to assists?",
    "choices": ["3:2", "2:3", "5:1", "1:5"],
    "answer_index": 0,
    "standard_code": "6.RP", "bloom": 2, "difficulty": "medium",
}


class ScriptedLLM:
    """Returns queued responses in order; records prompts."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if self.responses:
            return self.responses.pop(0)
        # default: approve verification, echo a valid mcq for generation
        if schema is VERIFY_SCHEMA:
            return {"same_standard": True, "same_bloom": True,
                    "same_answer": True, "same_difficulty": True, "reason": "ok"}
        return dict(BASE_MCQ)


@pytest.fixture
def storage(tmp_path):
    st = StorageManager(str(tmp_path))
    st.standards.upsert("6.RP", "math", "Ratios & Proportional Relationships",
                        "Understand ratio concepts and use ratio reasoning.",
                        grade_band="6-8")
    st.standards.upsert("6.NS", "math", "The Number System",
                        "Compute with rational numbers.", grade_band="6-8")
    return st


@pytest.fixture
def student(storage):
    pid = storage.accounts.create_parent("exam@example.com", "h", status="active")
    return storage.accounts.create_student(pid, "Examinee", grade_band="6-8",
                                           interests=["soccer"])


def _course_with_units(storage, uid="course_exam0001"):
    course = {
        "uid": uid, "title": "Ratios", "status": "ready",
        "modules": [{"uid": "mod_1", "title": "M1", "units": [
            {"uid": "unit_1", "title": "U1", "lessons": [
                {"uid": "less_1", "title": "L1", "concepts": [
                    {"uid": "con_u1c1", "title": "Ratio Notation"},
                    {"uid": "con_u1c2", "title": "Unit Rate"}]}]},
            {"uid": "unit_2", "title": "U2", "lessons": [
                {"uid": "less_2", "title": "L2", "concepts": [
                    {"uid": "con_u2c1", "title": "Percent"}]}]},
        ]}],
    }
    storage.courses.create_course(course)
    return uid


# ------------------------------------------------------------ pure helpers

class TestValidityGuardStructural:
    def test_correct_reskin_passes(self):
        ok, reason = structural_ok(BASE_MCQ, THEMED_MCQ)
        assert ok, reason

    def test_number_change_fails(self):
        bad = dict(THEMED_MCQ, prompt="A striker scored 4 goals for every 2 assists. Ratio?")
        ok, reason = structural_ok(BASE_MCQ, bad)
        assert not ok and reason == "quantities changed"

    def test_answer_index_move_fails(self):
        bad = dict(THEMED_MCQ, answer_index=1)
        ok, reason = structural_ok(BASE_MCQ, bad)
        assert not ok and reason == "answer index moved"

    def test_standard_drift_fails(self):
        bad = dict(THEMED_MCQ, standard_code="6.NS")
        ok, reason = structural_ok(BASE_MCQ, bad)
        assert not ok and reason == "standard drift"

    def test_numeric_answer_change_fails(self):
        base = {"item_type": "numeric", "prompt": "3 for 2 — unit rate?",
                "answer_value": 1.5, "standard_code": "6.RP", "bloom": 3,
                "difficulty": "medium"}
        bad = dict(base, answer_value=2.0)
        ok, reason = structural_ok(base, bad)
        assert not ok and reason == "numeric answer changed"


class TestItemShape:
    def test_valid_types(self):
        assert validate_item_shape(BASE_MCQ)[0]
        assert validate_item_shape({"item_type": "free", "prompt": "Explain.",
                                    "rubric": "Correct idea."})[0]

    def test_mcq_needs_choices(self):
        bad = dict(BASE_MCQ, choices=["only", "two"])
        assert not validate_item_shape(bad)[0]

    def test_mcq_index_bounds(self):
        bad = dict(BASE_MCQ, answer_index=9)
        assert not validate_item_shape(bad)[0]


class TestObjectiveGrading:
    def test_mcq(self):
        assert grade_objective(BASE_MCQ, {"answer_index": 0}, {"response_index": 0}) == (1, 1.0)
        assert grade_objective(BASE_MCQ, {"answer_index": 0}, {"response_index": 2}) == (0, 0.0)

    def test_numeric_tolerance_and_unit(self):
        item = {"item_type": "numeric"}
        key = {"answer_value": 1.5, "tolerance": 0.01, "unit": "cups"}
        assert grade_objective(item, key, {"response_value": 1.505, "unit": "cups"}) == (1, 1.0)
        assert grade_objective(item, key, {"response_value": 1.6, "unit": "cups"}) == (0, 0.0)
        assert grade_objective(item, key, {"response_value": 1.5, "unit": "liters"}) == (0, 0.0)
        assert grade_objective(item, key, {"response_value": "not-a-number"}) == (0, 0.0)

    def test_ordering_exact(self):
        item = {"item_type": "ordering"}
        key = {"ordering": ["a", "b", "c"]}
        assert grade_objective(item, key, {"response_order": ["a", "b", "c"]}) == (1, 1.0)
        assert grade_objective(item, key, {"response_order": ["b", "a", "c"]}) == (0, 0.0)

    def test_free_returns_none(self):
        assert grade_objective({"item_type": "free"}, {}, {}) is None


class TestScoring:
    def test_score_and_subscores(self):
        items = [
            {"item_type": "mcq", "is_correct": 1, "standard_code": "6.RP", "grade": None},
            {"item_type": "mcq", "is_correct": 0, "standard_code": "6.RP", "grade": None},
            {"item_type": "free", "is_correct": 1, "grade": 3, "standard_code": "6.NS"},
        ]
        score, subs = score_attempt(items)
        assert abs(score - (1 + 0 + 0.85) / 3) < 1e-9
        assert abs(subs["6.RP"] - 0.5) < 1e-9
        assert abs(subs["6.NS"] - 0.85) < 1e-9

    def test_civics_display(self):
        assert civics_display(0.70) == "35 / 50"
        assert civics_display(0.68) == "34 / 50"

    def test_strip_answer_key(self):
        view = strip_answer_key(BASE_MCQ)
        assert "answer_index" not in view and "choices" in view


# ------------------------------------------------------------- end-to-end

class TestAttemptFlow:
    def _engine(self, storage, responses=None):
        return ExamEngine(storage, ScriptedLLM(responses))

    def _blueprint(self, n_mcq=2):
        return {"version": 1, "shuffle": False, "slots": [
            {"standard_code": "6.RP", "bloom": 2, "item_type": "mcq", "count": n_mcq}]}

    def test_generation_yields_blueprint_items(self, storage, student):
        eng = self._engine(storage, [dict(BASE_MCQ), None,  # theme fails → base served
                                     dict(BASE_MCQ), None])
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(2),
                                            course_uid="course_x", scope_uid="unit_1")
        view, err = eng.start_attempt(exam_id, student)
        assert err is None and view["n_items"] == 2 and view["theme"] == "soccer"
        aid = view["attempt_id"]
        first, _ = eng.next_item(aid, student)
        assert first["item"]["item_type"] == "mcq"
        assert "answer_index" not in first["item"], "answer key leaked!"
        second, _ = eng.next_item(aid, student)
        assert second["item_index"] == 1
        done, _ = eng.next_item(aid, student)
        assert done == {"done": True}

    def test_theming_applies_when_valid(self, storage, student):
        # generation → themed → verifier approves
        eng = self._engine(storage, [
            dict(BASE_MCQ), dict(THEMED_MCQ),
            {"same_standard": True, "same_bloom": True, "same_answer": True,
             "same_difficulty": True, "reason": "ok"},
        ])
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(1))
        view, _ = eng.start_attempt(exam_id, student)
        item, _ = eng.next_item(view["attempt_id"], student)
        assert item["item"]["theme_validated"] is True
        assert "striker" in item["item"]["prompt"]
        # answer key preserved server-side
        row = storage.exams.get_item(item["item"]["response_id"])
        assert row["correct"]["answer_index"] in range(4)

    def test_bad_theme_falls_back_to_base(self, storage, student):
        bad_theme = dict(THEMED_MCQ, prompt="A striker scored 4 goals for every 2 assists. Ratio?")
        eng = self._engine(storage, [dict(BASE_MCQ), bad_theme])
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(1))
        view, _ = eng.start_attempt(exam_id, student)
        item, _ = eng.next_item(view["attempt_id"], student)
        assert item["item"]["theme_validated"] is False
        assert "recipe" in item["item"]["prompt"]     # base item served

    def test_full_pass_unlocks_next_unit(self, storage, student):
        course_uid = _course_with_units(storage)
        storage.standards.sync_concept_standards(
            [], [{"concept_uid": "con_u1c1", "standard_code": "6.RP"}])
        eng = self._engine(storage, [dict(BASE_MCQ), None, dict(BASE_MCQ), None])
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(2),
                                            course_uid=course_uid, scope_uid="unit_1")
        view, _ = eng.start_attempt(exam_id, student, course_uid=course_uid)
        aid = view["attempt_id"]
        for _ in range(2):
            item, _ = eng.next_item(aid, student)
            row = storage.exams.get_item(item["item"]["response_id"])
            eng.record_response(aid, item["item"]["response_id"],
                                {"response_index": row["correct"]["answer_index"]}, student)
        result, err = eng.submit_attempt(aid, student)
        assert err is None and result["passed"] is True
        assert result["gating"]["unlocked_next_unit"] is True
        enr = storage.enrollments.get(student, course_uid)
        assert enr["current_concept_uid"] == "con_u2c1"
        prog = storage.progress.get_progress("con_u1c1", student_id=student)
        assert prog and prog["status"] == "completed"

    def test_fail_returns_remediation_not_unlock(self, storage, student):
        course_uid = _course_with_units(storage)
        storage.standards.sync_concept_standards(
            [], [{"concept_uid": "con_u1c1", "standard_code": "6.RP"},
                 {"concept_uid": "con_u1c2", "standard_code": "6.RP"}])
        eng = self._engine(storage, [dict(BASE_MCQ), None, dict(BASE_MCQ), None])
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(2),
                                            course_uid=course_uid, scope_uid="unit_1")
        view, _ = eng.start_attempt(exam_id, student, course_uid=course_uid)
        aid = view["attempt_id"]
        for _ in range(2):
            item, _ = eng.next_item(aid, student)
            row = storage.exams.get_item(item["item"]["response_id"])
            wrong = (row["correct"]["answer_index"] + 1) % 4
            eng.record_response(aid, item["item"]["response_id"],
                                {"response_index": wrong}, student)
        result, _ = eng.submit_attempt(aid, student)
        assert result["passed"] is False
        assert result["gating"]["unlocked_next_unit"] is False
        assert result["gating"]["remediation_concepts"] == ["con_u1c1", "con_u1c2"]
        assert storage.enrollments.get(student, course_uid) is None
        assert result["missed_standards"] == ["6.RP"]

    def test_no_correctness_leak_pre_grade(self, storage, student):
        eng = self._engine(storage, [dict(BASE_MCQ), None])
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(1))
        view, _ = eng.start_attempt(exam_id, student)
        item, _ = eng.next_item(view["attempt_id"], student)
        res, _ = eng.record_response(view["attempt_id"], item["item"]["response_id"],
                                     {"response_index": 0}, student)
        assert set(res.keys()) == {"recorded", "answered", "n_items"}

    def test_submit_idempotent(self, storage, student):
        eng = self._engine(storage, [dict(BASE_MCQ), None])
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(1))
        view, _ = eng.start_attempt(exam_id, student)
        item, _ = eng.next_item(view["attempt_id"], student)
        eng.record_response(view["attempt_id"], item["item"]["response_id"],
                            {"response_index": 0}, student)
        r1, _ = eng.submit_attempt(view["attempt_id"], student)
        r2, _ = eng.submit_attempt(view["attempt_id"], student)
        assert r1["score"] == r2["score"] and r2["status"] == "graded"

    def test_start_returns_existing_in_progress(self, storage, student):
        eng = self._engine(storage)
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(1))
        v1, _ = eng.start_attempt(exam_id, student)
        v2, _ = eng.start_attempt(exam_id, student)
        assert v1["attempt_id"] == v2["attempt_id"]

    def test_attempt_limit_429(self, storage, student):
        eng = self._engine(storage, [dict(BASE_MCQ), None] * 6)
        exam_id = storage.exams.create_exam("checkpoint", self._blueprint(1))
        for _ in range(3):
            view, err = eng.start_attempt(exam_id, student)
            assert err is None
            item, _ = eng.next_item(view["attempt_id"], student)
            eng.record_response(view["attempt_id"], item["item"]["response_id"],
                                {"response_index": 0}, student)
            eng.submit_attempt(view["attempt_id"], student)
        _, err = eng.start_attempt(exam_id, student)
        assert err and err[1] == 429

    def test_free_item_graded_by_socratic_schema(self, storage, student):
        free_item = {"item_type": "free", "prompt": "Explain a ratio.",
                     "rubric": "Defines ratio as a comparison.",
                     "exemplar": "A ratio compares two quantities.",
                     "standard_code": "6.RP", "bloom": 2, "difficulty": "medium"}
        eng = self._engine(storage, [
            dict(free_item), None,               # generate; theme fails
            {"grade": 3, "feedback": "Good reasoning about comparison."},
        ])
        bp = {"version": 1, "slots": [
            {"standard_code": "6.RP", "bloom": 2, "item_type": "free", "count": 1}]}
        exam_id = storage.exams.create_exam("checkpoint", bp)
        view, _ = eng.start_attempt(exam_id, student)
        item, _ = eng.next_item(view["attempt_id"], student)
        eng.record_response(view["attempt_id"], item["item"]["response_id"],
                            {"response_text": "It compares two amounts."}, student)
        result, _ = eng.submit_attempt(view["attempt_id"], student)
        assert result["score"] == 0.85 and result["passed"] is True

    def test_free_grading_failure_never_silently_passes(self, storage, student):
        free_item = {"item_type": "free", "prompt": "Explain.", "rubric": "R",
                     "standard_code": "6.RP", "bloom": 2, "difficulty": "medium"}
        eng = self._engine(storage, [dict(free_item), None, None])  # grader returns None
        bp = {"version": 1, "slots": [
            {"standard_code": "6.RP", "bloom": 2, "item_type": "free", "count": 1}]}
        exam_id = storage.exams.create_exam("checkpoint", bp)
        view, _ = eng.start_attempt(exam_id, student)
        item, _ = eng.next_item(view["attempt_id"], student)
        eng.record_response(view["attempt_id"], item["item"]["response_id"],
                            {"response_text": "x"}, student)
        result, _ = eng.submit_attempt(view["attempt_id"], student)
        assert result["score"] == 0.5 and result["passed"] is False  # grade-2 floor


class TestUtahThresholds:
    def _run_prep(self, storage, student, threshold, n_correct, n_items):
        eng = ExamEngine(storage, ScriptedLLM(
            [dict(BASE_MCQ), None] * n_items))
        bp = {"version": 1, "slots": [
            {"standard_code": "6.RP", "bloom": 2, "item_type": "mcq", "count": n_items}]}
        exam_id = storage.exams.create_exam("standardized_prep", bp,
                                            pass_threshold=threshold)
        view, _ = eng.start_attempt(exam_id, student)
        aid = view["attempt_id"]
        for i in range(n_items):
            item, _ = eng.next_item(aid, student)
            row = storage.exams.get_item(item["item"]["response_id"])
            right = row["correct"]["answer_index"]
            answer = right if i < n_correct else (right + 1) % 4
            eng.record_response(aid, item["item"]["response_id"],
                                {"response_index": answer}, student)
        result, _ = eng.submit_attempt(aid, student)
        return result

    def test_gfl_74_cut(self, storage, student):
        result = self._run_prep(storage, student, 0.74, 37, 50)
        assert result["passed"] is True      # 0.74 exactly
        assert result["gating"]["unlocked_next_unit"] is False  # non-gating

    def test_gfl_below_cut_fails(self, storage, student):
        result = self._run_prep(storage, student, 0.74, 36, 50)
        assert result["passed"] is False

    def test_civics_35_of_50(self, storage, student):
        result = self._run_prep(storage, student, 0.70, 35, 50)
        assert result["passed"] is True
        assert result["display"]["civics"] == "35 / 50"

    def test_civics_34_fails(self, storage, student):
        result = self._run_prep(storage, student, 0.70, 34, 50)
        assert result["passed"] is False
        assert result["display"]["civics"] == "34 / 50"
