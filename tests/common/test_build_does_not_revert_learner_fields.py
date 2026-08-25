"""A writer that started hours ago must not win.

A hydration run loads one `course` dict at resume time and writes it back on
every progress update, for hours. update_course overwrites structure.json with
whatever it is given, so anything changed on disk in that window is silently
reverted.

Measured 2026-08-25: a course title corrected from "advanced sql" to
"Advanced SQL" reverted within minutes on the course that was building, while
the identical correction stuck on an idle course.

The build owns modules, status and its verdicts. It does not own what the
course is called, how it is taught, or what the learner said they wanted.
"""
import pytest

from services.common.storage import StorageManager


@pytest.fixture()
def store(tmp_path):
    return StorageManager(str(tmp_path)).courses


def _course(**over):
    base = {"uid": "course_x", "title": "advanced sql", "status": "skeleton",
            "teaching_style": "hands-on", "learner_context": "window functions",
            "modules": [{"uid": "mod_1", "title": "M", "units": []}]}
    base.update(over)
    return base


def test_a_stale_writer_does_not_revert_the_title(store):
    """Exactly what the hydrator does: load once, write back hours later."""
    store.create_course(_course())

    # The build loads the course and holds it (for hours, in production).
    held_by_build = store.get_course("course_x")

    # Meanwhile a learner — or a repair — renames it.
    live = store.get_course("course_x")
    live["title"] = "Advanced SQL"
    store.update_course("course_x", live)

    # The build now writes back the copy it loaded BEFORE the rename.
    held_by_build["status"] = "building"
    store.update_course("course_x", held_by_build)

    got = store.get_course("course_x")
    assert got["title"] == "Advanced SQL", "the stale build reverted the rename"
    assert got["status"] == "building", "the build must still own the status"


def test_the_brief_and_style_are_protected_too(store):
    store.create_course(_course())
    held_by_build = store.get_course("course_x")

    live = store.get_course("course_x")
    live["teaching_style"] = "ELI5"
    live["learner_context"] = "only recursive CTEs"
    store.update_course("course_x", live)

    store.update_course("course_x", held_by_build)

    got = store.get_course("course_x")
    assert got["teaching_style"] == "ELI5"
    assert got["learner_context"] == "only recursive CTEs"


def test_a_deliberate_change_still_applies(store):
    """Protection must not make a rename impossible.

    A read-modify-write carries the current stamp, so it is not stale and is
    honoured — no flag needed. This is the case that broke when the first
    version of this protection keyed off the field name instead of staleness:
    update_course silently ignored a new title.
    """
    store.create_course(_course())
    c = store.get_course("course_x")
    c["title"] = "Advanced SQL"
    store.update_course("course_x", c)
    assert store.get_course("course_x")["title"] == "Advanced SQL"


def test_a_course_built_from_scratch_is_honoured(store):
    """A literal dict with no timestamp is a deliberate construction, not a
    stale copy, and must be written as given."""
    store.create_course(_course())
    store.update_course("course_x", _course(title="Written Deliberately"))
    assert store.get_course("course_x")["title"] == "Written Deliberately"


def test_the_build_still_owns_the_structure(store):
    """Only the named metadata is protected — not the course content."""
    store.create_course(_course())
    grown = _course(modules=[{"uid": "mod_1", "title": "M", "units": []},
                             {"uid": "mod_2", "title": "M2", "units": []}])
    store.update_course("course_x", grown)
    assert len(store.get_course("course_x")["modules"]) == 2
