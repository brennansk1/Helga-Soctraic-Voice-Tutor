"""Starting and resuming several courses.

The defect these cover: `RESUME_COURSE` saved the outgoing course and restored
the incoming one, and `SET_CONTEXT` — which is what the Learn tab actually
sends when a course is opened — did neither. It switched `active_course_uid`,
wiped transcript/history/queue/node/streaks, and moved on. So the correct path
was the one nobody took, and using two courses lost work from the first and
started the second from nothing.
"""
import os
import sys

import pytest

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "services/core"))
sys.path.append(os.path.join(os.getcwd(), "services/common"))


class _FsmStore:
    """The fsm_sessions single-row blob, in memory."""

    def __init__(self):
        self.rows = {}

    def get(self, student_id):
        return {"blob": self.rows.get(student_id)}

    def upsert(self, student_id, blob):
        self.rows[student_id] = blob


class _Courses:
    def get_course(self, uid):
        return {"uid": uid, "teaching_style": "", "title": uid}

    def get_concept_by_uid(self, course_uid, uid):
        return None

    def get_concept_content(self, *a):
        return ""


class _Storage:
    def __init__(self):
        self.fsm = _FsmStore()
        self.courses = _Courses()


def _fsm():
    from services.core.fsm_logic import MnemosyneFSM
    from services.common.turn_state import TurnState
    f = MnemosyneFSM.__new__(MnemosyneFSM)
    f.storage = _Storage()
    f.student_id = "student_1"
    f.state_file = "/nonexistent/user_state.json"
    f.active_course_uid = None
    f.current_lesson_node = None
    f.syllabus_queue = []
    f.completed_topics = set()
    f.transcript = []
    f.conversation_history = []
    f.socratic_type_index = 0
    f.concept_correct_streak = 0
    f.concept_miss_streak = 0
    f.concept_question_count = 0
    f.current_bloom_level = 1
    f.bloom_correct_streak = 0
    f._palace_index = 0
    f.current_locus_uid = None
    f.current_locus_desc = ""
    f.concept_bloom_target = None
    f.passed_question_types = set()
    f.prior_concepts_summary = []
    f.course_bloom_floor = 1
    f.course_bloom_ceiling = 6
    f.state = "SOCRATIC_LEARNING"
    f.grade_band = None
    f.turn_state = TurnState()
    return f


def _be_in(fsm, uid, concept, said):
    """Put the FSM mid-lesson in `uid`, then persist it."""
    fsm.active_course_uid = uid
    fsm.current_lesson_node = {"uid": f"con_{concept}", "title": concept}
    fsm.transcript = [{"sender": "user", "text": said}]
    fsm.conversation_history = [(said, "and what would that predict?")]
    fsm._save_current_course_progress()


def test_two_courses_are_stored_separately():
    fsm = _fsm()
    _be_in(fsm, "course_a", "Ohm's Law", "is it V over R?")
    _be_in(fsm, "course_b", "The July Crisis", "because of the alliances?")
    import json
    blob = json.loads(fsm.storage.fsm.rows["student_1"])
    assert set(blob["courses"]) == {"course_a", "course_b"}
    assert blob["courses"]["course_a"]["current_node"]["title"] == "Ohm's Law"
    assert blob["courses"]["course_b"]["current_node"]["title"] == "The July Crisis"


def test_saving_one_course_does_not_erase_another():
    """The blob is read-modify-written; a naive write would drop the rest."""
    fsm = _fsm()
    _be_in(fsm, "course_a", "Ohm's Law", "first")
    _be_in(fsm, "course_b", "The July Crisis", "second")
    _be_in(fsm, "course_a", "Kirchhoff", "third")
    import json
    blob = json.loads(fsm.storage.fsm.rows["student_1"])
    assert "course_b" in blob["courses"], "other course lost"
    assert blob["courses"]["course_b"]["current_node"]["title"] == "The July Crisis"


