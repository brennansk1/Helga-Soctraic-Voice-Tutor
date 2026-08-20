#!/usr/bin/env python3
"""Find frontend controls that do nothing, and frontend calls that go nowhere.

`docs/BACKEND_FRONTEND_SWEEP.md` swept one direction: backend routes with no
caller. This sweeps the other three, which are the ones a USER notices:

  A. fetch()/form action -> a route that does not exist. The button appears to
     work, spins, and fails. This is the worst kind because it looks wired.
  B. A control in a template whose id is never referenced by any JS and which
     carries no inline handler and no href. It renders, invites a click, and
     does nothing at all.
  C. JS reaching for an element id that no template defines. Usually the
     remains of a control that was removed, and usually a silent null
     dereference on the line after.
  D. A call to a function that no script loaded by that page defines. This is
     the worst of the four and the reason this check exists: session.js called
     five functions living only in session-course-creation.js, which no
     template loaded, so every STRUCT:/LOG:/CHECK:/ERROR: status message threw
     ReferenceError and abandoned the rest of the handler. A missing element
     yields null and the next line usually guards it; a missing FUNCTION
     throws, and everything after it in that handler never runs.

A regex finds CANDIDATES, not truth -- ids are built dynamically, handlers are
delegated from a parent, routes are registered in blueprints. So everything
here is reported as something to look at, the known-dynamic patterns are
excluded, and the exit code is 0. It is a lead generator, not a gate.

Run: python3 tools/frontend_wiring_sweep.py [--json out.json]
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "services/web-ui/templates"
JS = ROOT / "services/web-ui/static/js"
# Every module that registers routes. Missing one does not under-report --
# it INVENTS broken calls, which is worse: the first run of this sweep
# reported 10 dead endpoints that were all real, just declared on blueprints
# it had never read.
APPS = sorted((ROOT / "services/web-ui").glob("*.py"))


def _read(p):
    try:
        return p.read_text()
    except Exception:
        return ""


def _strip_js_comments(t):
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", t)


def _strip_html_comments(t):
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    return re.sub(r"\{#.*?#\}", "", t, flags=re.S)


# ----------------------------------------------------------------- routes
def routes():
    """Every URL rule, with its blueprint's url_prefix applied."""
    out = set()
    for f in APPS:
        text = _read(f)
        # Blueprint("parent", __name__, url_prefix="/parent") -> the decorator
        # name that carries that prefix.
        prefixes = {}
        for m in re.finditer(
                r"""(\w+)\s*=\s*Blueprint\([^)]*?url_prefix\s*=\s*["']([^"']+)["']""",
                text, re.S):
            prefixes[m.group(1)] = m.group(2).rstrip("/")
        for m in re.finditer(
                r"""@(\w+)\.route\(\s*["']([^"']+)["']""", text):
            deco, rule = m.group(1), m.group(2)
            out.add(prefixes.get(deco, "") + rule)
    return out


def _route_matches(url, known):
    """Compare a called URL against Flask rules, allowing <converters>."""
    url = url.split("?")[0].rstrip("/") or "/"
    for r in known:
        rr = r.rstrip("/") or "/"
        if rr == url:
            return True
        # /api/program/<uid> -> ^/api/program/[^/]+$
        pat = re.sub(r"<[^>]+>", r"[^/]+", re.escape(rr).replace(r"\<", "<").replace(r"\>", ">"))
        pat = re.sub(r"<[^>]+>", r"[^/]+", pat)
        if re.fullmatch(pat, url):
            return True
    return False



def _prefix_matches(prefix, known):
    """Does any rule begin with this literal prefix? For concatenated URLs."""
    base = prefix.split("?")[0]
    for r in known:
        if r.startswith(base):
            return True
        # /api/library/detail?source=  vs rule /api/library/detail
        if base.rstrip("/") == r.rstrip("/"):
            return True
    return False


# Template-literal and concatenated URLs cannot be resolved statically; treat a
# prefix match as wired rather than inventing a failure.
_DYNAMIC = re.compile(r"[$`+]|\{\{|\{%")


def broken_calls(known):
    hits = []
    for f in sorted(list(JS.glob("*.js")) + list(TPL.rglob("*.html"))):
        text = _strip_js_comments(_strip_html_comments(_read(f)))
        seen = set()
        for m in re.finditer(
                r"""fetch\(\s*[`'"]([^`'"]+)[`'"](\s*\+)?""", text):
            url, concat = m.group(1), bool(m.group(2))
            if not url.startswith("/") or url in seen:
                continue
            seen.add(url)
            if _DYNAMIC.search(url):
                continue
            # '/api/aid/' + id  ->  the literal is a PREFIX of the real path,
            # so a rule that starts with it (and takes a segment) is a match.
            if concat or url.endswith(("/", "=")):
                if _prefix_matches(url, known):
                    continue
                hits.append((f.name, url + "<...>", "fetch"))
                continue
            if not _route_matches(url, known):
                hits.append((f.name, url, "fetch"))
        for m in re.finditer(r"""<form[^>]*action\s*=\s*["']([^"']+)["']""", text):
            url = m.group(1)
            if url.startswith("/") and not _DYNAMIC.search(url) \
                    and not _route_matches(url, known) and url not in seen:
                seen.add(url)
                hits.append((f.name, url, "form action"))
    return hits


