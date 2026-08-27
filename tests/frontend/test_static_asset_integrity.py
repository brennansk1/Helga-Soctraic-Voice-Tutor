"""Guards for the failure family this frontend keeps producing: code that is
syntactically fine, runs without a visible crash, and quietly does nothing.

Three real bugs from one afternoon's work motivated each check below:
  * two `function show(...)` declarations in one scope — the later one silently
    replaced the tab switcher, so the Due tab stopped loading;
  * `load()` called in endReview and defined nowhere — a ReferenceError on the
    last line of a handler, so the queue never refreshed and nothing looked wrong;
  * CSS written against `var(--border-default)`, a token that does not exist —
    the declaration is dropped at computed-value time and the borders vanish.

None of these are caught by `node --check`.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "services" / "web-ui" / "static"
JS_DIR = STATIC / "js"
CSS_DIR = STATIC / "css"
TEMPLATES = ROOT / "services" / "web-ui" / "templates"


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"(?<![:/\w])//[^\n]*", " ", text)


def _strip_literals(text: str) -> str:
    """Blank out string contents. Prose in a user-facing string ("2 card(s)
    reviewed") otherwise reads as a function call."""
    text = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", text)
    text = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', text)
    return re.sub(r"`(?:\\.|[^`\\])*`", "``", text)


# Minified third-party bundles are not ours to lint and defeat every heuristic
# here (single-letter names, packed scopes).
VENDOR = re.compile(r"\.min\.js$|^(socket\.io|feather|sortable|chart|marked)\b")


def _is_vendor(path) -> bool:
    return bool(VENDOR.search(path.name))


def _js_files():
    return sorted(p for p in JS_DIR.glob("*.js")
                  if p.stat().st_size and not _is_vendor(p))


def _css_files():
    return sorted(CSS_DIR.glob("*.css"))


@pytest.mark.parametrize("path", _js_files(), ids=lambda p: p.name)
def test_no_duplicate_function_declarations(path):
    """Two `function f()` in one file: the last wins, silently."""
    src = _strip_literals(_strip_comments(
        path.read_text(encoding="utf-8", errors="replace")))
    names = re.findall(r"^\s*function\s+([A-Za-z_$][\w$]*)\s*\(", src, re.M)
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, (
        f"{path.name}: {dupes} declared more than once. In one scope the later "
        f"declaration replaces the earlier with no error at all."
    )


@pytest.mark.parametrize("path", _js_files(), ids=lambda p: p.name)
def test_no_var_shadows_a_function_of_the_same_name(path):
    """`function f(){}` and `var f = ...` in one scope.

    Both hoist, then the assignment overwrites the function — so every call to
    f() throws "f is not a function", but only once that line has run, which is
    usually somewhere far from either declaration. The review session in
    practice.js declared `function current()` while the quiz section below it
    declared `var current = null`, and the whole session died on load.
    """
    src = _strip_literals(_strip_comments(
        path.read_text(encoding="utf-8", errors="replace")))
    # Only declarations at the same nesting level can actually collide. A `var`
    # inside a function body shadows the outer function only within that body,
    # which is a readability hazard but not the runtime failure this guards
    # against — build-view.js has exactly that and is fine.
    funcs = set(re.findall(r"^(\s{0,4})function\s+([A-Za-z_$][\w$]*)\s*\(", src, re.M))
    vars_ = set(re.findall(r"^(\s{0,4})var\s+([A-Za-z_$][\w$]*)\s*=", src, re.M))
    by_name_f = {n for _i, n in funcs}
    by_name_v = {n for _i, n in vars_}
    clash = sorted(by_name_f & by_name_v)
    assert not clash, (
        f"{path.name}: {clash} declared as both a function and a var. The "
        f"assignment wins at runtime and every call to it throws."
    )




# NOTE: an undefined-call check (the `load()` bug) lived here and was removed.
# Deciding whether a bare `name(...)` is defined needs a real JS scope analysis;
# a regex cannot tell a function parameter or a template-literal fragment from a
# call, and it reported six false positives against zero true ones. That gap is
# eslint's `no-undef` to fill, not this file's — better an honest hole than a
# check nobody trusts.


def _root_tokens():
    """Every custom property defined on a :root-ish selector, across all CSS."""
    tokens = set()
    for css in _css_files():
        text = css.read_text(encoding="utf-8", errors="replace")
        for block in re.findall(r"(?::root|^html)[^{]*\{(.*?)\}", text, re.S | re.M):
            tokens |= set(re.findall(r"(--[\w-]+)\s*:", block))
    return tokens


@pytest.mark.parametrize("path", _css_files(), ids=lambda p: p.name)
def test_css_variables_are_defined(path):
    """`var(--x)` with no fallback, where --x is defined nowhere.

    The whole declaration becomes invalid at computed-value time, so the
    property silently reverts to its initial value: borders disappear, radii go
    square, colours fall back to black.
    """
    tokens = _root_tokens()
    text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))

    # Only bare var(--x) — a var(--x, fallback) degrades on purpose.
    used = set(re.findall(r"var\(\s*(--[\w-]+)\s*\)", text))
    # Locally-scoped properties (defined anywhere in this file) are fine too.
    local = set(re.findall(r"(--[\w-]+)\s*:", text))

    missing = sorted(used - tokens - local)
    assert not missing, (
        f"{path.name} uses undefined CSS variables {missing}. The declarations "
        f"using them are dropped entirely by the browser."
    )


def test_every_stylesheet_is_actually_linked():
    """An orphan stylesheet is a trap: edits to it appear to do nothing.

    `practice.css` sat unlinked in static/css while the page it named was styled
    from elsewhere, so a whole block of new CSS landed in a file no page loads.
    """
    linked = set()
    for tpl in TEMPLATES.rglob("*.html"):
        text = tpl.read_text(encoding="utf-8", errors="replace")
        linked |= set(re.findall(r"filename=['\"]css/([\w.-]+\.css)['\"]", text))
        linked |= set(re.findall(r"href=['\"][^'\"]*?/css/([\w.-]+\.css)", text))

    # A stylesheet may also be injected at runtime by its own script, which is
    # a legitimate way to keep a feature to one <script> tag (course-share.js
    # does this). Count those as linked.
    for js in JS_DIR.glob("*.js"):
        text = js.read_text(encoding="utf-8", errors="replace")
        linked |= set(re.findall(r"css/([\w.-]+\.css)", text))

    on_disk = {p.name for p in _css_files()}
    orphans = sorted(on_disk - linked)
    assert not orphans, (
        f"stylesheets present but linked by no template: {orphans}. Either link "
        f"them or delete them — editing an unlinked file has no visible effect."
    )
