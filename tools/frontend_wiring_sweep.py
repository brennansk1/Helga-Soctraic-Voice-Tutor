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

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(
            {"broken_calls": bad_calls, "dead_controls": dead,
             "missing_elements": missing}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