# ------------------------------------------------------- dead controls
_INTERACTIVE = re.compile(
    r"<(button|a|select|input)\b([^>]*)>", re.I | re.S)


def _attr(tag, name):
    m = re.search(name + r"""\s*=\s*["']([^"']*)["']""", tag, re.I)
    return m.group(1) if m else None


def dead_controls(js_text, all_tpl_text):
    """Controls with an id that nothing ever references, and no other wiring."""
    hits = []
    for f in sorted(TPL.rglob("*.html")):
        text = _strip_html_comments(_read(f))
        for m in _INTERACTIVE.finditer(text):
            tag_name, attrs = m.group(1).lower(), m.group(2)
            whole = m.group(0)
            cid = _attr(attrs, "id")
            # Jinja ({{ }}) and JS template literals (${ }) both build the id
            # at runtime, so "nothing references it" says nothing.
            if not cid or "{{" in cid or "{%" in cid or "${" in cid:
                continue
            # Anything that is wired by another mechanism is not dead.
            if re.search(r"\bon[a-z]+\s*=", attrs, re.I):
                continue
            if tag_name == "a" and _attr(attrs, "href") not in (None, "#", ""):
                continue
            if tag_name in ("input", "select") and _attr(attrs, "form"):
                continue
            if _attr(attrs, "type") in ("submit", "reset") or \
                    _attr(attrs, "data-action") or _attr(attrs, "data-target"):
                continue
            # Referenced anywhere by id, in JS or inline script?
            if re.search(r"""["'#]%s\b""" % re.escape(cid), js_text):
                continue
            if re.search(r"""["'#]%s\b""" % re.escape(cid), all_tpl_text):
                continue
            # A label pointing at it means it is a real form field.
            if re.search(r"""for\s*=\s*["']%s["']""" % re.escape(cid), all_tpl_text):
                continue
            label = re.sub(r"<[^>]+>", " ", whole)
            hits.append((f.name, cid, tag_name, " ".join(label.split())[:60]))
    return hits


# ------------------------------------------------- JS reaching for nothing
_GET_BY_ID = re.compile(r"""getElementById\(\s*["']([^"']+)["']""")
_QUERY_ID = re.compile(r"""querySelector(?:All)?\(\s*["']#([\w-]+)["']""")


def missing_elements(all_tpl_text, js_files):
    hits = []
    for f in js_files:
        text = _strip_js_comments(_read(f))
        seen = set()
        for rx in (_GET_BY_ID, _QUERY_ID):
            for m in rx.finditer(text):
                eid = m.group(1)
                if eid in seen or "$" in eid or "+" in eid:
                    continue
                seen.add(eid)
                # Defined in a template, or created by JS itself?
                if re.search(r"""id\s*=\s*["']%s["']""" % re.escape(eid),
                             all_tpl_text):
                    continue
                if re.search(r"""\.id\s*=\s*["']%s["']""" % re.escape(eid), text):
                    continue
                if re.search(r"""id=["']%s["']""" % re.escape(eid), text):
                    continue
                hits.append((f.name, eid))
    return hits




_VENDOR = re.compile(r"\.min\.js$|^(socket\.io|feather|chart|marked|purify)")


def _is_vendor(path):
    """Minified or third-party. Its internals are not our wiring."""
    if _VENDOR.search(path.name):
        return True
    text = _read(path)
    # A bundle betrays itself by line length long before anything else.
    longest = max((len(l) for l in text.splitlines()), default=0)
    return longest > 500


# No string-stripping here on purpose. A JS tokenizer good enough to survive
# regex literals (/['"]/ opens a quote it never closes) is a project of its
# own, and the first two attempts silently cut session.js from 59,538
# characters to 8,709 -- reporting a clean bill of health on a file they had
# effectively deleted. Precision comes instead from the rule below: the name
# must be a function some project script actually DEFINES. No CSS string in a
# template literal contains "addProgressLog(", so literals cost nothing.


# ------------------------------------------- calls to functions nothing defines
# Only names that look like project functions are considered: anything defined
# by any script, any browser built-in, and any method call (x.foo()) is out of
# scope. The goal is a name that WILL throw, not a list of globals.
_DEF = re.compile(r"function\s+([A-Za-z_$][\w$]*)\s*\(")
_ASSIGNED = re.compile(r"(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=")
_WINDOW_DEF = re.compile(r"window\.([A-Za-z_$][\w$]*)\s*=")
# Not preceded by a dot (method call), an identifier char, or a QUOTE.
# The quote is what keeps SVG out of it: aids.js builds
# "transform: 'translate(14,...)'" and build-view.js happens to define a
# function called translate(), which is a coincidence, not a call.
_CALL = re.compile(r"(?<![.\w$\"'`])([a-z_$][\w$]*)\s*\(")

