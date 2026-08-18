#!/usr/bin/env python3
"""coverage_check.py — course coverage against a real published syllabus.

WHY THIS EXISTS ALONGSIDE syllabus_check.py
-------------------------------------------
`syllabus_check.py` (quality gate criterion 6) asks an LLM which topics a course
covers. Measured 2026-08-18 on a generated Linear Algebra course with the
external reference correctly supplied, it returned:

    coverage_pct : 0    verdict: INADEQUATE
    missing      : ['Vector Spaces', 'Basis and Dimension', 'Linear Maps',
                    'Determinants', ...]

The course's own module titles were "Vector Spaces and Linear Combinations",
"Basis and Dimension", "Matrix-Vector Multiplication and Linear Maps" and
"Determinants and Inverses". The judge declared as missing four topics that were
literally module titles — the defect `syllabus_check.py` documents about itself,
reproduced with the plumbing fixed. **The judge is the fault, not the wiring.**

This tool answers the same question with **no model anywhere in it**. A topic is
covered if its identifying terms appear in the course's own titles. That is a
weaker question than "is this taught well", and it is the right trade: it cannot
drift, cannot hallucinate, and its result is verifiable by eye in seconds.

On the same course: judge 0%, this tool **70%**, and 70% is the one that survives
inspection.

WHAT IT CANNOT DO
-----------------
Keyword presence is not evidence of quality or depth. A course listing
"Eigenvalues" in a title and teaching it badly scores the same as one teaching it
well. This measures COVERAGE — whether the curriculum reaches the material — and
coverage is exactly what the 42%-coverage failure was about. Depth is what the
depth contract and fact-check are for. Do not read it as a quality score.

REFERENCES
----------
A reference is a JSON file mapping topic area -> identifying terms:

    {"Least squares & projections": ["least squares", "projection"], ...}

Built from a real published syllabus (MIT OCW, a university course page, a
textbook's table of contents). `references/` holds the ones already transcribed.
"""

import argparse
import json
import os
import re
import sys


def course_title_blob(struct):
    """Every title in a course structure, lowercased, joined.

    Titles only — not generated body text. A course that merely *mentions* a term
    in prose has not covered it; a course with a lesson named for it has.
    """
    parts = []

    def walk(node):
        if isinstance(node, dict):
            t = node.get("title")
            if isinstance(t, str):
                parts.append(t)
            for key in ("modules", "units", "lessons", "concepts"):
                for child in (node.get(key) or []):
                    walk(child)

    walk(struct)
    for m in (struct.get("modules") or []):
        walk(m)
    return " | ".join(parts).lower(), parts


