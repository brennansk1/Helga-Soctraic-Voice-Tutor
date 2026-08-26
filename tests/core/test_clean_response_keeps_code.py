"""Stripping markdown must not strip SQL.

`clean_llm_response` removes emphasis so the text can be spoken. One of its
rules is `\\*(.+?)\\*` — italics — and in a SQL lesson that rule eats the
language. Measured on a live turn: the model wrote

    `SELECT name, price * 1.1 FROM products` ... It sees `price * 1.1`

and the learner was shown "price 1.1", twice. The regex matched from the first
asterisk to the second and removed both, along with nothing in between that was
emphasis at all. The turn was explaining what `*` MEANS.

`SELECT *` is the most common statement in the language this course teaches, so
these are pinned in both directions: code survives, prose emphasis still goes.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "services", "core"))

_DEPS = {k: MagicMock() for k in (
    "kuzu", "libzim", "sentence_transformers", "psutil", "yaml", "fsrs_engine",
    "safety", "service_manager", "db_manager", "content_provider",
    "course_builder")}


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    os.environ.setdefault("DATA_ROOT", str(tmp_path_factory.mktemp("data")))
    with patch.dict("sys.modules", _DEPS):
        from services.core.fsm_logic import clean_llm_response
    return clean_llm_response


# --- code must survive -------------------------------------------------------

@pytest.mark.parametrize("text,must_keep", [
    # The exact live failure.
    ("The asterisk is a wildcard. In `SELECT name, price * 1.1 FROM products`, "
     "the engine sees `price * 1.1` and calculates it.", "price * 1.1"),
    ("Use `SELECT * FROM users` to get every column.", "SELECT * FROM users"),
    ("Compare `a * b` with `a + b`.", "a * b"),
    ("```sql\nSELECT * FROM t WHERE x > 1;\n```", "SELECT * FROM t"),
    # A line starting with `* ` inside code is not a bullet.
    ("```sql\nSELECT\n  * \nFROM t;\n```", "*"),
])
def test_code_is_not_treated_as_markdown(clean, text, must_keep):
    out = clean(text)
    assert must_keep in out, f"code was mangled: {out!r}"


def test_two_separate_code_spans_both_survive(clean):
    """The failure needed two asterisks to bite — one in each span."""
    out = clean("First `SELECT *` then later `COUNT(*)` in the same turn.")
    assert "SELECT *" in out and "COUNT(*)" in out, out


# --- prose emphasis must still go -------------------------------------------

@pytest.mark.parametrize("text", [
    "This is **bold** and *italic* text.",
    "Some __underlined__ and _emphasised_ words.",
])
def test_prose_emphasis_is_still_stripped(clean, text):
    out = clean(text)
    assert "*" not in out and "_" not in out, (
        f"emphasis survived, so speech would read the markers aloud: {out!r}")


def test_emphasis_around_code_is_stripped_but_code_is_not(clean):
    out = clean("That is *very* important: `SELECT * FROM t`.")
    assert "very" in out and "*very*" not in out
    assert "SELECT * FROM t" in out


def test_placeholder_never_reaches_the_learner(clean):
    """The parking mechanism must be invisible whatever the input."""
    out = clean("Odd input with `code` and a stray \x00CODE9\x00 marker.")
    assert "\x00" not in out
    assert "CODE9" not in out or "code" in out
