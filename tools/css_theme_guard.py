#!/usr/bin/env python3
"""Catch the two ways a stylesheet can look correct in light mode by accident.

Both bugs shipped in this project already:

  1. An undefined custom property. `var(--surface-raised, #fff)` renders white
     in BOTH themes because the token never existed -- the fallback is not a
     safety net, it is a light-mode constant. design-system.css says it in its
     own header: a token must be defined for both themes or it is not a token.

  2. A <button> used as a card with no `color`. Unlike a <div>, a button does
     not inherit page colour; the UA sets ButtonText (black). Black on white
     looks deliberate, black on #232e28 is unreadable.

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

if failures:
    print("CSS THEME GUARD: %d problem(s)\n" % len(failures))
    for x in failures:
        print("  " + x)
    sys.exit(1)
print("CSS theme guard: clean -- %d tokens defined, %d button classes checked"
      % (len(defined), len(button_classes)))
