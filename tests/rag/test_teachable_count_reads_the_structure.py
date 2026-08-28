"""teachable_count must count teachability, from the concept tree.

Two bugs, one after the other, both in the same three lines:

  1. The field returned `hydrated_count` — files written — while its own
     comment said it counts "Concepts the TUTOR CAN TEACH FROM ... not files on
     disk". Advanced SQL reported 83 with 73 teachable.

  2. Fixing it to call count_teachable(course, ...) reported 0 for EVERY
     course, which is worse. list_courses() returns SQLite rows and those carry
     no `modules`, so the walk found no concepts at all. The tree lives in
     structure.json; get_course merges it.

The second is only visible against a real store, which is what this uses.
"""
import os
import tempfile

import pytest


TEACHABLE = """# Frames

## Core Explanation
The planner keeps per-column statistics and uses them to estimate how many rows
a predicate will match, which decides between a sequential scan and an index
scan for a given query shape on this table.

## Misconceptions
- **Belief**: a sequential scan always means a missing index, which is the
  usual first reaction and is wrong more often than it is right in practice.
  **Correction**: the planner costs both routes and often prefers the scan.

## Analogies
- **Simple**: reading a whole chapter straight through is faster than looking
  up thirty separate page numbers and flipping to each one in turn, even though
  the chapter contains pages you did not need to read at all.
"""

# is_teachable also requires MIN_SECTION_WORDS (25) of body per section, so a
# heading with a sentence under it is not teachable. Measured while writing
# this: the first Analogies block came to 24 words and failed, which looked
# exactly like the bug under test. Asserted below so it cannot rot back.

OUTLINE = "# Frames\n\n## Governing Result\nSome prose but no tutor sections.\n"


def test_the_fixture_itself_is_teachable():
    """Guards the test data, not the code. A fixture one word under the
    section minimum fails identically to the defect this file is about."""
    from services.core.course_audit import is_teachable
    assert is_teachable(TEACHABLE), "the teachable fixture is not teachable"
    assert not is_teachable(OUTLINE), "the outline fixture should not be"


@pytest.fixture()
def lib(tmp_path):
    os.environ["DATA_ROOT"] = str(tmp_path)
    from services.rag import librarian
    librarian.storage = librarian.StorageManager(str(tmp_path))
    librarian._TEACHABLE_CACHE.clear()
    return librarian


def _make_course(lib, uid, bodies):
    concepts = [{"uid": f"con_{i}", "title": f"C{i}"} for i in range(len(bodies))]
    lib.storage.courses.create_course({
        "uid": uid, "title": "T", "modules": [
            {"uid": "mod_1", "title": "M", "units": [
                {"uid": "unit_1", "title": "U", "lessons": [
                    {"uid": "less_1", "title": "L", "concepts": concepts}]}]}]})
    for c, body in zip(concepts, bodies):
        lib.storage.courses.save_concept_content(uid, c["uid"], body)


def test_counts_teachable_concepts_from_a_list_courses_row(lib):
    """The row has no `modules`. Walking it directly is what returned 0."""
    _make_course(lib, "course_mix", [TEACHABLE, TEACHABLE, OUTLINE])
    row = [c for c in lib.storage.courses.list_courses()
           if c["uid"] == "course_mix"][0]
    assert "modules" not in row, "fixture no longer reproduces the real shape"

    got = lib._teachable_count(row)
    assert got == 2, (
        f"expected 2 teachable of 3, got {got!r} — 0 means the helper walked "
        "the SQLite row instead of loading the structure")


def test_does_not_fall_back_to_files_written(lib):
    """hydrated_count would say 3 here; teachability says 1."""
    _make_course(lib, "course_thin", [TEACHABLE, OUTLINE, OUTLINE])
    row = [c for c in lib.storage.courses.list_courses()
           if c["uid"] == "course_thin"][0]
    assert lib._teachable_count(row) == 1


def test_an_unreadable_course_reports_unknown_not_zero(lib):
    """None says "not measured". Zero says "nothing can be taught", which is a
    claim, and the wrong one to make when the structure simply is not there."""
    assert lib._teachable_count({"uid": "course_absent"}) is None
    assert lib._teachable_count({}) is None