def test_switching_course_saves_the_one_being_left():
    """The core bug. SET_CONTEXT wiped the session without saving it."""
    fsm = _fsm()
    fsm.active_course_uid = "course_a"
    fsm.current_lesson_node = {"uid": "con_1", "title": "Ohm's Law"}
    fsm.transcript = [{"sender": "user", "text": "unsaved answer"}]

    fsm.transition({"type": "SET_CONTEXT",
                    "payload": {"course_uid": "course_b"}})

    import json
    blob = json.loads(fsm.storage.fsm.rows["student_1"])
    assert "course_a" in blob["courses"], "outgoing course never saved"
    said = blob["courses"]["course_a"]["transcript"]
    assert said and said[0]["text"] == "unsaved answer"


def test_switching_course_restores_the_one_being_entered():
    """The other half. Opening a started course began it from nothing."""
    fsm = _fsm()
    _be_in(fsm, "course_b", "The July Crisis", "because of the alliances?")
    fsm.active_course_uid = "course_a"
    fsm.current_lesson_node = {"uid": "con_1", "title": "Ohm's Law"}
    fsm.transcript = []

    fsm.transition({"type": "SET_CONTEXT",
                    "payload": {"course_uid": "course_b"}})

    assert fsm.current_lesson_node is not None, "incoming course not restored"
    assert fsm.current_lesson_node["title"] == "The July Crisis"
    assert fsm.transcript, "transcript not restored"


def test_switching_back_and_forth_keeps_both_positions():
    """The behaviour a learner actually expects from two courses."""
    fsm = _fsm()
    _be_in(fsm, "course_a", "Ohm's Law", "a-answer")
    _be_in(fsm, "course_b", "The July Crisis", "b-answer")

    fsm.transition({"type": "SET_CONTEXT", "payload": {"course_uid": "course_a"}})
    assert fsm.current_lesson_node["title"] == "Ohm's Law"

    fsm.transition({"type": "SET_CONTEXT", "payload": {"course_uid": "course_b"}})
    assert fsm.current_lesson_node["title"] == "The July Crisis"

    fsm.transition({"type": "SET_CONTEXT", "payload": {"course_uid": "course_a"}})
    assert fsm.current_lesson_node["title"] == "Ohm's Law"


def test_struggle_survives_a_pause():
    """`turn_state.misses` drives "CHANGE YOUR APPROACH". Losing it means a
    learner who gave up on a hard point is greeted as a beginner on it."""
    fsm = _fsm()
    fsm.active_course_uid = "course_a"
    fsm.current_lesson_node = {"uid": "con_1", "title": "Ohm's Law"}
    fsm.turn_state.misses = 2
    fsm.turn_state.current_question = "what happens to the current?"
    fsm._save_current_course_progress()

    fresh = _fsm()
    fresh.storage.fsm.rows = fsm.storage.fsm.rows
    fresh.active_course_uid = "course_a"
    fresh._load_course_progress("course_a")

    assert fresh.turn_state.misses == 2, "the learner's struggle was forgotten"
    assert fresh.turn_state.current_question == "what happens to the current?"


def test_a_fresh_course_starts_clean():
    """Restoring must not leak the previous course's struggle into a new one."""
    fsm = _fsm()
    fsm.active_course_uid = "course_a"
    fsm.turn_state.misses = 3
    fsm._save_current_course_progress()

    fsm.transition({"type": "SET_CONTEXT", "payload": {"course_uid": "course_new"}})
    assert fsm.turn_state.misses == 0
    assert fsm.transcript == []


def test_resume_points_reports_every_course():
    fsm = _fsm()
    _be_in(fsm, "course_a", "Ohm's Law", "x")
    _be_in(fsm, "course_b", "The July Crisis", "y")
    points = fsm.resume_points()
    assert set(points) == {"course_a", "course_b"}
    assert points["course_a"]["concept_title"] == "Ohm's Law"
    assert points["course_b"]["saved_at"] is not None


