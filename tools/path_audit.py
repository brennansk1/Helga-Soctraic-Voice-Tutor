#!/usr/bin/env python3
"""path_audit.py — pathologies specific to a NODE-BASED learning path.

WHY THIS IS SEPARATE FROM THE DEPTH CONTRACT
--------------------------------------------
The depth contract judges each concept ON ITS OWN: is it rigorous, grounded,
correct? That is necessary but blind to everything that only exists BETWEEN
nodes. A course can be twelve individually excellent cards and still be a bad
path — the same idea taught three times under different names, a term used four
nodes before it is defined, a "concept" that is really half a sentence.

Helga is Duolingo-shaped: short nodes on a path. These are the failure modes
that shape invites, and none of them are visible one card at a time.

WHAT IT CHECKS
  1. REPETITION      near-duplicate concepts (same idea, different title)
  2. UNDER-COVERAGE  concepts that are named but barely taught
  3. FORWARD REFS    a term used well before the node that defines it
  4. CONTEXT         nodes that never connect to what came before
  5. GRANULARITY     nodes far outside the course's own typical size
  6. ORDERING        a concept whose stated prerequisites come after it

All structural — no LLM calls, so it is free and deterministic. Run it on any
course, before or after content generation.

    python3 tools/path_audit.py --course course_xxxx
    python3 tools/path_audit.py            # every course on disk
"""

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

COURSES_DIR = os.path.join(_ROOT, "data", "courses")

_STOP = {
    "the", "and", "for", "with", "from", "into", "that", "this", "what", "how",
    "why", "its", "their", "using", "use", "used", "via", "over", "under",
    "between", "within", "about", "when", "where", "which", "than", "then",
    "these", "those", "each", "both", "your", "you", "are", "was", "were",
    "identify", "understand", "explain", "describe", "recognize", "spot",
    "find", "name", "list", "define", "apply", "compute", "calculate",
}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _walk(structure):
    """Yield (order_index, module, unit, lesson, concept) in path order."""
    i = 0
    for m in structure.get("modules", []) or []:
        for u in m.get("units", []) or []:
            for les in u.get("lessons", []) or []:
                for c in les.get("concepts", []) or []:
                    yield i, m, u, les, c
                    i += 1


