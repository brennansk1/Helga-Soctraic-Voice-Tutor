"""The `code` aid — source as a teaching object, not a paste.

A markdown fence can only show FINISHED code. The whole reason code is an aid
is `stage`: it can blank the line the student has to supply and reveal it once
they have answered. That is the exact analogue of labelling a triangle's
unknown side "?", and without it, showing code in a Socratic turn hands over
the answer with syntax colouring on it.
"""
import pytest

from services.common.visual_aids import KINDS, normalize_aid


def _aid(**spec):
    base = {"language": "python", "code": "a = 1\nb = 2\nreturn a + b"}
    base.update(spec)
    return normalize_aid({"kind": "code", "title": "T", "spec": base})


def test_code_is_a_recognised_kind():
    assert "code" in KINDS


def test_a_plain_listing_normalises():
    aid, err = _aid()
    assert err is None
    assert aid["spec"]["lines"] == ["a = 1", "b = 2", "return a + b"]


def test_a_blank_creates_a_stage():
    """The stage machinery is what makes this a question."""
    aid, err = _aid(blanks=[{"line": 3, "hint": "what do you return?"}])
    assert err is None
    assert aid["stages_total"] >= 1
    assert aid["spec"]["blanks"][0]["line"] == 3
    assert aid["spec"]["blanks"][0]["stage"] >= 1


def test_a_blank_outside_the_listing_is_clamped_not_crashed():
    aid, err = _aid(blanks=[{"line": 99, "hint": "x"}])
    assert err is None
    assert aid["spec"]["blanks"][0]["line"] <= len(aid["spec"]["lines"])


def test_an_unknown_language_degrades_to_text_rather_than_failing():
    """A tutor teaching Prolog should not lose its figure."""
    aid, err = _aid(language="prolog")
    assert err is None and aid["spec"]["language"] == "text"


def test_highlight_lines_outside_the_listing_are_dropped():
    aid, err = _aid(highlight=[1, 2, 999, "x"])
    assert err is None
    assert aid["spec"]["highlight"] == [1, 2]


def test_an_empty_listing_is_refused():
    aid, err = normalize_aid({"kind": "code", "spec": {"code": "   \n\n"}})
    assert aid is None and "at least one line" in err


def test_a_long_listing_is_truncated_to_a_teachable_size():
    """This rides in a chat turn; it cannot be a whole file."""
    from services.common.visual_aids import MAX_CODE_LINES, MAX_CODE_LINE
    # Long in LINES but comfortably inside MAX_SPEC_BYTES, so truncation is
    # what should fire here rather than the global size guard.
    aid, err = _aid(code="\n".join(f"line_{i} = {i}" for i in range(120)))
    assert err is None
    assert len(aid["spec"]["lines"]) == MAX_CODE_LINES
    assert all(len(l) <= MAX_CODE_LINE for l in aid["spec"]["lines"])


def test_an_enormous_payload_is_refused_outright():
    """The global MAX_SPEC_BYTES guard fires BEFORE per-kind truncation, and
    should: an 80KB paste is not a teaching figure, and silently showing its
    first 40 lines would hide that the tutor sent something absurd. Recorded
    as a test so nobody loosens a shared safety bound to suit this kind."""
    aid, err = _aid(code="\n".join("x" * 400 for _ in range(200)))
    assert aid is None and "too large" in err


def test_compare_to_gives_a_before_and_after():
    """"What changed when you fixed it?" needs both halves together."""
    aid, err = _aid(compare_to="a = 1\nb = 3\nreturn a * b")
    assert err is None and len(aid["spec"]["compare_to"]) == 3


# ------------------------------------------------------------ description
# What a screen reader announces, what TTS speaks, and what REPLACES the
# listing entirely in text-only mode. Reading 40 lines of source aloud is
# useless, so it describes the shape and carries the question.

def test_the_description_carries_the_question_not_the_source():
    aid, _ = _aid(code="def f():\n    return 1",
                  blanks=[{"line": 2, "hint": "what comes back?"}])
    alt = aid["alt"]
    assert "blank" in alt and "what comes back?" in alt
    assert "return 1" not in alt, "the description must not leak the answer"


def test_the_description_names_the_language_and_size():
    aid, _ = _aid()
    assert "python" in aid["alt"] and "3 line" in aid["alt"]


def test_the_description_mentions_highlighted_lines():
    aid, _ = _aid(highlight=[2])
    assert "line 2" in aid["alt"]


def test_the_description_says_when_it_is_a_comparison():
    aid, _ = _aid(compare_to="x = 1")
    assert "second version" in aid["alt"]