def test_resume_points_never_raises_on_a_corrupt_blob():
    fsm = _fsm()
    fsm.storage.fsm.rows["student_1"] = '{"courses": {"c": "not-a-dict"}}'
    assert fsm.resume_points() == {}


def test_turn_state_round_trip_survives_corruption():
    from services.common import turn_state as ts_io
    assert ts_io.from_dict(None).misses == 0
    assert ts_io.from_dict("garbage").misses == 0
    assert ts_io.to_dict(None) == {}


# --- efficiency with many courses --------------------------------------------

def test_course_stats_are_memoised_on_mtime(tmp_path):
    """`/api/courses` calls this PER COURSE and each call parsed that course's
    structure.json — 28-84 KB apiece in the real data directory."""
    import json
    import os
    from services.common.storage import StorageManager

    data = tmp_path / "data"
    (data / "courses" / "course_x").mkdir(parents=True)
    structure = {"uid": "course_x", "title": "X", "modules": [
        {"units": [{"lessons": [{"concepts": [{"title": "a"}, {"title": "b"}]}]}]}]}
    path = data / "courses" / "course_x" / "structure.json"
    path.write_text(json.dumps(structure))

    sm = StorageManager(data_dir=str(data))
    sm.courses._stats_cache.clear()

    reads = {"n": 0}
    original = sm.courses.get_course

    def counting(uid):
        reads["n"] += 1
        return original(uid)

    sm.courses.get_course = counting

    first = sm.courses.get_course_stats("course_x")
    assert first["concepts"] == 2
    for _ in range(5):
        sm.courses.get_course_stats("course_x")
    assert reads["n"] == 1, f"structure re-read {reads['n']} times"


def test_changing_a_course_invalidates_its_stats(tmp_path):
    """A rebuilt or re-hydrated course must re-count, or the cards go stale."""
    import json
    import os
    import time
    from services.common.storage import StorageManager

    data = tmp_path / "data"
    (data / "courses" / "course_y").mkdir(parents=True)
    path = data / "courses" / "course_y" / "structure.json"
    path.write_text(json.dumps({"uid": "course_y", "modules": [
        {"units": [{"lessons": [{"concepts": [{"title": "a"}]}]}]}]}))

    sm = StorageManager(data_dir=str(data))
    sm.courses._stats_cache.clear()
    assert sm.courses.get_course_stats("course_y")["concepts"] == 1

    time.sleep(0.01)
    path.write_text(json.dumps({"uid": "course_y", "modules": [
        {"units": [{"lessons": [{"concepts": [{"title": "a"},
                                              {"title": "b"}]}]}]}]}))
    os.utime(path, None)
    assert sm.courses.get_course_stats("course_y")["concepts"] == 2, \
        "stale stats served after the structure changed"


def test_missing_structure_is_not_cached(tmp_path):
    """A course mid-build has no structure yet and acquires one."""
    from services.common.storage import StorageManager
    data = tmp_path / "data"
    (data / "courses").mkdir(parents=True)
    sm = StorageManager(data_dir=str(data))
    sm.courses._stats_cache.clear()
    sm.courses.get_course_stats("course_missing")
    assert "course_missing" not in sm.courses._stats_cache


def test_concept_text_is_not_persisted_into_the_blob():
    """It is up to 10 KB per course of something already on disk, and it is
    the WRONG copy once a concept is re-hydrated."""
    import json
    fsm = _fsm()
    fsm.active_course_uid = "course_a"
    fsm.current_lesson_node = {"uid": "con_1", "title": "Ohm's Law",
                               "text": "x" * 5000, "resource_text": "x" * 5000}
    fsm._save_current_course_progress()
    blob = fsm.storage.fsm.rows["student_1"]
    assert "xxxxx" not in blob, "concept text written into the session blob"
    assert json.loads(blob)["courses"]["course_a"]["current_node"]["title"] \
        == "Ohm's Law"