_BUILTINS = {
    "if", "for", "while", "switch", "catch", "return", "typeof", "function",
    "new", "delete", "void", "in", "of", "do", "else", "try", "throw", "case",
    "await", "yield", "with", "super", "this",
    "fetch", "alert", "confirm", "prompt", "parseInt", "parseFloat", "isNaN",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval", "encodeURI",
    "encodeURIComponent", "decodeURI", "decodeURIComponent", "require",
    "structuredClone", "queueMicrotask", "btoa", "atob", "escape", "unescape",
    "io", "feather", "getComputedStyle", "requestAnimationFrame",
    "cancelAnimationFrame", "matchMedia", "scrollTo", "print", "open", "close",
    "postMessage", "addEventListener", "removeEventListener", "reject",
    "resolve", "next", "done", "test", "exec", "then", "catch",
}


def _scripts_for_page(tpl_path, js_dir):
    """Which .js files a template pulls in, following its base template."""
    names, seen = set(), set()
    stack = [tpl_path]
    while stack:
        f = stack.pop()
        if f in seen or not f.exists():
            continue
        seen.add(f)
        text = _read(f)
        names |= set(re.findall(r"filename\s*=\s*['\"]js/([\w.-]+\.js)", text))
        names |= set(re.findall(r"src\s*=\s*['\"][^'\"]*?/js/([\w.-]+\.js)", text))
        for m in re.finditer(r"""\{%\s*extends\s+['\"]([^'\"]+)['\"]""", text):
            stack.append(f.parent / m.group(1))
            stack.append(TPL / m.group(1))
    return {js_dir / n for n in names}


def undefined_calls():
    """Names that ARE project functions, called on a page that never loads them.

    Precision comes from that second clause. Reporting every name a file calls
    and cannot see produces 601 hits -- minified vendor bundles calling their
    own internals, and `rgba(`/`calc(`/`var(` inside CSS strings. Requiring the
    name to be DEFINED in some project script and merely absent from this
    page's script set is exactly the shape of the real bug (session.js calling
    addProgressLog, defined only in a file no template loads) and almost
    nothing else.
    """
    project = [f for f in sorted(JS.glob("*.js")) if not _is_vendor(f)]
    defined_where = {}
    for f in project:
        text = _strip_js_comments(_read(f))
        for name in set(_DEF.findall(text)) | set(_WINDOW_DEF.findall(text)):
            defined_where.setdefault(name, set()).add(f.name)

    hits = []
    for tpl in sorted(TPL.rglob("*.html")):
        files = {f for f in _scripts_for_page(tpl, JS) if f.exists()}
        loaded = {f for f in files if not _is_vendor(f)}
        if not loaded:
            continue
        inline = _strip_js_comments(_strip_html_comments(_read(tpl)))
        here = set(_DEF.findall(inline)) | set(_WINDOW_DEF.findall(inline))
        for f in loaded:
            here |= set(_DEF.findall(_strip_js_comments(_read(f))))
            here |= set(_WINDOW_DEF.findall(_strip_js_comments(_read(f))))
        for f in sorted(loaded):
            text = _strip_js_comments(_read(f))
            for name in sorted(set(_CALL.findall(text))):
                if name in here or name in _BUILTINS or len(name) < 3:
                    continue
                owners = defined_where.get(name)
                if not owners:
                    continue          # not a project function; out of scope
                hits.append((tpl.name, f.name, name, ", ".join(sorted(owners))))
    return sorted(set(hits))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()

    known = routes()
    js_files = sorted(JS.glob("*.js"))
    js_text = "\n".join(_strip_js_comments(_read(f)) for f in js_files)
    all_tpl_text = "\n".join(_read(f) for f in TPL.rglob("*.html"))

    bad_calls = broken_calls(known)
    dead = dead_controls(js_text, all_tpl_text)
    missing = missing_elements(all_tpl_text, js_files)

    print("Frontend wiring sweep — %d routes, %d templates, %d scripts\n"
          % (len(known), len(list(TPL.rglob("*.html"))), len(js_files)))

    print("A. Calls to a route that does not exist  (%d)" % len(bad_calls))
    for f, url, kind in bad_calls:
        print(f"   {f}: {kind} {url}")
    if not bad_calls:
        print("   none")

    print("\nB. Controls nothing references  (%d)" % len(dead))
    for f, cid, tag, label in dead:
        print(f"   {f}: <{tag} id=\"{cid}\">  {label!r}")
    if not dead:
        print("   none")

    print("\nC. JS reaching for an element no template defines  (%d)"
          % len(missing))
    for f, eid in missing:
        print(f"   {f}: #{eid}")
    if not missing:
        print("   none")

    undef = undefined_calls()
    print("\nD. Calls to a function the page never loads  (%d)" % len(undef))
    for tpl, f, name, owner in undef:
        print(f"   {tpl}: {f} calls {name}() -- defined in {owner}, "
              f"which this page does not load")
    if not undef:
        print("   none")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(
            {"broken_calls": bad_calls, "dead_controls": dead,
             "missing_elements": missing, "undefined_calls": undef}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
