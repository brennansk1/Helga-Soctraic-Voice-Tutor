#!/usr/bin/env python3
"""Run Stage 4 over a course that is already built, and optionally record it.

Stage 4 runs at the END of a build. Every course that existed before it did —
and every course built while it was still a library rather than a stage — has
no verdict, which the UI correctly shows as "not assessed" rather than as a
pass. This runs it after the fact.

    python3 tools/audit_course.py <course_uid> [--json] [--write]

`--write` records the verdict on the course, exactly as a build would, so the
course card and the trust panel show what the audit found. Without it, nothing
is modified.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.core.course_audit import audit_course, walk_concepts

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def load(course_uid):
    root = os.path.join(DATA, "courses", course_uid)
    with open(os.path.join(root, "structure.json"), encoding="utf-8") as f:
        structure = json.load(f)
    contents = {}
    for concept, _ in walk_concepts(structure):
        uid = concept.get("uid")
        path = os.path.join(root, "content", f"{uid}.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                contents[uid] = f.read()
    sources = {}
    db = os.path.join(DATA, "helga.db")
    if os.path.exists(db):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for row in conn.execute(
                    "SELECT concept_uid, title, url, passage, source_type "
                    "FROM sources WHERE course_uid=?", (course_uid,)):
                sources.setdefault(row[0], []).append(
                    {"title": row[1], "url": row[2], "passage": row[3],
                     "type": row[4]})
        except sqlite3.Error:
            pass
        conn.close()
    return structure, contents, sources



def _write_verdict(uid, structure, report):
    """Record the verdict on the course, the way a build would.

    READ, MODIFY, WRITE. structure.json IS the course: writing a small dict
    over it would replace every module in it. That mistake has been made in
    this repo before and cost a 101-concept build its structure.
    """
    import copy
    path = os.path.join(DATA, "courses", uid, "structure.json")
    with open(path, encoding="utf-8") as f:
        current = json.load(f)

    slim = copy.deepcopy(report)
    # The findings list runs to hundreds on a bad course and this rides inside
    # structure.json, which is read on every page load.
    slim["findings"] = slim.get("findings", [])[:60]
    current["audit"] = slim

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    print(f"  recorded audit verdict on {uid}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    uid = sys.argv[1]
    structure, contents, sources = load(uid)
    report = audit_course(
        structure, contents, sources_by_uid=sources,
        mastery=structure.get("mastery") or structure.get("mastery_level"),
        course_title=structure.get("title"),
        # OFF THE COURSE, never re-inferred — re-inference is what made the
        # depth contract demand a named theorem of every SQL concept.
        domain=structure.get("teaching_domain"))
    report["ran"] = True
    _b = report.get("by_severity", {}).get("blocking", 0)
    _s = report.get("by_severity", {}).get("serious", 0)
    report["verdict"] = ("blocking_findings" if _b else
                         "needs_review" if _s else
                         "incomplete" if report.get("concepts_not_audited")
                         else "clean")

    if "--write" in sys.argv:
        _write_verdict(uid, structure, report)

    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
        return 0

    r = report
    print(f"\n{r['course_title']}  —  {r['concepts_total']} concepts")
    print(f"  audited        {r['concepts_audited']}")
    if r["concepts_not_audited"]:
        print(f"  NOT audited    {r['concepts_not_audited']}  (no content file)")
    print(f"  with findings  {r['concepts_with_findings']}")
    print(f"  seconds        {r['seconds']}"
          f"   ({r['seconds'] / max(1, r['concepts_audited']):.3f}s per concept)")

    if r.get("systemic"):
        print("\n  SYSTEMIC — affects most of the course, reported once:")
        for sysf in r["systemic"]:
            print(f"    [{sysf['severity']}] {sysf['check']} — "
                  f"{sysf['concepts']} concepts ({sysf['share']:.0%})")
            print(f"      {sysf['detail'][:150]}")

    print("\n  checks run (concepts each covered):")
    for name, n in sorted(r["checks_run"].items(), key=lambda kv: -kv[1]):
        print(f"    {name:20} {n}")

    if not r["findings"]:
        print("\n  no findings\n")
        return 0

    print(f"\n  findings by severity: {r['by_severity']}")
    print(f"  findings by check:    {r['by_check']}\n")
    order = {"blocking": 0, "serious": 1, "minor": 2}
    shown = sorted(r["findings"], key=lambda f: order.get(f["severity"], 9))
    for f in shown[:40]:
        print(f"  [{f['severity']:8}] {f['check']:18} {f['title'][:44]}")
        print(f"             {f['detail'][:150]}")
        if f["quote"]:
            print(f"             > {f['quote'][:120]}")
    if len(shown) > 40:
        print(f"\n  … {len(shown) - 40} more (use --json)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