def _normalise(text):
    """Fold punctuation and whitespace so 'Gram-Schmidt' matches 'gram schmidt'."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def check_coverage(struct, reference):
    """Coverage of `reference` (area -> terms) by a course structure.

    Returns a dict with per-area hits, the matched term for each, and a
    percentage. Never raises on a malformed course — a missing structure is
    reported as 0 areas checked, NOT as 0% coverage, because "we could not look"
    and "it covers nothing" are different facts.
    """
    blob, titles = course_title_blob(struct or {})
    if not titles:
        return {"error": "no titles found in structure", "areas_checked": 0}

    norm_blob = _normalise(blob)
    areas, hits = [], 0
    for area, terms in reference.items():
        matched = [t for t in terms if _normalise(t) in norm_blob]
        covered = bool(matched)
        hits += covered
        areas.append({"area": area, "covered": covered, "matched": matched[:3]})

    total = len(reference)
    return {
        "areas_checked": total,
        "areas_covered": hits,
        "coverage_pct": round(hits / total * 100) if total else 0,
        "areas": areas,
        "missing": [a["area"] for a in areas if not a["covered"]],
        "title_count": len(titles),
        "instrument": "keyword (no model)",
    }


def sequencing_check(struct):
    """Is the course ordered as taught, or merely sorted?

    Coverage cannot see this. A course copied from an alphabetical index scored
    **100%** on the coverage instrument above while its modules ran
    "Addition..., Cofactors..., Definition..., Diagonal Matrix, Gauss-Jordan,
    Identity Matrix" — every topic present, none of it in a teachable order, and
    "Identity Matrix" standing as a module.

    Presence is not sequence, so the two questions need two instruments. This is
    still model-free: alphabetical ordering is arithmetic.
    """
    mods = [(m.get("title") or "").strip().lower()
            for m in ((struct or {}).get("modules") or [])]
    mods = [m for m in mods if m]
    if len(mods) < 4:
        return {"checked": False,
                "reason": "too few modules to judge ordering"}
    in_order = sum(1 for a, b in zip(mods, mods[1:]) if a <= b)
    ratio = in_order / max(1, len(mods) - 1)
    alphabetical = ratio >= 0.9
    return {
        "checked": True,
        "alphabetical": alphabetical,
        "in_order_ratio": round(ratio, 2),
        "verdict": "INDEX_ORDER" if alphabetical else "ok",
        "note": ("modules are in alphabetical order — this is an index, not a "
                 "teaching sequence" if alphabetical else ""),
    }


def structural_summary(struct):
    """Lesson and concept counts, for the volume-parity half of the question.

    A course can cover the syllabus and still be the wrong size, and the
    Linear Algebra measurement found exactly that combination: 54 lessons
    against MIT 18.06's 34 lectures while covering 70% of its syllabus —
    over-long AND under-covering at the same time. Reporting both together is
    what makes that visible.
    """
    mods = (struct or {}).get("modules") or []
    units = [u for m in mods for u in (m.get("units") or [])]
    lessons = [l for u in units for l in (u.get("lessons") or [])]
    concepts = [c for l in lessons for c in (l.get("concepts") or [])]
    return {
        "modules": len(mods), "units": len(units),
        "lessons": len(lessons), "concepts": len(concepts),
        "empty_lessons": sum(1 for l in lessons if not (l.get("concepts") or [])),
        "concepts_per_lesson": (round(len(concepts) / len(lessons), 2)
                                if lessons else 0),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", required=True, help="path to structure.json")
    p.add_argument("--reference", required=True,
                   help="path to a reference JSON (area -> terms)")
    p.add_argument("--expect-lessons", type=int, default=None,
                   help="real course's lecture count, for volume comparison")
    p.add_argument("--min-coverage", type=int, default=None,
                   help="exit non-zero below this percentage")
    p.add_argument("--out")
    a = p.parse_args()

    struct = json.load(open(a.course))
    reference = json.load(open(a.reference))
    if "areas" in reference:            # allow a wrapped reference file
        meta, reference = reference, reference["areas"]
    else:
        meta = {}

    result = check_coverage(struct, reference)
    result["structure"] = structural_summary(struct)
    result["sequencing"] = sequencing_check(struct)
    result["reference"] = meta.get("name", os.path.basename(a.reference))

    print(f"\n=== coverage vs {result['reference']} ===")
    print(f"    instrument: {result.get('instrument')}\n")
    for row in result.get("areas", []):
        mark = "HIT " if row["covered"] else "MISS"
        print(f"  {mark} {row['area'][:52]:54s} {row['matched'] or ''}")
    print(f"\n  COVERAGE: {result.get('areas_covered')}/{result.get('areas_checked')}"
          f" = {result.get('coverage_pct')}%")

    st = result["structure"]
    print(f"\n  structure: {st['modules']} modules · {st['units']} units · "
          f"{st['lessons']} lessons · {st['concepts']} concepts "
          f"({st['concepts_per_lesson']}/lesson)")
    expect = a.expect_lessons or meta.get("lectures")
    if expect:
        delta = round((st["lessons"] - expect) / expect * 100)
        print(f"  volume   : {st['lessons']} lessons vs {expect} real lectures "
              f"({delta:+d}%)")
        if delta > 15 and result.get("coverage_pct", 0) < 100:
            print("  NOTE     : longer than the real course AND not covering it — "
                  "length is being spent on depth in topics already chosen, "
                  "not on reaching the ones missed.")

    seq = result.get("sequencing") or {}
    if seq.get("alphabetical"):
        print(f"\n  SEQUENCING: **{seq['verdict']}** — {seq['note']}")
        print("  Coverage above is meaningless as a quality signal when this "
              "fires: every topic can be present in an unteachable order.")
    elif seq.get("checked"):
        print(f"  sequencing: ok (in-order pairs {seq['in_order_ratio']})")

    if a.out:
        json.dump(result, open(a.out, "w"), indent=2)

    if a.min_coverage is not None and result.get("coverage_pct", 0) < a.min_coverage:
        print(f"\n  FAIL: below --min-coverage {a.min_coverage}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