def audit(uid):
    cdir = os.path.join(COURSES_DIR, uid)
    spath = os.path.join(cdir, "structure.json")
    if not os.path.exists(spath):
        return {"uid": uid, "error": "no structure.json"}
    with open(spath) as f:
        s = json.load(f)

    nodes = []
    for idx, m, u, les, c in _walk(s):
        body = ""
        p = os.path.join(cdir, "content", f"{c.get('uid')}.md")
        if os.path.exists(p):
            with open(p) as f:
                body = f.read()
        nodes.append({
            "i": idx,
            "uid": c.get("uid"),
            "title": c.get("title") or "",
            "module": m.get("title") or "",
            "lesson": les.get("title") or "",
            "body": body,
            "words": len(body.split()),
            "tokens": _tokens(c.get("title")),
        })
    # NB: a course with zero concepts is NOT an early return. A course that is
    # nothing but empty modules and units is the most broken kind there is, and
    # bailing out with "no concepts" gives the least useful diagnosis exactly
    # when the most useful one is available — namely, WHICH containers are
    # empty. The structural checks below handle an empty node list fine.

    findings = {"repetition": [], "under_covered": [], "forward_refs": [],
                "isolated": [], "granularity": [], "ordering": [],
                "duplicate_body": [], "empty_container": [],
                "dangling_prereq": [], "circular_prereq": [],
                "bloom_regression": [], "bloom_flat": [],
                "module_imbalance": [], "title_mismatch": [],
                "missing_content": [], "exact_duplicate_title": []}

    # 1. REPETITION — near-duplicate titles anywhere in the path.
    for a in range(len(nodes)):
        for b in range(a + 1, len(nodes)):
            sim = _jaccard(nodes[a]["tokens"], nodes[b]["tokens"])
            if sim >= 0.6:
                findings["repetition"].append({
                    "a": nodes[a]["title"], "b": nodes[b]["title"],
                    "similarity": round(sim, 2),
                    "same_lesson": nodes[a]["lesson"] == nodes[b]["lesson"],
                })

    # 2. UNDER-COVERAGE — a node whose own title words barely appear in its body.
    #    Catches a concept that is announced and then not actually taught.
    for n in nodes:
        if not n["words"]:
            continue
        body_tok = _tokens(n["body"])
        covered = len(n["tokens"] & body_tok) / len(n["tokens"]) if n["tokens"] else 1.0
        if covered < 0.5:
            findings["under_covered"].append({
                "title": n["title"], "coverage": round(covered, 2),
                "words": n["words"],
            })

    # 3. FORWARD REFERENCES — a distinctive term from a LATER node's title used
    #    here, before that node defines it. In a linear path that is a
    #    prerequisite inversion the learner feels as "wait, what is that?".
    for n in nodes:
        if not n["words"]:
            continue
        body_tok = _tokens(n["body"])
        ahead = []
        for later in nodes[n["i"] + 1:]:
            distinctive = later["tokens"] - n["tokens"]
            # Require a strong overlap so we flag a real concept reference,
            # not an incidental shared word.
            if distinctive and len(distinctive & body_tok) / len(distinctive) >= 0.7:
                ahead.append(later["title"])
        if ahead:
            findings["forward_refs"].append({
                "title": n["title"], "references_later": ahead[:3],
            })

    # 4. ISOLATION — a node that never references any earlier node's subject.
    #    The first few are exempt: nothing precedes them.
    for n in nodes[2:]:
        if not n["words"]:
            continue
        body_tok = _tokens(n["body"])
        prior = set()
        for earlier in nodes[:n["i"]]:
            prior |= earlier["tokens"]
        if prior and not (prior & body_tok):
            findings["isolated"].append({"title": n["title"]})

    # 5. GRANULARITY — outliers against the course's OWN typical node size,
    #    rather than an absolute number, since level changes the baseline.
    sizes = [n["words"] for n in nodes if n["words"]]
    if len(sizes) > 3:
        med = statistics.median(sizes)
        for n in nodes:
            if not n["words"]:
                continue
            if n["words"] < med * 0.35:
                findings["granularity"].append(
                    {"title": n["title"], "words": n["words"],
                     "median": int(med), "kind": "thin"})
            elif n["words"] > med * 2.2:
                findings["granularity"].append(
                    {"title": n["title"], "words": n["words"],
                     "median": int(med), "kind": "oversized"})

    # 6. ORDERING — declared prerequisites that appear later in the path.
    pos = {n["title"].lower(): n["i"] for n in nodes}
    for idx, m, u, les, c in _walk(s):
        for pre in (c.get("prerequisites") or []):
            j = pos.get(str(pre).lower())
            if j is not None and j > idx:
                findings["ordering"].append(
                    {"title": c.get("title"), "prerequisite": pre})

    # 7. EXACT DUPLICATE TITLES — two nodes the learner cannot tell apart.
    tcount = Counter(n["title"].strip().lower() for n in nodes if n["title"])
    for t, cnt in tcount.items():
        if cnt > 1:
            findings["exact_duplicate_title"].append({"title": t, "count": cnt})

    # 8. DUPLICATE BODY — copy-pasted teaching. Distinct from repetition-by-
    #    title: two differently-named nodes serving identical content is the
    #    worse version, because it looks like progress.
    seen_bodies = {}
    for n in nodes:
        if n["words"] < 30:
            continue
        sig = " ".join(sorted(_tokens(n["body"])))[:400]
        if sig and sig in seen_bodies:
            findings["duplicate_body"].append(
                {"a": seen_bodies[sig], "b": n["title"]})
        elif sig:
            seen_bodies[sig] = n["title"]

    # 9. EMPTY CONTAINERS — a module with no units, a unit with no lessons, a
    #    lesson with no concepts. The learner clicks into nothing.
    for m in s.get("modules", []) or []:
        units = m.get("units") or []
        if not units:
            findings["empty_container"].append(
                {"kind": "module", "title": m.get("title")})
        for u in units:
            lessons = u.get("lessons") or []
            if not lessons:
                findings["empty_container"].append(
                    {"kind": "unit", "title": u.get("title")})
            for les in lessons:
                if not (les.get("concepts") or []):
                    findings["empty_container"].append(
                        {"kind": "lesson", "title": les.get("title")})

    # 10/11. PREREQUISITE GRAPH — dangling (points at nothing) and circular
    #        (A needs B needs A). Both strand the learner.
    titles_lc = {n["title"].strip().lower(): n["title"] for n in nodes}
    prereq_map = {}
    for idx, m, u, les, c in _walk(s):
        t = (c.get("title") or "").strip().lower()
        pres = [str(p).strip().lower() for p in (c.get("prerequisites") or [])]
        prereq_map[t] = pres
        for pre in pres:
            if pre and pre not in titles_lc:
                findings["dangling_prereq"].append(
                    {"title": c.get("title"), "missing": pre})

    def _cycle(node, seen):
        if node in seen:
            return list(seen) + [node]
        for nxt in prereq_map.get(node, []):
            if nxt in prereq_map:
                got = _cycle(nxt, seen | {node})
                if got:
                    return got
        return None

    reported = set()
    for t in prereq_map:
        cyc = _cycle(t, set())
        if cyc:
            key = frozenset(cyc)
            if key not in reported:
                reported.add(key)
                findings["circular_prereq"].append({"cycle": cyc[:4]})

    # 12/13. BLOOM PROGRESSION — a level that goes BACKWARDS mid-course, or is
    #        flat for the whole course (no cognitive ramp at all).
    blooms = [c.get("bloom_level") for _, _, _, _, c in _walk(s)
              if c.get("bloom_level") is not None]
    mod_blooms = [m.get("bloom_target") for m in (s.get("modules") or [])
                  if m.get("bloom_target") is not None]
    for i in range(1, len(mod_blooms)):
        if mod_blooms[i] < mod_blooms[i - 1]:
            findings["bloom_regression"].append(
                {"module_index": i, "from": mod_blooms[i - 1], "to": mod_blooms[i]})
    if len(mod_blooms) > 2 and len(set(mod_blooms)) == 1:
        findings["bloom_flat"].append({"level": mod_blooms[0],
                                       "modules": len(mod_blooms)})

    # 14. MODULE IMBALANCE — one module carrying most of the course while
    #     another is a stub makes the path feel arbitrary.
    per_module = Counter()
    for _, m, _, _, c in _walk(s):
        per_module[m.get("title")] += 1
    if len(per_module) > 1:
        vals = list(per_module.values())
        if max(vals) >= 3 * max(1, min(vals)):
            findings["module_imbalance"].append(
                {"largest": max(vals), "smallest": min(vals),
                 "modules": len(vals)})

    # 15. TITLE/CONTENT MISMATCH — the body's dominant subject is not the
    #     node's subject. Distinct from under-coverage: here it teaches
    #     something, just not the advertised thing.
    for n in nodes:
        if n["words"] < 60 or not n["tokens"]:
            continue
        body_tok = _tokens(n["body"])
        if body_tok and not (n["tokens"] & body_tok):
            findings["title_mismatch"].append(
                {"title": n["title"], "words": n["words"]})

    # 16. MISSING CONTENT — a node on the path with no lesson behind it.
    for n in nodes:
        if not n["words"]:
            findings["missing_content"].append({"title": n["title"]})

    total = len(nodes)
    return {
        "uid": uid,
        "title": s.get("title"),
        "mastery": s.get("mastery"),
        "concepts": total,
        "findings": findings,
        "counts": {k: len(v) for k, v in findings.items()},
        "clean": all(not v for v in findings.values()),
    }


