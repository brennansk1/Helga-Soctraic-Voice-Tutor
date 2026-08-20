"""Walk every page and measure what decides whether a person can navigate this.

Structural facts only -- each is a yes/no a reviewer can check, not an opinion
about taste: does the page load, does it have exactly one heading for the
outline, is the nav there, is there a landmark and a skip link, does the
heading order skip a level, does it scroll sideways, does its JavaScript throw.

Found on the first run: /login and /signup answered 403 to a plain visit (so
nobody could sign in), /courses/new threw a SyntaxError from a Jinja block
nested inside another, /status had NO layout CSS at all, and its headings
jumped h1 -> h3.

USAGE
    python3 tools/real_app.py &          # or any host serving the web-ui
    python3 tools/page_audit.py

A COUNT IS NOT A VERDICT. /create reports 5 h1s and is correct: the carousel
marks off-screen pages `inert`, so exactly one is exposed to assistive tech.
Check what a finding means before acting on it.
"""
import json, sys
from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("HELGA_AUDIT_BASE", "http://127.0.0.1:5098")
PAGES = ["/", "/courses", "/courses/new", "/create", "/degree", "/learn",
         "/library", "/build", "/notebook", "/practice", "/progress", "/test",
         "/review", "/schedule", "/quiz", "/settings", "/account", "/status",
         "/palace", "/setup", "/login", "/signup", "/students", "/parent",
         "/course/view", "/concept_details"]

CHECK_JS = r"""
() => {
  const q = s => [...document.querySelectorAll(s)];
  const vis = e => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility !== 'hidden' && c.display !== 'none'; };
  const h1 = q('h1').filter(vis);
  const nav = q('nav a, .nav-link').filter(vis);
  const focusable = q('a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])').filter(vis);
  // headings in order -- a jump from h1 to h3 breaks screen-reader outlines
  const levels = q('h1,h2,h3,h4,h5,h6').filter(vis).map(e => +e.tagName[1]);
  let skips = 0;
  for (let i = 1; i < levels.length; i++) if (levels[i] - levels[i-1] > 1) skips++;
  // the largest visible interactive thing above the fold = likely primary action
  const above = focusable.filter(e => e.getBoundingClientRect().top < window.innerHeight);
  const d = document.documentElement;
  return {
    title: document.title,
    h1: h1.length, h1text: h1.length ? h1[0].textContent.trim().slice(0, 48) : null,
    navLinks: nav.length,
    hasSkipLink: q('a').some(a => /skip to/i.test(a.textContent||'')),
    hasMain: q('main,[role=main]').length > 0,
    headingSkips: skips,
    focusables: focusable.length,
    focusablesAboveFold: above.length,
    horizOverflow: d.scrollWidth > d.clientWidth ? (d.scrollWidth + '>' + d.clientWidth) : null,
    scrollHeight: d.scrollHeight,
    // any visible element whose text is an unhandled error / raw template
    rawJinja: /\{\{|\{%/.test(document.body.innerText),
    bodyLen: document.body.innerText.trim().length,
    firstText: document.body.innerText.trim().slice(0, 90).replace(/\s+/g, ' ')
  };
}
"""

def run():
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for path in PAGES:
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            errs, con = [], []
            pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
            pg.on("console", lambda m: con.append(m.type) if m.type == "error" else None)
            rec = {"path": path}
            try:
                resp = pg.goto(BASE + path, wait_until="domcontentloaded", timeout=20000)
                rec["status"] = resp.status if resp else None
                pg.wait_for_timeout(1600)
                rec.update(pg.evaluate(CHECK_JS))
                rec["finalUrl"] = pg.url.replace(BASE, "") or "/"
            except Exception as e:
                rec["error"] = str(e)[:120]
            rec["pageErrors"] = errs[:3]
            rec["consoleErrors"] = len(con)
            out.append(rec)
            pg.close()
        b.close()
    return out

if __name__ == "__main__":
    res = run()
    json.dump(res, open("/tmp/task0/ux_audit.json", "w"), indent=1)
    print(f"{'path':<18}{'st':<5}{'h1':<4}{'nav':<5}{'main':<6}{'skipL':<7}"
          f"{'hSkip':<7}{'ovf':<6}{'jsErr':<7}redirect")
    for r in res:
        red = "" if r.get("finalUrl") in (r["path"], None) else "-> " + str(r.get("finalUrl"))
        print(f"{r['path']:<18}{str(r.get('status')):<5}{str(r.get('h1')):<4}"
              f"{str(r.get('navLinks')):<5}{str(r.get('hasMain')):<6}"
              f"{str(r.get('hasSkipLink')):<7}{str(r.get('headingSkips')):<7}"
              f"{str(r.get('horizOverflow') or '-'):<6}"
              f"{str(len(r.get('pageErrors') or [])):<7}{red}")
