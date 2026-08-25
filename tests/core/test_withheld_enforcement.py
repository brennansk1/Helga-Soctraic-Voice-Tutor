"""A concept marked withheld must actually be withheld.

Pass 3 marks a concept it could not repair — one still stating something a
real database contradicts. The flag was WRITTEN by the repair pass and read by
nothing: the FSM served it exactly as before, the structure endpoint dropped it
before the path view ever saw it, and the audit report said the course was
protected.

That is worse than not having built the mechanism. A safety feature nobody
enforces is a false assurance, and it is this repo's signature defect —
`source_confidence` was dropped by the same endpoint, which is why the
low-confidence badge never rendered once.

Three readers have to honour it, and each is tested here: storage keeps the
flag, the structure endpoint carries it, and the tutor refuses to teach it.
"""
import json
import sys
from unittest.mock import MagicMock, patch

import pytest


# fsm_logic imports its neighbours flatly (`from fsrs_engine import ...`), so
# it only imports with services/core on the path or those names mocked. This
# is the block tests/core/test_fsm_logic.py already uses.
class _MockFlaskApp:
    def __init__(self, *a, **kw): pass
    def route(self, *a, **kw): return lambda f: f
    def run(self, *a, **kw): pass


_flask = MagicMock()
_flask.Flask = _MockFlaskApp
_CORE_DEPS = {
    "kuzu": MagicMock(), "libzim": MagicMock(),
    "sentence_transformers": MagicMock(), "psutil": MagicMock(),
    "yaml": MagicMock(), "fsrs_engine": MagicMock(), "safety": MagicMock(),
    "service_manager": MagicMock(), "db_manager": MagicMock(),
    "content_provider": MagicMock(), "course_builder": MagicMock(),
}


def _course_with_withheld():
    return {
        "uid": "course_t", "title": "T", "teaching_domain": "computer_science",
        "modules": [{"uid": "mod_1", "title": "M", "units": [
            {"uid": "unit_1", "title": "U", "lessons": [
                {"uid": "less_1", "title": "L", "concepts": [
                    {"uid": "con_ok", "title": "Fine"},
                    {"uid": "con_bad", "title": "Broken", "withheld": True,
                     "withheld_reason": "NULL = NULL is UNKNOWN, not TRUE"},
                ]},
            ]}]}],
    }


def test_the_repair_pass_marks_the_concept_node():
    """Written where every reader looks, not only in the audit report."""
    from services.core.course_builder import ContentHydrator
    h = ContentHydrator.__new__(ContentHydrator)
    course = _course_with_withheld()
    for c in course["modules"][0]["units"][0]["lessons"][0]["concepts"]:
        c.pop("withheld", None)
        c.pop("withheld_reason", None)
    h._mark_withheld(course, [{"concept_uid": "con_bad", "title": "Broken",
                               "why": "NULL = NULL is UNKNOWN, not TRUE"}])
    concepts = course["modules"][0]["units"][0]["lessons"][0]["concepts"]
    bad = next(c for c in concepts if c["uid"] == "con_bad")
    ok = next(c for c in concepts if c["uid"] == "con_ok")
    assert bad["withheld"] is True
    assert "NULL" in bad["withheld_reason"]
    assert not ok.get("withheld"), "an unaffected concept was withheld"


def test_the_tutor_refuses_to_teach_it():
    """The reader that matters most: the one that would say it out loud."""
    with patch.dict("sys.modules", _CORE_DEPS):
        import services.core.fsm_logic as fsm_mod

    course = _course_with_withheld()
    concepts = {c["uid"]: c
                for c in course["modules"][0]["units"][0]["lessons"][0]["concepts"]}

    class _Courses:
        @staticmethod
        def get_concept_by_uid(course_uid, uid):
            return concepts.get(uid)

        @staticmethod
        def get_concept_content(course_uid, uid):
            return "# Broken\n\n## Core Explanation\n" + ("body " * 80)

    class _Storage:
        courses = _Courses()

    f = fsm_mod.MnemosyneFSM.__new__(fsm_mod.MnemosyneFSM)
    f.storage = _Storage()
    f.active_course_uid = "course_t"
    f._hd_consent_blocked = lambda uid: False
    said, events = [], []
    f.speak = lambda t, **k: said.append(t)
    f.send_status_update = lambda t, **k: events.append(k.get("event") or {})

    assert f.get_concept_details("con_bad") is None, \
        "the tutor served a concept known to state something false"
    assert any(e.get("type") == "CONCEPT_WITHHELD" for e in events)
    assert said, "the learner was told nothing about the gap"

    served = f.get_concept_details("con_ok")
    assert served is not None and served["title"] == "Fine", \
        "withholding one concept blocked an unaffected one"


def test_the_structure_endpoint_carries_the_flag():
    """The path view renders from this. Dropping it here is how the learner
    ends up clicking a concept the tutor then refuses."""
    import inspect

    from services.rag import librarian
    src = inspect.getsource(librarian.structure)
    assert '"withheld"' in src, \
        "the structure endpoint drops withheld before the path view sees it"
    assert '"withheld_reason"' in src, "the reason is dropped"
