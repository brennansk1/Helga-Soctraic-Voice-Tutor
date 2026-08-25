#!/usr/bin/env python3
"""hydration_qa.py — one verdict on whether a course's CONTENT is good enough.

`skeleton_qa.py` grades the structure: does it cover the syllabus, in a
teachable order, shaped like a school course. Every check in it reads titles and
counts. **None of them reads a word of content**, which is why a course could
measure PROFESSIONAL while containing verified false claims and hollow concepts.

This is the content counterpart, and the exit criteria for hydration
development. Same discipline as the skeleton harness:

  * **conjunctive** — every check that CAN run must pass
  * **NOT RUN is never a pass**, so a course cannot score better by having less
    measured about it
  * **arithmetic wherever possible**, because the LLM judge in this repo swings
    +/-1.4 out of 5 between identical runs

WHAT IT MEASURES

    redundancy      is the same idea taught repeatedly?      (ledger, no model)
    substance       do concepts assert anything?             (claims, no model)
    depth           does the mastery level mean something?   (contract, no model)
    grounding       do claims rest on retained sources?      (no model)
    supplementary   what share rests ONLY on weak sources?   (no model)
    truth           are the claims actually supported?       (MiniCheck, a model)

Only the last has a model in it, and it is the one that must be validated on a
seeded false-claim set before anything is gated on it.

USAGE
    python3 tools/hydration_qa.py --course <uid> --data-root <dir>
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")))

# --- thresholds: the exit criteria, in one place -----------------------------
#
# Chosen to be falsifiable rather than aspirational, and deliberately not zero
# where zero would be wrong. Repetition in particular has a correct non-zero
# level — Bruner's spiral is the retention mechanism the scheduling design
# depends on — so what is bounded is concepts that RE-INTRODUCE rather than
# build.



# The checks themselves now live in services/core/course_qa.py so the pipeline
# can run them, not only a person at a terminal. This file is the CLI over
# them — the same move level_audit.py -> level_calibration.py made, for the
# same reason: a check only a human can run is a check that does not run.
#
# check_redundancy stays here: it rebuilds a body from stored claims to feed
# the ledger's own comparator, which is reporting work rather than a check.
from services.core.course_qa import (            # noqa: F401  (CLI surface)
    check_substance, check_hollowness, check_grounding, check_supplementary,
    check_depth, check_truth,
    MAX_REDUNDANT_SHARE, MIN_CLAIMS_PER_CONCEPT, MIN_COMPLETENESS,
    MAX_HOLLOW_SHARE, MIN_GROUNDED_SHARE, MAX_SUPPLEMENTARY_SHARE,
    MIN_DEPTH_PASS, MAX_FALSE_CLAIM_SHARE,
)


def _db(data_root):
    for name in ("helga.db", "progress.db"):
        p = os.path.join(data_root, name)
        if os.path.exists(p):
            return sqlite3.connect(p)
    raise SystemExit(f"no database under {data_root}")


def check_redundancy(conn, course_uid):
    """How much of the course re-teaches what it already taught."""
    try:
        from services.core.taught_ledger import check_redundancy as chk
    except ImportError:
        return {"checked": False, "reason": "ledger unavailable"}
    rows = conn.execute(
        "SELECT concept_uid, title, ordinal FROM taught_concepts "
        "WHERE course_uid=? ORDER BY ordinal", (course_uid,)).fetchall()
    if not rows:
        return {"checked": False, "reason": "no ledger rows — course predates it"}

    offenders = []
    for uid, title, ordinal in rows:
        claims = [r[0] for r in conn.execute(
            "SELECT claim FROM taught_claims WHERE course_uid=? AND concept_uid=?",
            (course_uid, uid))]
        if not claims:
            continue
        # Rebuild a body from the stored claims rather than re-reading the file:
        # the ledger is the record, and this keeps the check independent of
        # whether content still lives on disk.
        r = chk(conn, course_uid, uid, "\n".join(f"- {c}" for c in
                                                 [f"## Key Facts"] + claims), ordinal)
        if not r.get("ok"):
            offenders.append({"title": title,
                              "share": r.get("reintroduced_share")})
    share = len(offenders) / len(rows)
    return {"checked": True, "concepts": len(rows), "redundant": len(offenders),
            "share": round(share, 3), "examples": [o["title"] for o in offenders[:4]],
            "ok": share <= MAX_REDUNDANT_SHARE}


def run(conn, course_uid, course_json=None, verifier=None):
    checks = {
        "redundancy": check_redundancy(conn, course_uid),
        "substance": check_substance(conn, course_uid),
        "hollowness": check_hollowness(conn, course_uid),
        "grounding": check_grounding(conn, course_uid),
        "supplementary": check_supplementary(conn, course_uid),
        "depth": check_depth(course_json),
        "truth": check_truth(conn, course_uid, verifier),
    }
    ran = {k: v for k, v in checks.items() if v.get("checked")}
    failed = sorted(k for k, v in ran.items() if not v.get("ok"))
    not_run = sorted(k for k, v in checks.items() if not v.get("checked"))
    # Checks that ran over only part of the course. `coverage` is set when a
    # check knows the course is bigger than the slice it measured.
    partial = sorted(k for k, v in ran.items()
                     if v.get("partial_run") or (v.get("coverage") or 1) < 0.9)
    return {
        "checks": checks, "failed": failed, "not_run": not_run,
        # Conjunctive, and NOT RUN is reported rather than counted — the same
        # rule the skeleton harness uses, for the same reason.
        # A CHECK THAT SAW A SIXTH OF THE COURSE HAS NOT CLEARED THE COURSE.
        #
        # `failed` only counts checks that ran AND returned ok=False, so a
        # depth check covering 14 concepts of 95 — a resumed build recording
        # only its last segment — reported PASS, and the course came out
        # CONTENT_READY on 15% coverage. That is the same defect as reporting
        # clean because nothing was measured, wearing a passing badge.
        #
        # Partial coverage is neither a pass nor a failure; it is an unfinished
        # measurement, and it belongs with `not_run` where the discipline
        # already says it cannot count as clean.
        "verdict": ("CONTENT_READY" if not failed and not partial
                    else "NOT_READY" if failed else "INCOMPLETE"),
        "partial": partial,
        "complete": not not_run and not partial,
        "instrument": ("arithmetic on the ledger (no model), except `truth`"
                       if verifier else "arithmetic only (no model)"),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", required=True)
    p.add_argument("--data-root", default=os.getenv("DATA_ROOT", "data"))
    p.add_argument("--structure", help="path to structure.json for the depth check")
    p.add_argument("--verify", action="store_true",
                   help="run the truth check with MiniCheck (loads a model)")
    p.add_argument("--out")
    a = p.parse_args()

    conn = _db(a.data_root)
    cj = None
    # DEFAULT TO THE COURSE'S OWN STRUCTURE.
    #
    # --structure was optional and unset by default, so the depth check
    # received no course JSON and reported "no depth_contract on the course"
    # on every ordinary invocation. Combined with the key-name bug it was
    # fixed for, depth had two independent reasons never to run — and the tool
    # printed CONTENT_READY for a course that fails it 20 concepts out of 33.
    #
    # The file is always at a known path; there is no reason to make the
    # operator remember to point at it.
    structure_path = a.structure or os.path.join(
        a.data_root, "courses", a.course, "structure.json")
    if structure_path and os.path.exists(structure_path):
        cj = json.load(open(structure_path))

    verifier = None
    if a.verify:
        try:
            from services.core.claim_verifier import get_verifier
            verifier = get_verifier()
        except Exception as e:
            print(f"  (verifier unavailable: {e})")

    r = run(conn, a.course, cj, verifier)
    print(f"\n=== hydration QA ===   ({r['instrument']})\n")
    for name in ("redundancy", "substance", "hollowness", "grounding",
                 "supplementary", "depth", "truth"):
        c = r["checks"][name]
        mark = "—   " if not c.get("checked") else ("PASS" if c.get("ok") else "FAIL")
        detail = c.get("reason") or ", ".join(
            f"{k}={v}" for k, v in c.items()
            if k not in ("checked", "ok", "examples", "reason"))
        print(f"  {mark} {name:14s} {detail}")
        if c.get("examples"):
            print(f"       e.g. {c['examples'][:2]}")
    print(f"\n  VERDICT: {r['verdict']}"
          + (f" — failed: {', '.join(r['failed'])}" if r["failed"] else "")
          + (f"\n  NOT RUN: {', '.join(r['not_run'])}" if r["not_run"] else "")
          + (f"\n  PARTIAL: {', '.join(r['partial'])} — measured on only part "
             f"of the course" if r.get("partial") else ""))
    if a.out:
        json.dump(r, open(a.out, "w"), indent=2)
    return 0 if r["verdict"] == "CONTENT_READY" else 1


if __name__ == "__main__":
    sys.exit(main())
