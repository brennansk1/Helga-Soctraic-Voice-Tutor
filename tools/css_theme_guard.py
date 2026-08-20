#!/usr/bin/env python3
"""Catch the three ways a stylesheet can look correct in light mode by accident.

All three bugs shipped in this project already:

  1. An undefined custom property. `var(--surface-raised, #fff)` renders white
     in BOTH themes because the token never existed -- the fallback is not a
     safety net, it is a light-mode constant. design-system.css says it in its
     own header: a token must be defined for both themes or it is not a token.

  2. A <button> used as a card with no `color`. Unlike a <div>, a button does
     not inherit page colour; the UA sets ButtonText (black). Black on white
     looks deliberate, black on #232e28 is unreadable.

  3. A text token that is simply too pale for the surfaces it is printed on.
     --text-secondary was #6b7c6e for the life of the project and measured
     3.92:1 on --bg-primary -- every muted caption in the product, 218 call
     sites across 16 stylesheets, sat under AA in the light theme and nothing
     said so. Eyeballing cannot catch this; a contrast ratio is arithmetic, so
     the guard does the arithmetic.

Precision matters more than reach here: a guard that cries wolf gets muted.
Only classes genuinely rendered on a <button> are checked, and a token counts
as defined if anything sets it, including JS at runtime.

Run: python3 tools/css_theme_guard.py
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS = ROOT / "services/web-ui/static/css"
TPL = ROOT / "services/web-ui/templates"
JS = ROOT / "services/web-ui/static/js"

# ---------------------------------------------------------------- tokens
defined = set()
for f in CSS.glob("*.css"):
    defined |= set(re.findall(r"^\s*(--[\w-]+)\s*:", f.read_text(), re.M))
# A token may legitimately be supplied at runtime or inline on an element.
for f in list(JS.glob("*.js")) + list(TPL.glob("*.html")):
    t = f.read_text()
    defined |= set(re.findall(r"setProperty\(\s*['\"](--[\w-]+)", t))
    defined |= set(re.findall(r"style\s*=\s*[\"'][^\"']*?(--[\w-]+)\s*:", t))
    defined |= set(re.findall(r"@property\s+(--[\w-]+)", t))
for f in CSS.glob("*.css"):
    defined |= set(re.findall(r"@property\s+(--[\w-]+)", f.read_text()))

# ------------------------------------------------- classes used on <button>
# Only buttons that actually CONTAIN TEXT can suffer the black-text bug. A dot,
# a hamburger, an icon-only control has nothing to render in ButtonText, and
# flagging them is how a guard earns a permanent skip.
button_classes = set()
for f in list(TPL.glob("*.html")) + list(JS.glob("*.js")):
    text = f.read_text()
    for m in re.finditer(r"<button\b[^>]*?class\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</button>",
                         text, re.S):
        inner = re.sub(r"<[^>]+>", "", m.group(2))
        inner = re.sub(r"\{[%{].*?[%}]\}", "", inner, flags=re.S)
        if re.search(r"\w", inner):
            button_classes |= set(m.group(1).split())
    # buttons built in JS: el.className = "..." near createElement("button"),
    # counted only when the same block also gives the button real text.
    for m in re.finditer(r"createElement\(\s*[\"']button[\"']\s*\)([\s\S]{0,400}?)"
                         r"appendChild", text):
        block = m.group(1)
        cm = re.search(r"className\s*=\s*[\"']([^\"']+)", block)
        if cm and re.search(r"(textContent|innerText)\s*=\s*[^;\n]*\w", block):
            button_classes |= set(cm.group(1).split())

failures = []
for f in sorted(CSS.glob("*.css")):
    text = f.read_text()
    for token in sorted(set(re.findall(r"var\(\s*(--[\w-]+)", text))):
        if token not in defined:
            failures.append(f"{f.name}: var({token}) is never defined anywhere -- "
                            f"its fallback is a light-mode constant")

    body_only = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body_only):
        sel, body = m.group(1).strip(), m.group(2)
        if re.search(r":(hover|focus|active|disabled|checked|not)", sel):
            continue
        # The bug is on the button itself, never on a descendant or a
        # pseudo-element -- those inherit from the button and are fine.
        last = re.split(r"[\s>+~]+", sel.strip())[-1]
        if "::" in last:
            continue
        classes = set(re.findall(r"\.([\w-]+)", last))
        if not (classes & button_classes):
            continue
        if not re.search(r"background(-color)?\s*:", body):
            continue
        if re.search(r"(^|;)\s*color\s*:", body):
            continue
        hit = ", ".join(sorted(classes & button_classes))
        failures.append(f"{f.name}: `{sel.splitlines()[-1].strip()}` styles a "
                        f"<button> (.{hit}) with a background but no color -- "
                        f"it renders UA black in dark mode")

# ------------------------------------------------------------- contrast
# Resolve the token table per theme, then measure. Light is :root plus any
# [data-theme="light"] override; dark is that table with [data-theme="dark"]
# applied on top. Both selectors carry the same specificity, so within a file
# source order decides -- which is the order we read them in.
_THEME_SEL = re.compile(r'(:root|\[data-theme\s*=\s*"(light|dark)"\])\s*\{([^{}]*)\}')
_CSS_ORDER = ["style.css", "icons.css", "design-system.css"]

def _tables():
    light, dark_over = {}, {}
    files = [CSS / n for n in _CSS_ORDER if (CSS / n).exists()]
    files += [f for f in sorted(CSS.glob("*.css")) if f.name not in _CSS_ORDER]
    for f in files:
        body = re.sub(r"/\*.*?\*/", "", f.read_text(), flags=re.S)
        for m in _THEME_SEL.finditer(body):
            which = m.group(2) or "light"
            target = dark_over if which == "dark" else light
            for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(3)):
                target[k] = v.strip()
    dark = dict(light); dark.update(dark_over)
    return light, dark

def _hex(table, name, depth=0):
    """Resolve a token to (r,g,b), following one var() indirection at a time."""
    v = table.get(name)
    if v is None or depth > 4:
        return None
    v = v.strip()
    ref = re.match(r"var\(\s*(--[\w-]+)", v)
    if ref:
        return _hex(table, ref.group(1), depth + 1)
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", v)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"#([0-9a-fA-F]{3})", v)
    if m:
        h = m.group(1)
        return tuple(int(c * 2, 16) for c in h)
    m = re.match(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)", v)
    if m:
        return tuple(int(float(g)) for g in m.groups())
    return None  # gradients, colour functions and keywords are not measurable

def _ratio(a, b):
    def rel(c):
        f = []
        for ch in c:
            ch /= 255.0
            f.append(ch / 12.92 if ch <= 0.03928 else ((ch + 0.055) / 1.055) ** 2.4)
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    l1, l2 = rel(a), rel(b)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)

# Body-copy tokens against the surfaces they are actually printed on. Deliberately
# NOT every text x every background: --text-inverted and --on-accent exist to sit
# on an accent fill and would fail against a page surface by design, so they are
# measured against the accent instead.
_BODY = ["--text-primary", "--text-secondary"]
_SURFACES = ["--bg-primary", "--bg-secondary", "--bg-tertiary", "--bg-chat"]
_ON_ACCENT = [("--on-accent", "--accent-primary")]
AA = 4.5

for _theme_name, _table in zip(("light", "dark"), _tables()):
    for _t in _BODY:
        for _bg in _SURFACES:
            fg, bgc = _hex(_table, _t), _hex(_table, _bg)
            if not fg or not bgc:
                continue
            r = _ratio(fg, bgc)
            if r < AA:
                failures.append(
                    f"contrast[{_theme_name}]: var({_t}) on var({_bg}) is "
                    f"{r:.2f}:1, under AA {AA} -- body text at this pairing is "
                    f"not readable")
    for _t, _bg in _ON_ACCENT:
        fg, bgc = _hex(_table, _t), _hex(_table, _bg)
        if fg and bgc:
            r = _ratio(fg, bgc)
            if r < AA:
                failures.append(
                    f"contrast[{_theme_name}]: var({_t}) on var({_bg}) is "
                    f"{r:.2f}:1, under AA {AA}")


if failures:
    print("CSS THEME GUARD: %d problem(s)\n" % len(failures))
    for x in failures:
        print("  " + x)
    sys.exit(1)
print("CSS theme guard: clean -- %d tokens defined, %d button classes checked, "
      "contrast measured in both themes"
      % (len(defined), len(button_classes)))
