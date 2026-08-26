"""Activity logging is the input to the day streak and the whole parent
dashboard, and it failed silently for every completed concept: the call passed
(course, concept, type, details) into a signature of
(course_uid, activity_type, concept_uid, unit_uid), so the details dict landed
on unit_uid, SQLite refused to bind it, and a surrounding except logged a
warning nobody read. activity_log held 0 rows.
"""
import inspect

import pytest

from services.common.storage import StorageManager


def test_log_activity_rejects_positional_misuse(tmp_path):
    """The extra arguments are keyword-only, so the original mistake is now a
    TypeError at the call site instead of an empty table months later."""
    sm = StorageManager(data_dir=str(tmp_path))
    sig = inspect.signature(sm.activity.log_activity)
    for name in ("concept_uid", "unit_uid", "details"):
        assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name

    with pytest.raises(TypeError):
        sm.activity.log_activity("course_x", "con_y", "concept_completed", {"t": 1})


def test_completed_concept_is_logged_and_counts_toward_the_streak(tmp_path):
    """The end-to-end claim Home makes: finish something today, streak is 1."""
    sm = StorageManager(data_dir=str(tmp_path))
    sm.activity.log_activity(
        course_uid="course_test0001",
        activity_type="concept_completed",
        concept_uid="con_test0001",
        details={"title": "Anything"},
    )
    rows = sm.activity.get_activities()
    assert len(rows) == 1, "the completion did not reach activity_log"
    assert rows[0]["activity_type"] == "concept_completed"
    assert rows[0]["concept_uid"] == "con_test0001"
    assert sm.activity.get_streak() == 1
