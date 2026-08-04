#!/usr/bin/env python3
"""ui_audit.py — find UI state that nothing renders.

WHY THIS EXISTS

Three bugs of the same shape were found by hand in the learn tab alone, and
each one had been shipping silently:

  * `.hidden` was set on five elements; four had no CSS rule, so the progress
    pill, the Bloom badge, the mastery bar and the hero all rendered while the
    markup said they were hidden — and the hero's margins pushed ~90px of dead
    space above the first message of every session.
  * `#mic-btn.recording` was styled for a button that used to have a border and
    a background. After a restyle the rule barely showed, so the microphone
    looked the same whether or not it was recording.
  * `session.js` toggled a `no-audio` class on the pause button that no
    stylesheet answered, so a control that does nothing without live audio sat
    in the composer permanently.

None of these throws, logs, or fails a test. The JS is correct, the CSS is
absent, and the two halves never meet. The only thing they have in common is
that a class is introduced in one language and never answered in the other —
which is exactly what a script can check.

WHAT IT REPORTS

  DEAD    a class set by JS or markup that no stylesheet styles. Usually a
          state the user can reach and never see.
  ORPHAN  a class styled in CSS that nothing ever sets. Usually dead weight
          from a refactor; occasionally a rename that broke a state.

Both are heuristics, not proofs — a class may be styled by a library, applied
by a template loop, or matched by an attribute selector. The output is a
starting list for a human, so it prints WHERE each one comes from.

USAGE
    python3 tools/ui_audit.py                 # dead hooks (the ones that bite)
    python3 tools/ui_audit.py --orphans       # also unused CSS
    python3 tools/ui_audit.py --json
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "services", "web-ui")
CSS_DIR = os.path.join(WEB, "static", "css")
JS_DIR = os.path.join(WEB, "static", "js")
TPL_DIR = os.path.join(WEB, "templates")

# Classes that are structural or come from outside our stylesheets.
IGNORE = {
    "hidden",          # handled specially: reported per-element, not globally
    "active", "show", "open", "selected", "disabled", "loading", "error",
    "sr-only", "d-none", "fa", "fas", "far", "material-icons",
}
IGNORE_PREFIXES = ("katex", "fa-", "swiper", "leaflet")


def _read(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _files(directory, *suffixes):
    out = []
    for base, _dirs, names in os.walk(directory):
        for name in names:
            if name.endswith(suffixes) and ".min." not in name:
                out.append(os.path.join(base, name))
    return sorted(out)


def _stylesheet_texts():
    """Every stylesheet the browser sees — including the <style> blocks inside
    templates. Missing those was the tool's own first bug: this project keeps a
    lot of CSS inline (learn.html alone carries hundreds of rules), so scanning
    only static/css reported 313 false 'dead' classes."""
    for path in _files(CSS_DIR, ".css"):
        yield path, re.sub(r"/\*.*?\*/", "", _read(path), flags=re.DOTALL)
    for path in _files(TPL_DIR, ".html"):
        html = _read(path)
        for block in re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL | re.I):
            yield path, re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)


def css_classes():
    """Every class name that appears in a selector, from every stylesheet."""
    found = set()
    for _path, text in _stylesheet_texts():
        for match in re.finditer(r"\.(-?[_a-zA-Z][\w-]*)", text):
            found.add(match.group(1))
    return found


def js_classes():
    """Classes the running app adds, removes or toggles. Returns {cls: [where]}."""
    out = defaultdict(list)
    patterns = (
        # classList.add('a', 'b')  /  .toggle('a', cond)  /  .remove('a')
        re.compile(r"classList\.(?:add|remove|toggle)\(\s*([^)]*)\)"),
        # className = 'a b'  /  className += ' a'
        re.compile(r"className\s*\+?=\s*([\"'`][^\"'`]*[\"'`])"),
        # class="..." inside a JS template literal building markup
        re.compile(r"class=[\"'`]([^\"'`{]*)[\"'`]"),
    )
    for path in _files(JS_DIR, ".js") + _files(TPL_DIR, ".html"):
        text = _read(path)
        where = os.path.relpath(path, ROOT)
        for pattern in patterns:
            for match in pattern.finditer(text):
                for token in re.findall(r"[\"'`]([^\"'`]+)[\"'`]", match.group(0)):
                    for cls in token.split():
                        cls = cls.strip()
                        if _interesting(cls):
                            out[cls].append(where)
    return out


def _interesting(cls):
    if not cls or len(cls) < 3:
        return False
    if not re.match(r"^-?[_a-zA-Z][\w-]*$", cls):
        return False            # template placeholders, ${expr}, etc.
    if cls in IGNORE or cls.startswith(IGNORE_PREFIXES):
        return False
    return True


HIDDEN_RE = re.compile(r'id="([\w-]+)"[^>]*class="([^"]*\bhidden\b[^"]*)"')


def _has_global_hidden(text):
    """A bare `.hidden { … }` rule — one that hides anything carrying the class,
    not just a specific element."""
    for match in re.finditer(r"(^|[,{}\s])\.hidden\s*[,{]", text, re.MULTILINE):
        # `.foo.hidden` and `#bar.hidden` are element-specific, not global.
        prefix = text[max(0, match.start() - 1):match.start() + 1]
        if not re.search(r"[\w\-\]\)]$", prefix.replace(".hidden", "")):
            return True
    return False


def _pages_with_global_hidden():
    """Which TEMPLATES get a global `.hidden` rule.

    This has to be page-aware, and getting it wrong cost real time: an inline
    <style> block applies only to the template that carries it, so a global
    `.hidden` in learn.html says nothing about settings.html. Treating one
    page's inline rule as project-wide reports zero problems and hides the
    pages that genuinely have none. base.html is the exception — every page
    extends it, so its rules are global.
    """
    external_global = any(_has_global_hidden(t)
                          for p, t in _stylesheet_texts() if p.endswith(".css"))
    base = os.path.join(TPL_DIR, "base.html")
    base_global = os.path.exists(base) and any(
        _has_global_hidden(b) for b in
        re.findall(r"<style[^>]*>(.*?)</style>", _read(base), re.DOTALL | re.I))

    covered = set()
    for path in _files(TPL_DIR, ".html"):
        if external_global or base_global:
            covered.add(path)
            continue
        own = re.findall(r"<style[^>]*>(.*?)</style>", _read(path), re.DOTALL | re.I)
        if any(_has_global_hidden(b) for b in own):
            covered.add(path)
    return covered


def hidden_without_rules(styled):
    """Elements marked `.hidden` on a page that has no rule to honour it.
    Returns [(template, id, classes)]."""
    covered_pages = _pages_with_global_hidden()
    out = []
    for path in _files(TPL_DIR, ".html"):
        if path in covered_pages:
            continue
        text = _read(path)
        for match in HIDDEN_RE.finditer(text):
            element_id, classes = match.group(1), match.group(2)
            others = [c for c in classes.split() if c != "hidden"]
            # Leading dot matters: paired_selectors() stores ".modal.hidden",
            # so comparing "modal.hidden" never matched and every element with
            # a styled co-class was reported as broken.
            covered = any(f".{c}.hidden" in styled for c in others) or \
                f"#{element_id}.hidden" in styled
            if not covered:
                out.append((os.path.relpath(path, ROOT), element_id, classes))
    return out


def paired_selectors():
    """Selector fragments like `.foo.hidden` or `#bar.hidden`, so the
    element-level check above can tell covered from uncovered."""
    found = set()
    for _path, text in _stylesheet_texts():
        for match in re.finditer(r"([#.][\w-]+)\.hidden", text):
            found.add(match.group(0))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orphans", action="store_true", help="also list CSS nothing sets")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()

    styled = css_classes()
    used = js_classes()
    dead = {c: sorted(set(w))[:3] for c, w in used.items() if c not in styled}
    hidden = hidden_without_rules(paired_selectors())
    orphans = sorted(styled - set(used)) if args.orphans else []

    if args.as_json:
        print(json.dumps({"dead": dead, "hidden_unstyled": hidden,
                          "orphans": orphans}, indent=2))
        return 0

    print(f"UI audit · {len(styled)} styled classes · {len(used)} set at runtime\n")

    if hidden:
        print(f"UNSTYLED `.hidden` ({len(hidden)}) — markup says hidden, CSS does not:")
        for tpl, element_id, classes in hidden:
            print(f"   #{element_id:<28} {tpl}")
        print()

    if dead:
        print(f"DEAD CLASS HOOKS ({len(dead)}) — set by the app, styled by nothing:")
        for cls in sorted(dead):
            print(f"   .{cls:<32} {', '.join(dead[cls])}")
        print()

    if orphans:
        print(f"ORPHAN CSS ({len(orphans)}) — styled, never set:")
        for cls in orphans[:60]:
            print(f"   .{cls}")
        if len(orphans) > 60:
            print(f"   … and {len(orphans) - 60} more")
        print()

    if not (hidden or dead):
        print("No dead hooks found.")
    print("Heuristic, not proof: a class may be applied by a template loop or a\n"
          "library. Treat this as a list to check, not a list to delete.")
    return 1 if (hidden or dead) else 0


if __name__ == "__main__":
    sys.exit(main())
