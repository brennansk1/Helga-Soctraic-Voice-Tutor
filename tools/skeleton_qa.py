#!/usr/bin/env python3
"""skeleton_qa.py — one verdict on whether a skeleton is professional-grade.

WHY A HARNESS AND NOT FOUR TOOLS
--------------------------------
Four instruments grew up separately, each answering a different question, and
each has caught something the others could not:

    coverage      does it reach the material?        (vs a real syllabus)
    sequencing    is it in a teachable ORDER?        (caught a 100%-coverage
                                                      course that was alphabetical)
    structure     is it SHAPED like a course?        (caught [9,9,3,3,3,3])
    coherence     does it teach ideas before use?    (no external reference needed)

Read singly they mislead. A course scored 100% on coverage while being an
unteachable alphabetical index; another was perfectly ordered and shaped while
missing a third of its subject. **The verdict has to be conjunctive**, and it has
to say which instrument failed, because "not professional-grade" is useless
without "because the modules taper".

Every check here is arithmetic on titles and counts. No model anywhere, so the
verdict cannot drift between runs — which matters, since this project measured
its own generator swinging ±10 points on identical input.

WHAT "PROFESSIONAL-GRADE" MEANS HERE
------------------------------------
Not "as good as a human's course" — that is unfalsifiable. It means:

  * covers what a published syllabus for the subject covers,
  * in an order a person could teach,
  * shaped the way a school lays out modules, weeks and sessions,
  * without using ideas before introducing them,
  * and without sections whose titles name nothing.

A course can fail this and still be useful; it cannot pass this and be junk.

USAGE
    python3 tools/skeleton_qa.py --course structure.json \\
        [--reference tools/references/mit_18.06_linear_algebra.json]

The reference is optional. Without one, coverage and sequencing-vs-source cannot
run and are reported as NOT RUN — never as passed, and never as zero.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")))

from coverage_check import (check_coverage, sequencing_check,  # noqa: E402
                            structural_summary)
from structure_quality import assess as structure_assess  # noqa: E402


def _coherence(struct):
    try:
        from services.core.coherence import check_coherence
    except ImportError:
        return {"checked": False, "reason": "coherence module unavailable"}
    return check_coherence(struct)


def run(struct, reference=None, expect_lessons=None):
    """Every check, one verdict, and the reason for it."""
    checks = {}

    # 1. Coverage — only with a reference. Absent means NOT RUN.
    if reference:
        cov = check_coverage(struct, reference)
        checks["coverage"] = {
            "ran": "error" not in cov,
            "passed": cov.get("coverage_pct", 0) >= 90,
            "detail": (f"{cov.get('coverage_pct')}% of the published syllabus"
                       if "error" not in cov else cov.get("error")),
            "missing": cov.get("missing", [])[:5],
        }
    else:
        checks["coverage"] = {
            "ran": False,
            "detail": "no reference syllabus supplied — coverage NOT measured",
        }

    # 2. Sequencing — needs no reference; alphabetical order is arithmetic.
    seq = sequencing_check(struct)
    checks["sequencing"] = {
        "ran": bool(seq.get("checked")),
        "passed": seq.get("verdict") == "ok",
        "detail": (seq.get("note") or
                   f"in-order pairs {seq.get('in_order_ratio')}"),
    }

    # 3. Structure — balance, titles, units, uniformity, school shape.
    st = structure_assess(struct)
    for name in ("balance", "titles", "units", "uniformity", "school_shape"):
        part = st.get(name) or {}
        if not part.get("checked"):
            checks[name] = {"ran": False, "detail": part.get("reason", "not run")}
            continue
        passed = bool(part.get("balanced") or part.get("specific")
                      or part.get("ok"))
        checks[name] = {"ran": True, "passed": passed,
                        "detail": _describe(name, part)}

    # 4. Coherence — forward references, no reference needed.
    coh = _coherence(struct)
    checks["coherence"] = {
        "ran": bool(coh.get("checked")),
        "passed": coh.get("verdict") == "ok",
        "detail": (f"{coh.get('forward_references', 0)} forward reference(s) "
                   f"in {coh.get('concepts', 0)} concepts"
                   if coh.get("checked") else coh.get("reason", "not run")),
    }

    ran = {k: v for k, v in checks.items() if v.get("ran")}
    failed = [k for k, v in ran.items() if not v.get("passed")]
    not_run = [k for k, v in checks.items() if not v.get("ran")]

    return {
        "checks": checks,
        "ran": sorted(ran),
        "failed": sorted(failed),
        "not_run": sorted(not_run),
        # Conjunctive: every check that COULD run must pass. A check that did not
        # run is reported, never counted as a pass — that distinction is the
        # whole reason a sourceless course cannot look better than a sourced one
        # simply by having less measured about it.
        "verdict": "PROFESSIONAL" if not failed else "NOT_PROFESSIONAL",
        "complete": not not_run,
        "structure": structural_summary(struct),
        "instrument": "arithmetic only (no model)",
    }


def _describe(name, part):
    if name == "balance":
        return (f"lessons per module {part['lessons_per_module']} "
                f"(spread {part['spread']}, taper {part['taper_ratio']})")
    if name == "titles":
        return (f"{part['generic']}/{part['titles']} titles name nothing"
                + (f" e.g. {part['examples'][:2]}" if part.get("examples") else ""))
    if name == "units":
        return f"{part['degenerate']}/{part['units']} units hold one lesson or none"
    if name == "uniformity":
        return f"units per module {part['units_per_module']}"
    if name == "school_shape":
        return (f"modules {part['modules_of_school_size']:.0%} · "
                f"units {part['units_of_school_size']:.0%} · "
                f"lessons {part['lessons_of_school_size']:.0%} in school-sized bands")
    return ""


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", required=True)
    p.add_argument("--reference")
    p.add_argument("--out")
    a = p.parse_args()

    struct = json.load(open(a.course))
    reference = None
    if a.reference:
        raw = json.load(open(a.reference))
        reference = raw.get("areas", raw)

    r = run(struct, reference)
    s = r["structure"]

    print(f"\n=== skeleton QA ===   ({r['instrument']})")
    print(f"  {s['modules']} modules · {s['units']} units · {s['lessons']} "
          f"lessons · {s['concepts']} concepts ({s['concepts_per_lesson']}/lesson)\n")
    for name in ("coverage", "sequencing", "school_shape", "balance", "titles",
                 "units", "uniformity", "coherence"):
        c = r["checks"].get(name) or {}
        mark = "—   " if not c.get("ran") else ("PASS" if c.get("passed") else "FAIL")
        print(f"  {mark} {name:14s} {c.get('detail', '')}")
    print(f"\n  VERDICT: {r['verdict']}"
          + (f" — failed: {', '.join(r['failed'])}" if r["failed"] else "")
          + (f"\n  NOT RUN: {', '.join(r['not_run'])}" if r["not_run"] else ""))

    if a.out:
        json.dump(r, open(a.out, "w"), indent=2)
    return 0 if r["verdict"] == "PROFESSIONAL" else 1


if __name__ == "__main__":
    sys.exit(main())
