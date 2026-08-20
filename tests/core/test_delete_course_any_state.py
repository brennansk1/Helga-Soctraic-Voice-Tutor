"""Deleting a course must work while you are studying it.

`/api/delete_course` does two things: it tells RAG to remove the rows and the
files, and it tells the FSM to drop its runtime state. The first always
happened. The second was nested inside `if self.state == "LOBBY"`, so deleting
the course you were in the middle of removed it from disk and left the FSM
still holding `active_course_uid`, `current_lesson_node` and a syllabus queue
pointing at concepts whose markdown no longer exists.

Same shape as LRN-4 (RESUME_COURSE was LOBBY-only) and LRN-9 (the back button
never told the FSM). Those two were promoted to global handlers; this one was
missed, and the delete button is the one place where "the backend did half of
it" leaves the user looking at something that is not there any more.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))


class _MockFlaskApp:
    def __init__(self, *a, **kw):
        pass

    def route(self, *a, **kw):
        return lambda f: f

    def run(self, *a, **kw):
        pass


flask_mock = MagicMock()
flask_mock.Flask = _MockFlaskApp
flask_mock.request = MagicMock()
cb_mock = MagicMock()
cb_mock.__file__ = 'mocked_course_builder.py'

_CORE_DEPS = {
    'kuzu': MagicMock(), 'libzim': MagicMock(),
    'sentence_transformers': MagicMock(), 'psutil': MagicMock(),
    'yaml': MagicMock(), 'fsrs_engine': MagicMock(), 'safety': MagicMock(),
    'service_manager': MagicMock(), 'db_manager': MagicMock(),
    'content_provider': MagicMock(), 'course_builder': cb_mock,
}

with patch.dict('sys.modules', _CORE_DEPS):
    from services.core.fsm_logic import MnemosyneFSM


@pytest.fixture
def fsm():
    m = MnemosyneFSM.__new__(MnemosyneFSM)
    m.active_course_uid = None
    m.current_lesson_node = None
    m.syllabus_queue = []
    m.state = "LOBBY"
    m.transcript = ["a line"]
    m.conversation_history = [{"role": "user", "content": "hi"}]
    m.student_id = "test"
    m.spoken = []
    m.speak = lambda t, *a, **k: m.spoken.append(t)
    m.stop_audio = lambda *a, **k: None
    m._read_session_blob = lambda: {"courses": {}, "last_active_uid": None}
    m._save_current_course_progress = lambda *a, **k: None
    m.storage = MagicMock()
    return m


def _studying(m, uid="course_abc"):
    m.state = "SOCRATIC_LEARNING"
    m.active_course_uid = uid
    m.current_lesson_node = {"uid": "con_1", "title": "A concept"}
    m.syllabus_queue = [{"uid": "con_2"}]
    return m


def test_delete_clears_state_from_the_lobby(fsm):
    fsm.active_course_uid = "course_abc"
    fsm.transition({"type": "DELETE_COURSE", "payload": {"uid": "course_abc"}})
    assert fsm.active_course_uid is None


def test_delete_clears_state_while_studying_that_course(fsm):
    """The regression: this used to leave the FSM teaching a deleted course."""
    _studying(fsm, "course_abc")
    fsm.transition({"type": "DELETE_COURSE", "payload": {"uid": "course_abc"}})
    assert fsm.active_course_uid is None, (
        "the FSM kept pointing at a course whose files and rows are gone")
    assert fsm.current_lesson_node is None
    assert fsm.syllabus_queue == []
    assert fsm.state == "LOBBY"
    # the dialogue belonged to the course; it goes with it
    assert fsm.transcript == []
    assert fsm.conversation_history == []


def test_deleting_a_different_course_does_not_end_the_session(fsm):
    """Only the deleted course is dropped -- not whatever you are studying."""
    _studying(fsm, "course_abc")
    fsm.transition({"type": "DELETE_COURSE", "payload": {"uid": "course_other"}})
    assert fsm.active_course_uid == "course_abc"
    assert fsm.state == "SOCRATIC_LEARNING"
    assert fsm.current_lesson_node is not None
    assert fsm.transcript == ["a line"], "an unrelated delete must not wipe the session"


def test_delete_without_a_uid_is_ignored(fsm):
    _studying(fsm, "course_abc")
    fsm.transition({"type": "DELETE_COURSE", "payload": {}})
    assert fsm.active_course_uid == "course_abc"


# --------------------------------------------------------------- storage side

def test_deleting_a_built_course_detaches_its_programme_slot(tmp_path):
    """The requirement survives; only the built content goes.

    `delete_course` cascades across eight tables so no orphan rows leak. When
    schema v17 added programmes, `program_courses` was not added to that list --
    so deleting a course built from a degree left its slot marked `built=1`
    pointing at a course_uid with nothing behind it, and the degree page went on
    showing it as built.

    Deleting the ROW would have been the other wrong fix: the learner deleted
    the generated content, not the degree requirement. It reverts to unbuilt.
    """
    from services.common.storage import StorageManager

    sm = StorageManager(data_dir=str(tmp_path))
    sm.programs.create("prog_t", {
        "subject": "X", "template": "associate", "terms": 4,
        "courses": [
            {"title": "A", "slot": "core", "term": 1, "requires": [], "built": False},
            {"title": "B", "slot": "core", "term": 1, "requires": [], "built": False},
        ]})
    sm.courses.create_course({"uid": "course_zz", "title": "A", "modules": []})
    sm.programs.mark_built("prog_t", "A", "course_zz")

    built = {c["title"]: c for c in sm.programs.get("prog_t")["courses"]}
    assert built["A"]["built"] and built["A"]["course_uid"] == "course_zz"

    sm.courses.delete_course("course_zz")

    after = sm.programs.get("prog_t")["courses"]
    assert len(after) == 2, "the degree requirement must survive the delete"
    slot = {c["title"]: c for c in after}["A"]
    assert not slot["built"], "slot still claims to be built"
    assert not slot["course_uid"], "slot still points at a deleted course"
