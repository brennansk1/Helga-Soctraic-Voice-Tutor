#!/usr/bin/env python3
"""degree_quality.py — is this shaped like a real degree programme?

`skeleton_qa.py` grades a single course. A programme fails in ways no course-level
check can see: a capstone in term 1, a prerequisite nobody can satisfy, nine
courses in one term and one in another, or twenty courses that are really the
same subject renamed.

Everything here is arithmetic on the plan. No model, so the verdict cannot drift.

WHAT A REAL PROGRAMME LOOKS LIKE
--------------------------------
Verified against published sources (docs/AI_UNIVERSITY_DESIGN.md):

    associate   60 credits   ~20 courses   4 terms   2 years
    bachelor's  120 credits  ~40 courses   8 terms   4 years

with 4-6 courses per term. From that everything else follows: terms should be
comparable in size, prerequisites must precede dependents, a capstone comes last,
and the major should actually be present rather than implied.

WHAT IT CANNOT DO
-----------------
Judge whether the *courses* are the right ones for the subject. That is a
question about the field, and the honest answer is a published curriculum —
which `source_degree_slots` prefers and labels. This tool checks that whatever
courses were chosen are ARRANGED like a degree.
"""

import argparse
import json
import re
import statistics
import sys

# A programme whose courses are all one subject is a long course, not a degree.
# Real programmes mix general education, the major, and electives.
_MIN_DISTINCT_PREFIXES = 3


def _courses(plan):
    return (plan or {}).get("courses") or []


def check_term_balance(plan, terms=None):
    """Are terms comparable, or does one carry the programme?"""
    cs = _courses(plan)
    terms = terms or (plan or {}).get("terms") or 0
    if not cs or not terms:
        return {"checked": False, "reason": "no courses or terms"}
    counts = [sum(1 for c in cs if c.get("term") == t)
              for t in range(1, terms + 1)]
    mean = statistics.mean(counts) if counts else 0
    spread = (statistics.pstdev(counts) / mean) if mean else 0.0
    return {
        "checked": True,
        "courses_per_term": counts,
        "mean": round(mean, 1),
        "spread": round(spread, 2),
        "empty_terms": sum(1 for n in counts if n == 0),
        # A real full-time load is 4-6 courses; a term of 1 or 9 is not a term.
        "in_band": sum(1 for n in counts if 3 <= n <= 7),
        "ok": spread <= 0.4 and all(n > 0 for n in counts),
    }


def check_prerequisites(plan):
    """Every prerequisite present, and strictly earlier."""
    cs = _courses(plan)
    if not cs:
        return {"checked": False, "reason": "no courses"}
    index = {c["title"].strip().lower(): c for c in cs}
    missing, late = [], []
    edges = 0
    for c in cs:
        for req in (c.get("requires") or []):
            edges += 1
            r = index.get(req.strip().lower())
            if r is None:
                missing.append(f"{c['title']} <- {req}")
            elif r.get("term", 0) >= c.get("term", 0):
                late.append(f"{c['title']} (T{c.get('term')}) <- {req} "
                            f"(T{r.get('term')})")
    return {
        "checked": True,
        "edges": edges,
        "missing": missing[:5],
        "not_earlier": late[:5],
        # A programme with NO edges is not validated, it is unstructured — a real
        # degree has levelled sequences.
        "ok": not missing and not late and edges > 0,
    }


def check_capstone(plan):
    """A capstone belongs at the end."""
    cs = _courses(plan)
    terms = (plan or {}).get("terms") or 0
    caps = [c for c in cs if c.get("slot") == "capstone"]
    if not caps:
        return {"checked": False, "reason": "no capstone slot in this template"}
    misplaced = [c["title"] for c in caps if c.get("term") != terms]
    return {"checked": True, "capstones": len(caps),
            "misplaced": misplaced, "ok": not misplaced}


def check_breadth(plan):
    """Is this a degree, or one subject repeated twenty times?"""
    cs = _courses(plan)
    if len(cs) < 5:
        return {"checked": False, "reason": "too few courses to judge breadth"}
    prefixes = set()
    for c in cs:
        m = re.match(r"^\s*([A-Z]{2,4})\s*[- ]?\s*\d{3}", c.get("title", ""))
        if m:
            prefixes.add(m.group(1).upper())
        else:
            # No catalogue code: fall back to the leading content word.
            words = [w for w in re.findall(r"[A-Za-z]+", c.get("title", ""))
                     if len(w) > 3]
            if words:
                prefixes.add(words[0].lower())
    slots = {c.get("slot") for c in cs}
    return {
        "checked": True,
        "distinct_subjects": len(prefixes),
        "slots_present": sorted(s for s in slots if s),
        "ok": len(prefixes) >= _MIN_DISTINCT_PREFIXES and len(slots) >= 2,
    }


