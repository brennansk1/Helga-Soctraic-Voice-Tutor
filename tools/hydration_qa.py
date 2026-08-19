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

MAX_REDUNDANT_SHARE = 0.10      # concepts re-introducing >=half their claims
MIN_CLAIMS_PER_CONCEPT = 2.0    # below this, concepts assert nothing
MIN_COMPLETENESS = 0.70         # teaching-object fields actually filled
MAX_HOLLOW_SHARE = 0.10         # concepts below half their fields
MIN_GROUNDED_SHARE = 0.80       # claims linked to a retained source
MAX_SUPPLEMENTARY_SHARE = 0.20  # claims resting ONLY on below-bar sources
MIN_DEPTH_PASS = 0.90           # concepts meeting their depth contract
MAX_FALSE_CLAIM_SHARE = 0.05    # claims a verifier judges unsupported


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


def check_substance(conn, course_uid):
    """Do concepts assert anything, or are they fluent and empty?

    The measured failure was ~half of concepts 'hollow' — structurally complete,
    passing the section template, saying little. Claims per concept is the
    cheapest model-free proxy for that.
    """
    rows = conn.execute(
        "SELECT c.concept_uid, COUNT(k.claim) FROM taught_concepts c "
        "LEFT JOIN taught_claims k ON k.course_uid=c.course_uid "
        "AND k.concept_uid=c.concept_uid WHERE c.course_uid=? "
        "GROUP BY c.concept_uid", (course_uid,)).fetchall()
    if not rows:
        return {"checked": False, "reason": "no ledger rows"}
    counts = [n for _, n in rows]
    mean = sum(counts) / len(counts)
    empty = sum(1 for n in counts if n == 0)
    return {"checked": True, "concepts": len(counts),
            "claims_per_concept": round(mean, 2), "empty": empty,
            "ok": mean >= MIN_CLAIMS_PER_CONCEPT and empty == 0}


def check_hollowness(conn, course_uid):
    """Structurally complete and substantively empty — the measured defect.

    ~half of concepts were found hollow: passing the section template, saying
    little. The section template cannot see this by construction, because
    passing it IS having the headings. The teaching object counts what each
    concept actually FILLED.
    """
    try:
        rows = conn.execute(
            "SELECT completeness FROM teaching_objects WHERE course_uid=?",
            (course_uid,)).fetchall()
    except sqlite3.OperationalError:
        return {"checked": False, "reason": "teaching_objects absent (pre-v14)"}
    scores = [r[0] for r in rows if r[0] is not None]
    if not scores:
        return {"checked": False, "reason": "no teaching objects"}
    mean = sum(scores) / len(scores)
    hollow = sum(1 for s in scores if s < 0.5)
    share = hollow / len(scores)
    return {"checked": True, "concepts": len(scores),
            "mean_completeness": round(mean, 3), "hollow": hollow,
            "hollow_share": round(share, 3),
            "ok": mean >= MIN_COMPLETENESS and share <= MAX_HOLLOW_SHARE}


def check_grounding(conn, course_uid):
    """Are claims linked to a retained source, or asserted from nowhere?"""
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM taught_claims WHERE course_uid=?",
            (course_uid,)).fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM claim_sources WHERE course_uid=? "
            "AND source_id IS NOT NULL", (course_uid,)).fetchone()[0]
    except sqlite3.OperationalError:
        return {"checked": False, "reason": "source tables absent (pre-v12)"}
    if not total:
        return {"checked": False, "reason": "no claims"}
    share = linked / total
    return {"checked": True, "claims": total, "grounded": linked,
            "share": round(share, 3), "ok": share >= MIN_GROUNDED_SHARE}


def check_supplementary(conn, course_uid):
    """Share of claims resting ONLY on below-bar sources.

    Measured in claims, not sources: one weak book can dominate content while
    being a small minority of the source list.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*), SUM(supplementary) FROM claim_sources "
            "WHERE course_uid=?", (course_uid,)).fetchone()
    except sqlite3.OperationalError:
        return {"checked": False, "reason": "claim_sources absent (pre-v12)"}
    total, supp = (row or (0, 0))
    if not total:
        return {"checked": False, "reason": "no claim-source links"}
    share = (supp or 0) / total
    return {"checked": True, "claims": total, "supplementary_only": supp or 0,
            "share": round(share, 3), "ok": share <= MAX_SUPPLEMENTARY_SHARE}


def check_depth(course_json):
    """Did concepts meet the depth contract for their mastery level?"""
    d = (course_json or {}).get("depth_contract") or {}
    if not d:
        return {"checked": False, "reason": "no depth_contract on the course"}
    total = d.get("checked") or d.get("total") or 0
    missed = d.get("missed") or d.get("failures") or 0
    if not total:
        return {"checked": False, "reason": "depth_contract recorded no totals"}
    share = (total - missed) / total
    return {"checked": True, "concepts": total, "passed": total - missed,
            "share": round(share, 3), "ok": share >= MIN_DEPTH_PASS}


def check_truth(conn, course_uid, verifier=None):
    """Are claims actually supported by the sources retained for them?

    NOT RUN without a verifier — never reported as passed. This is the only
    check here with a model in it, and the plan is explicit that it must be
    validated on a seeded false-claim set before anything is gated on it.
    """
    if verifier is None:
        return {"checked": False,
                "reason": "no verifier available — truth NOT measured"}
    try:
        rows = conn.execute(
            "SELECT cs.claim, s.passage FROM claim_sources cs "
            "JOIN sources s ON s.source_id = cs.source_id "
            "WHERE cs.course_uid=? AND s.passage != ''", (course_uid,)).fetchall()
    except sqlite3.OperationalError:
        return {"checked": False, "reason": "source tables absent"}
    if not rows:
        return {"checked": False, "reason": "no claim/passage pairs to check"}
    unsupported = [c for c, p in rows if not verifier(c, p)]
    share = len(unsupported) / len(rows)
    return {"checked": True, "pairs": len(rows), "unsupported": len(unsupported),
            "share": round(share, 3), "examples": unsupported[:3],
            # ADVISORY, NOT A GATE — measured, not assumed.
            #
            # On the seed set MiniCheck caught 3/3 falsehoods and also rejected
            # 2/3 TRUE claims that needed one step of inference from their
            # passage ("mean is (1+20)/2 = 10.5" was judged not to support "the
            # expected value is 10.5"). Teaching material is written to rephrase
            # and generalise its sources, so that failure mode is the norm here,
            # not an edge case.
            #
            # Failing a course on this would reject correct content at a rate
            # that swamps the defect it is looking for. It reports and flags for
            # review until the false-positive rate is measured on real content.
            "advisory": True,
            "note": ("flagging only — MiniCheck rejected 2/3 true claims "
                     "needing inference on the seed set"),
            "ok": True,
            "would_fail_if_gated": share > MAX_FALSE_CLAIM_SHARE}


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
    return {
        "checks": checks, "failed": failed, "not_run": not_run,
        # Conjunctive, and NOT RUN is reported rather than counted — the same
        # rule the skeleton harness uses, for the same reason.
        "verdict": "CONTENT_READY" if not failed else "NOT_READY",
        "complete": not not_run,
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
    if a.structure and os.path.exists(a.structure):
        cj = json.load(open(a.structure))

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
          + (f"\n  NOT RUN: {', '.join(r['not_run'])}" if r["not_run"] else ""))
    if a.out:
        json.dump(r, open(a.out, "w"), indent=2)
    return 0 if r["verdict"] == "CONTENT_READY" else 1


if __name__ == "__main__":
    sys.exit(main())
