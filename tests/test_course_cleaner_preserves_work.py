"""An unfinished course is not automatically garbage.

`clean_failed_courses` runs on every RAG service import and used to delete any
course in {failed, hydration_failed, building, skeleton} outright — directory
and SQLite row, no prompt, no backup.

Hydration is resumable: it skips every concept that already has content. So a
course stopped at 60 of 100 concepts is hours of model time that only needs
finishing, and the courses list now offers a "Resume build" button to finish it.
Deleting it made that button unreachable, and meant the stale-build reaper —
which marks an abandoned build "failed" so it stops showing as in progress —
handed its courses straight to the cleaner.

Measured: a 101-concept course sat at "skeleton" for five and a half hours
while it hydrated. Starting the stack in that window would have deleted all of
it.
"""
import json
import os

import pytest

from services.common.course_cleaner import clean_failed_courses


def _course(root, uid, status, concepts=0):
    d = os.path.join(root, "courses", uid)
    os.makedirs(os.path.join(d, "content"), exist_ok=True)
    with open(os.path.join(d, "structure.json"), "w") as f:
        json.dump({"uid": uid, "status": status, "modules": []}, f)
    for i in range(concepts):
        with open(os.path.join(d, "content", f"con_{i:08x}.md"), "w") as f:
            f.write("# real content\n")
    return d


@pytest.mark.parametrize("status",
                         ["skeleton", "building", "failed", "hydration_failed"])
def test_an_unfinished_course_with_content_is_preserved(tmp_path, status):
    d = _course(str(tmp_path), "course_keepme", status, concepts=3)
    clean_failed_courses(str(tmp_path))
    assert os.path.isdir(d), (
        f"a {status!r} course with hydrated concepts was deleted; resuming it "
        f"would have cost nothing and rebuilding costs hours")


@pytest.mark.parametrize("status",
                         ["skeleton", "building", "failed", "hydration_failed"])
def test_an_unfinished_course_with_NO_content_is_still_removed(tmp_path, status):
    """The cleaner still earns its keep: nothing was generated, so nothing is
    lost, and an empty shell in the list is just noise."""
    d = _course(str(tmp_path), "course_empty", status, concepts=0)
    clean_failed_courses(str(tmp_path))
    assert not os.path.isdir(d)


def test_a_ready_course_is_never_touched(tmp_path):
    d = _course(str(tmp_path), "course_ready", "ready", concepts=1)
    clean_failed_courses(str(tmp_path))
    assert os.path.isdir(d)


def test_a_course_with_no_structure_file_is_still_removed(tmp_path):
    d = os.path.join(str(tmp_path), "courses", "course_bare")
    os.makedirs(d, exist_ok=True)
    clean_failed_courses(str(tmp_path))
    assert not os.path.isdir(d)
