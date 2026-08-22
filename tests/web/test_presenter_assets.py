r"""The chat presenter's offline assets must actually be present.

WHY THIS TEST EXISTS
--------------------
`session.js` renders LaTeX with KaTeX and guards on `window.katex`, falling
back to raw TeX when it is absent. That fallback is good engineering and it is
also why nobody noticed that **KaTeX was never installed**: maths rendered as
raw `\lambda v = Av` in the chat, readable enough that it looked deliberate.

A graceful degradation that nothing checks is indistinguishable from a missing
feature. This asserts the files are there.

The same argument covers kind parity: the model is told in the prompt which
`kind` values it may emit, and the browser has a renderer per kind. If those
two lists drift, the tutor emits a diagram the UI silently drops.
"""
import os
import re

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
_KATEX = os.path.join(_ROOT, "services/web-ui/static/vendor/katex")
_AIDS_JS = os.path.join(_ROOT, "services/web-ui/static/js/aids.js")
_LEARN = os.path.join(_ROOT, "services/web-ui/templates/learn.html")


# ------------------------------------------------------------------- KaTeX
def test_katex_is_vendored_for_offline_use():
    """Helga is offline-first; a CDN reference would be a broken dependency."""
    for name in ("katex.min.js", "katex.min.css"):
        path = os.path.join(_KATEX, name)
        assert os.path.exists(path), (
            f"{name} missing — chat maths falls back to raw TeX. "
            f"See {_KATEX}/README.md for the install.")
        assert os.path.getsize(path) > 10_000, f"{name} looks truncated"


def test_katex_fonts_are_present():
    """The CSS references the font files; without them glyphs fall back."""
    fonts = os.path.join(_KATEX, "fonts")
    assert os.path.isdir(fonts), "katex/fonts missing"
    woff2 = [f for f in os.listdir(fonts) if f.endswith(".woff2")]
    assert len(woff2) >= 10, f"only {len(woff2)} woff2 fonts; expected the full set"


def test_learn_page_loads_katex_locally_not_from_a_cdn():
    html = open(_LEARN, encoding="utf-8").read()
    assert "vendor/katex/katex.min.js" in html
    assert "cdn.jsdelivr" not in html and "unpkg.com" not in html, (
        "an offline product must not reach a CDN for maths")


# --------------------------------------------------------------- kind parity
def _frontend_kinds():
    src = open(_AIDS_JS, encoding="utf-8").read()
    body = re.search(r"RENDER\s*=\s*\{(.*?)\n\};", src, re.S)
    if body:
        return set(re.findall(r"^\s{2}([a-z_]+)\s*:", body.group(1), re.M))
    return set(re.findall(r"RENDER\.([a-z_]+)\s*=", src))


def test_every_kind_the_model_may_emit_has_a_renderer():
    """A kind the browser cannot draw is a diagram the learner never sees."""
    from services.common.visual_aids import KINDS
    missing = set(KINDS) - _frontend_kinds()
    assert not missing, f"no browser renderer for: {sorted(missing)}"


def test_the_prompt_advertises_only_kinds_that_render():
    """The reverse drift: telling the model about a kind the UI dropped."""
    from services.common import prompts
    from services.common.visual_aids import KINDS
    text = prompts.aid_rules(requested=True)
    line = [l for l in text.splitlines() if l.startswith("kind must be one of")]
    assert line, "the grammar no longer states the allowed kinds"
    advertised = {k.strip() for k in re.findall(r"[a-z_]+", line[0])}
    renderable = _frontend_kinds()
    for kind in KINDS:
        if kind == "image":            # build-time only, never model-authored
            continue
        if kind in advertised:
            assert kind in renderable, f"prompt offers {kind}, UI cannot draw it"


@pytest.mark.parametrize("kind", ["timeline", "code", "image", "plot"])
def test_the_kinds_the_product_promises_are_renderable(kind):
    """Named in the README: timelines for history, code for CS, collected
    assets, plots for maths."""
    assert kind in _frontend_kinds(), f"{kind} has no renderer"


# --------------------------------------- a mis-heard word is not a wrong answer
#
# `session.js` auto-sent the raw ASR transcript with no confirmation. For an
# adult that is convenience. For a pre-reader it is unsafe: the child cannot
# read the transcript to catch a mis-hearing, so a transcription error is
# submitted and GRADED as a wrong answer — a fact about the microphone recorded
# as a fact about the learner.
#
# Child ASR word-error rate is 20-40% at ages 5-6 (Yeung & Alwan 2018), reaching
# adult accuracy only around age 13. This is the common case for young bands.

_SESSION_JS = os.path.join(_ROOT, "services/web-ui/static/js/session.js")
_LEARN_HTML = os.path.join(_ROOT, "services/web-ui/templates/learn.html")


def test_voice_autosend_is_gated_not_unconditional():
    src = open(_SESSION_JS, encoding="utf-8").read()
    assert "HELGA_VOICE_AUTOSEND" in src, (
        "the transcript is auto-sent with no gate")
    # the send must sit inside the gate, not beside it
    idx_gate = src.index("HELGA_VOICE_AUTOSEND")
    idx_send = src.index("sendTextMessage();", idx_gate)
    between = src[idx_gate:idx_send]
    assert "else" in between, "sendTextMessage is not inside the gate's else"


def test_the_page_supplies_the_flag():
    html = open(_LEARN_HTML, encoding="utf-8").read()
    assert "HELGA_VOICE_AUTOSEND" in html
    assert "voice_autosend" in html, "the flag is hardcoded, not server-driven"


def test_the_default_keeps_adult_behaviour():
    """An unknown band must not silently disable voice for adults."""
    html = open(_LEARN_HTML, encoding="utf-8").read()
    assert "is not defined or voice_autosend" in html, (
        "a missing flag must default to auto-send, not to off")


# ------------------------------------------- the code aid must render a LANGUAGE
#
# The code presenter was broken at three layers at once, each fatal on its own:
#   1. no aid-policy pattern ever yielded `code`      -> never requested
#   2. "sql query" matched none of SELECT/JOIN/GROUP BY -> 0/6 real SQL topics
#   3. uppercase SQL keywords rendered as plain text  -> a grey box
#
# SQL is the sharpest test because it is a real language a learner is likely to
# be shown, and it is conventionally written in UPPER CASE — the one form the
# highlighter could not match.

_AIDS_JS_SRC = open(_AIDS_JS, encoding="utf-8").read()


def test_sql_comments_are_recognised():
    """`--` is SQL's comment marker; only // and # were handled."""
    assert "--[^\\n]*" in _AIDS_JS_SRC, (
        "SQL comments render as plain code")


def test_sql_keyword_matching_folds_case():
    """SELECT ... FROM ... WHERE is the ordinary way to write SQL, and every
    one of those rendered as plain text against a lower-case keyword list."""
    assert "foldCase" in _AIDS_JS_SRC
    assert "m[4].toLowerCase()" in _AIDS_JS_SRC


def test_sql_has_keywords_defined_at_all():
    assert "sql:" in _AIDS_JS_SRC
    for kw in ("select", "from", "where", "join", "group"):
        assert kw in _AIDS_JS_SRC


def test_the_backend_and_the_highlighter_agree_that_sql_exists():
    from services.common.visual_aids import CODE_LANGS
    assert "sql" in CODE_LANGS