def check_titles(plan):
    """Placeholder names are the degree-level equivalent of a generic title."""
    cs = _courses(plan)
    if not cs:
        return {"checked": False, "reason": "no courses"}
    # "Nursing: gen_ed 1" — the shape produced when nothing filled the slots.
    #
    # The trailing NUMBER is what makes it a placeholder. Without it the pattern
    # matched "NUR 490: Capstone", which is a real course title that a real
    # catalogue would print — a capstone course is genuinely called Capstone.
    # `gen_ed` is matched with or without a number, since the underscore form is
    # an internal slot name that no catalogue would ever use.
    placeholder = [c["title"] for c in cs
                   if re.search(r":\s*(gen_ed\s*\d*"
                                r"|(core|elective|capstone)\s+\d+)\s*$",
                                c.get("title", ""), re.I)]
    return {"checked": True, "courses": len(cs), "placeholder": len(placeholder),
            "examples": placeholder[:4], "ok": not placeholder}


def assess(plan):
    out = {
        "term_balance": check_term_balance(plan),
        "prerequisites": check_prerequisites(plan),
        "capstone": check_capstone(plan),
        "breadth": check_breadth(plan),
        "titles": check_titles(plan),
        "instrument": "arithmetic on the plan (no model)",
    }
    graded = [v for v in out.values() if isinstance(v, dict) and v.get("checked")]
    failed = [k for k, v in out.items()
              if isinstance(v, dict) and v.get("checked") and not v.get("ok")]
    out["failed"] = sorted(failed)
    out["not_run"] = sorted(k for k, v in out.items()
                            if isinstance(v, dict) and not v.get("checked"))
    out["verdict"] = "DEGREE_SHAPED" if not failed else "NOT_DEGREE_SHAPED"
    out["graded"] = len(graded)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plan", required=True, help="path to a plan_degree() JSON")
    p.add_argument("--out")
    a = p.parse_args()
    plan = json.load(open(a.plan))
    r = assess(plan)

    print(f"\n=== degree quality ===   ({r['instrument']})")
    print(f"  {plan.get('subject')} · {plan.get('template')} · "
          f"{len(_courses(plan))} courses · {plan.get('terms')} terms · "
          f"source={plan.get('curriculum_source')}\n")
    tb, pr, cp, br, ti = (r["term_balance"], r["prerequisites"], r["capstone"],
                          r["breadth"], r["titles"])
    if tb.get("checked"):
        print(f"  {'PASS' if tb['ok'] else 'FAIL'} term_balance   "
              f"{tb['courses_per_term']} (spread {tb['spread']})")
    if pr.get("checked"):
        print(f"  {'PASS' if pr['ok'] else 'FAIL'} prerequisites  {pr['edges']} edges"
              + (f", missing {pr['missing']}" if pr['missing'] else "")
              + (f", not earlier {pr['not_earlier']}" if pr['not_earlier'] else ""))
    if cp.get("checked"):
        print(f"  {'PASS' if cp['ok'] else 'FAIL'} capstone       "
              f"{cp['capstones']} capstone(s)"
              + (f", misplaced {cp['misplaced']}" if cp['misplaced'] else " at the end"))
    if br.get("checked"):
        print(f"  {'PASS' if br['ok'] else 'FAIL'} breadth        "
              f"{br['distinct_subjects']} distinct subjects, slots {br['slots_present']}")
    if ti.get("checked"):
        print(f"  {'PASS' if ti['ok'] else 'FAIL'} titles         "
              f"{ti['placeholder']}/{ti['courses']} placeholder"
              + (f" e.g. {ti['examples'][:2]}" if ti['examples'] else ""))
    print(f"\n  VERDICT: {r['verdict']}"
          + (f" — failed: {', '.join(r['failed'])}" if r["failed"] else ""))

    if a.out:
        json.dump(r, open(a.out, "w"), indent=2)
    return 0 if r["verdict"] == "DEGREE_SHAPED" else 1


if __name__ == "__main__":
    sys.exit(main())
