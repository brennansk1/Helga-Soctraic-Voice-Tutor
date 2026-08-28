"""Why a course is not ready has to reach the learner.

The audit gate writes a precise sentence into structure.json — "4 of 4 concepts
are missing sections the tutor reads — there is no lesson to teach" — and it
stopped there. The course list showed the generic "Checked, with caveats"
instead, so a course that could not be taught at all looked exactly like one
with a few rough edges. Two of four courses on this machine were in that state.
"""
import pytest

from services.core.course_audit import (
    TUTOR_SECTIONS, count_teachable, is_teachable,
)

FULL = """# A Concept

## Core Explanation
""" + ("word " * 40) + """

## Misconceptions
- **Belief**: something wrong here entirely.
  **Correction**: """ + ("word " * 30) + """

## Analogies
""" + ("word " * 40) + """
"""

OUTLINE = """# A Concept

## Core Explanation
Two words.
"""


def test_a_full_concept_is_teachable():
    assert is_teachable(FULL)


def test_an_outline_is_not_teachable_even_though_the_file_exists():
    """Counting files on disk called these complete; the tutor cannot run a
    lesson from any of them."""
    assert not is_teachable(OUTLINE)
    assert not is_teachable("")
    assert not is_teachable(None)


@pytest.mark.parametrize("missing", TUTOR_SECTIONS)
def test_every_tutor_section_is_load_bearing(missing):
    stripped = "\n".join(
        block for block in FULL.split("\n## ")
        if not block.startswith(missing))
    assert not is_teachable(stripped), f"a concept without ## {missing} passed"


def test_a_present_but_empty_section_does_not_count():
    """"Present but empty of content" is the audit's own phrase for this."""
    thin = FULL.replace("## Analogies\n" + ("word " * 40), "## Analogies\nSee above.")
    assert not is_teachable(thin)


def _structure(uids):
    return {"modules": [{"units": [{"lessons": [
        {"concepts": [{"uid": u, "title": u} for u in uids]}]}]}]}


def test_count_teachable_reports_both_halves():
    content = {"a": FULL, "b": FULL, "c": OUTLINE, "d": ""}
    taught, total = count_teachable(_structure("abcd"), content.get)
    assert (taught, total) == (2, 4), "this is the Regex course's real state"


def test_count_teachable_survives_an_unreadable_concept():
    def boom(uid):
        if uid == "b":
            raise OSError("gone")
        return FULL
    taught, total = count_teachable(_structure("ab"), boom)
    assert (taught, total) == (1, 2), "one bad file must not lose the count"


def test_an_empty_structure_is_zero_not_a_crash():
    assert count_teachable({}, lambda u: "") == (0, 0)
    assert count_teachable(None, lambda u: "") == (0, 0)


def test_walk_is_unpacked_correctly():
    """walk_concepts yields (concept, path); treating it as a bare concept
    silently counted zero and would have reported every course as empty."""
    taught, total = count_teachable(_structure(["only"]), lambda u: FULL)
    assert total == 1 and taught == 1
