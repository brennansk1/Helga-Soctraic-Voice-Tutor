"""Can a person actually get anywhere from anywhere?

`page_audit.py` checks each page in isolation. This checks the GRAPH: every
internal link on every page is followed, and the result says which
destinations are broken, which pages nothing links to, and which pages offer
no way onward.

Three questions, each answerable as a fact:

  * BROKEN LINK   -- a link on a page whose destination does not load.
  * ORPHAN        -- a page no other page links to. Reachable only if you know
                     the URL, which for a real feature means it is invisible.
  * DEAD END      -- a page with no outbound link at all. Nowhere to go but
                     the browser Back button.

KNOWN FALSE POSITIVE: /palace reports as an orphan and is not one. It is
linked from learn.html, and the crawler cannot reach /learn because /learn
without a ?course_uid redirects to /courses. Memory Palace is a mode inside a
course, not a destination -- check a finding before acting on it.

FOUND ON THE FIRST RUN: /library had NO inbound link from anywhere in the app,
and neither did /setup -- the page whose entire job is to walk somebody
through a machine that will not run Helga was reachable only by typing its
URL, including from the screen that tells them the machine is blocked.

Run: python3 tools/real_app.py & ; python3 tools/flow_audit.py
"""
import json, os, re, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("HELGA_AUDIT_BASE", "http://127.0.0.1:5098")
SEEDS = ["/", "/courses", "/create", "/degree", "/library", "/build",
         "/notebook", "/practice", "/progress", "/settings", "/status",
         "/courses/new", "/setup", "/login", "/signup", "/palace"]
SKIP = re.compile(r"^(mailto:|tel:|javascript:|#|https?://(?!127\.0\.0\.1))")


def norm(href):
    if not href or SKIP.search(href):
        return None
    href = href.replace(BASE, "")
    if not href.startswith("/"):
        return None
    return href.split("#")[0] or "/"


def main():
    graph, status, out_count = {}, {}, {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        seen = set()
        queue = list(SEEDS)
        while queue:
            path = queue.pop(0)
            if path in seen:
                continue
            seen.add(path)
            try:
                r = pg.goto(BASE + path, wait_until="domcontentloaded", timeout=15000)
                status[path] = r.status if r else 0
                pg.wait_for_timeout(900)
                hrefs = pg.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.getAttribute('href'))")
            except Exception as e:
                status[path] = "ERR " + str(e)[:40]
                hrefs = []
            links = sorted({h for h in (norm(x) for x in hrefs) if h})
            graph[path] = links
            out_count[path] = len(links)
            for l in links:
                if l not in seen and l not in queue:
                    queue.append(l)
        b.close()

    inbound = {}
    for src, links in graph.items():
        for l in links:
            inbound.setdefault(l, set()).add(src)

    print("Crawled %d pages\n" % len(graph))

    broken = {p: s for p, s in status.items()
              if not (isinstance(s, int) and 200 <= s < 400)}
    print("BROKEN DESTINATIONS (%d)" % len(broken))
    for p, s in sorted(broken.items()):
        who = sorted(inbound.get(p, []))[:3]
        print(f"   {p}  -> {s}   linked from: {who or 'nothing'}")
    if not broken:
        print("   none")

    print("\nORPHANS -- no page links here (%d)" %
          len([p for p in graph if p not in inbound and p not in ("/",)]))
    for p in sorted(graph):
        if p not in inbound and p != "/":
            print(f"   {p}")

    print("\nDEAD ENDS -- no outbound links (%d)" %
          len([p for p, n in out_count.items() if n == 0]))
    for p, n in sorted(out_count.items()):
        if n == 0:
            print(f"   {p}   (status {status.get(p)})")

    json.dump({"graph": graph, "status": {k: str(v) for k, v in status.items()},
               "inbound": {k: sorted(v) for k, v in inbound.items()}},
              open("/tmp/task0/flow_audit.json", "w"), indent=1)


if __name__ == "__main__":
    main()