def _report(r):
    if r.get("error"):
        print(f"{r['uid']}: {r['error']}")
        return 1
    print(f"\n{'='*70}")
    print(f"{r['uid']}  |  {r['title']}  (mastery {r['mastery']}, "
          f"{r['concepts']} concepts)")
    labels = {
        "missing_content": "Node on the path with no lesson behind it",
        "empty_container": "Module/unit/lesson the learner clicks into for nothing",
        "exact_duplicate_title": "Two nodes with the same title",
        "duplicate_body": "Different titles, identical teaching (copy-paste)",
        "repetition": "Repeated concepts (same idea, different node)",
        "under_covered": "Announced but barely taught",
        "title_mismatch": "Body teaches something other than the title",
        "forward_refs": "Uses a term defined later in the path",
        "ordering": "Prerequisite appears after the concept",
        "dangling_prereq": "Prerequisite that does not exist in the course",
        "circular_prereq": "Prerequisite cycle (A needs B needs A)",
        "isolated": "Never connects to anything earlier",
        "bloom_regression": "Cognitive level goes BACKWARDS mid-course",
        "bloom_flat": "No cognitive ramp across the whole course",
        "module_imbalance": "One module carries the course, another is a stub",
        "granularity": "Node size far off the course's own norm",
    }
    issues = 0
    for k, label in labels.items():
        items = r["findings"][k]
        if not items:
            print(f"  ok   {label}")
            continue
        issues += len(items)
        print(f"  {len(items):<4} {label}")
        for it in items[:3]:
            if k == "repetition":
                where = "same lesson" if it["same_lesson"] else "different lessons"
                print(f"         '{it['a'][:32]}' ~ '{it['b'][:32]}' "
                      f"({it['similarity']}, {where})")
            elif k == "granularity":
                print(f"         '{it['title'][:40]}' {it['words']}w "
                      f"vs median {it['median']} ({it['kind']})")
            elif k == "forward_refs":
                print(f"         '{it['title'][:32]}' -> "
                      f"{', '.join(x[:24] for x in it['references_later'])}")
            elif k == "ordering":
                print(f"         '{it['title'][:32]}' needs '{it['prerequisite'][:28]}'")
            elif k == "duplicate_body":
                print(f"         '{it['a'][:30]}' == '{it['b'][:30]}'")
            elif k == "empty_container":
                print(f"         {it['kind']}: '{str(it['title'])[:40]}'")
            elif k == "dangling_prereq":
                print(f"         '{it['title'][:30]}' -> missing '{it['missing'][:26]}'")
            elif k == "circular_prereq":
                print(f"         {' -> '.join(x[:18] for x in it['cycle'])}")
            elif k == "bloom_regression":
                print(f"         module {it['module_index']}: "
                      f"{it['from']} -> {it['to']}")
            elif k == "bloom_flat":
                print(f"         every module at level {it['level']}")
            elif k == "module_imbalance":
                print(f"         largest {it['largest']} vs smallest "
                      f"{it['smallest']} concepts")
            elif k == "exact_duplicate_title":
                print(f"         '{it['title'][:40]}' x{it['count']}")
            else:
                print(f"         '{str(it.get('title'))[:48]}'")
        if len(items) > 3:
            print(f"         … and {len(items) - 3} more")
    print(f"\n  PATH: {'CLEAN' if not issues else str(issues) + ' issue(s)'}")
    return 0 if not issues else 1


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course")
    p.add_argument("--json")
    args = p.parse_args()

    uids = ([args.course] if args.course
            else sorted(d for d in os.listdir(COURSES_DIR)
                        if os.path.isdir(os.path.join(COURSES_DIR, d)))
            if os.path.isdir(COURSES_DIR) else [])
    if not uids:
        print("No courses on disk.")
        return 1
    results, rc = [], 0
    for uid in uids:
        r = audit(uid)
        results.append(r)
        rc |= _report(r)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {args.json}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
