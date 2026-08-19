#!/usr/bin/env python3
"""structure_quality.py — is this shaped like a professional course?

WHY COVERAGE IS NOT ENOUGH
--------------------------
`coverage_check.py` answers "does it reach the material" and `sequencing_check`
answers "is it in a teachable order". Neither can see that a course is *shaped*
wrong. Measured on real builds:

    generated    lessons per module = [9, 9, 3, 3, 3, 3]
    spine-built  lessons per module = [6, 6, 3, 6, 6, 8]

The first is a taper: modules 1-2 carry three times the rest. No real course
front-loads nine sessions and then coasts through four modules of three — it is
the signature of a generator putting effort into the early calls and running out
of steam, and it produces a curriculum that feels abandoned halfway.

Everything here is arithmetic on titles and counts. No model, so no drift.

WHAT IT MEASURES
----------------
* **balance**    — spread of lessons across modules
* **taper**      — do later modules shrink systematically?
* **degenerate** — units holding a single lesson, which is a unit in name only
* **generic**    — "Advanced Topics", "Core Concepts": titles that name nothing
* **uniformity** — does every module have the same depth of structure?
"""

import argparse
import json
import re
import statistics
import sys

# Titles that could belong to any course on any subject. A real syllabus says
# "Orthogonal Bases and Gram-Schmidt", not "Advanced Topics" — a generic title is
# the model declining to decide what the section is about.
_GENERIC = re.compile(
    r"^(advanced|further|additional|other|misc\w*|core|key|basic|general|"
    r"introductory)?\s*"
    r"(topics?|concepts?|principles?|fundamentals?|foundations?|ideas?|"
    r"applications?|methods?|techniques?|material|content|study|review|"
    r"overview|introduction|conclusion|summary|extras?)\s*"
    r"(\d+|i{1,3})?$", re.I)


# THE SHAPE OF A SCHOOL COURSE, AS RANGES.
#
# One definition, used by the builder to ask for a shape and by this tool to
# check it. Two copies would drift, and the checker would end up grading against
# a standard the builder was never told about.
#
# Ranges rather than fixed counts, because real courses vary: a module may run
# two weeks or four, a week may hold two sessions or five. What is NOT flexible
# is the level below the range — a module of one unit is a week wearing a
# module's name, and that is collapse rather than variation.
SCHOOL_SHAPE = {
    "units_per_module": (2, 4),      # a module is ~2-4 weeks
    "lessons_per_unit": (2, 5),      # a week is ~3 class sessions
    "concepts_per_lesson": (2, 4),   # a 50-minute session covers 2-4 ideas
}


def _modules(struct):
    return (struct or {}).get("modules") or []


def _lessons_of(module):
    return [l for u in (module.get("units") or [])
            for l in (u.get("lessons") or [])]


def check_balance(struct):
    """Are modules comparable in size, or does the course taper?"""
    mods = _modules(struct)
    if len(mods) < 3:
        return {"checked": False, "reason": "too few modules"}
    counts = [len(_lessons_of(m)) for m in mods]
    if not any(counts):
        return {"checked": False, "reason": "no lessons"}

    mean = statistics.mean(counts)
    spread = (statistics.pstdev(counts) / mean) if mean else 0.0

    # Taper: compare the first third against the last third. A course that
    # front-loads is a generator losing steam, not a syllabus.
    third = max(1, len(counts) // 3)
    head, tail = counts[:third], counts[-third:]
    taper = (statistics.mean(head) / statistics.mean(tail)
             if statistics.mean(tail) else float("inf"))

    return {
        "checked": True,
        "lessons_per_module": counts,
        "mean": round(mean, 1),
        "spread": round(spread, 2),          # coefficient of variation
        "taper_ratio": round(taper, 2),      # >1 means front-loaded
        # 0.35 tolerates real variation — a capstone module is legitimately
        # smaller — while catching [9,9,3,3,3,3], whose spread is 0.58.
        "balanced": spread <= 0.35 and taper <= 1.6,
    }


def check_titles(struct):
    """Do the titles name anything, or could they belong to any course?"""
    titles, generic = [], []
    for m in _modules(struct):
        for level, node in [("module", m)] + \
                [("unit", u) for u in (m.get("units") or [])] + \
                [("lesson", l) for u in (m.get("units") or [])
                 for l in (u.get("lessons") or [])]:
            t = (node.get("title") or "").strip()
            if not t:
                continue
            titles.append(t)
            if _GENERIC.match(t):
                generic.append(f"{level}: {t}")
    if not titles:
        return {"checked": False, "reason": "no titles"}
    rate = len(generic) / len(titles)
    return {
        "checked": True,
        "titles": len(titles),
        "generic": len(generic),
        "rate": round(rate, 3),
        "examples": generic[:6],
        "specific": rate <= 0.05,
    }


def check_units(struct):
    """Units holding one lesson are units in name only."""
    units = [u for m in _modules(struct) for u in (m.get("units") or [])]
    if not units:
        return {"checked": False, "reason": "no units"}
    sizes = [len(u.get("lessons") or []) for u in units]
    degenerate = sum(1 for s in sizes if s <= 1)
    empty = sum(1 for s in sizes if s == 0)
    return {
        "checked": True,
        "units": len(units),
        "degenerate": degenerate,
        "empty": empty,
        "rate": round(degenerate / len(units), 3),
        "ok": empty == 0 and degenerate / len(units) <= 0.15,
    }


def check_uniformity(struct):
    """Does every module have real substructure, or do some collapse?"""
    mods = _modules(struct)
    if not mods:
        return {"checked": False, "reason": "no modules"}
    unit_counts = [len(m.get("units") or []) for m in mods]
    return {
        "checked": True,
        "units_per_module": unit_counts,
        "modules_without_units": sum(1 for n in unit_counts if n == 0),
        "ok": all(n >= 1 for n in unit_counts),
    }


def check_school_shape(struct):
    """Do the levels map onto a real school's calendar?

    In a real institution each level is a unit of TIME, not an arbitrary
    grouping:

        module   2-3 weeks of the syllabus
        unit     one week
        lesson   one class session   (3 per week)
        concept  one idea inside a session

    So a module should hold 2-3 units, a unit about 3 lessons, and a lesson 2-4
    concepts. Measured on real builds, the difference is stark:

        units per module [3, 3, 3, 3, 3, 3]  -> every module is three weeks
        units per module [3, 3, 1, 1, 1, 1]  -> four modules are ONE week each

    The second is not a module in any sense a registrar would recognise; it is a
    week wearing a module's name.
    """
    mods = _modules(struct)
    if not mods:
        return {"checked": False, "reason": "no modules"}

    units_per = [len(m.get("units") or []) for m in mods]
    lessons_per_unit, concepts_per_lesson = [], []
    for m in mods:
        for u in (m.get("units") or []):
            ls = u.get("lessons") or []
            lessons_per_unit.append(len(ls))
            for l in ls:
                concepts_per_lesson.append(len(l.get("concepts") or []))

    def _share_in(values, lo, hi):
        return (sum(1 for v in values if lo <= v <= hi) / len(values)
                if values else 0.0)

    # Shares rather than medians: a median hides four collapsed modules behind
    # two healthy ones, which is exactly the [3,3,1,1,1,1] case.
    mod_ok = _share_in(units_per, *SCHOOL_SHAPE["units_per_module"])
    unit_ok = _share_in(lessons_per_unit, *SCHOOL_SHAPE["lessons_per_unit"])
    lesson_ok = _share_in(concepts_per_lesson,
                          *SCHOOL_SHAPE["concepts_per_lesson"])

    return {
        "checked": True,
        "units_per_module": units_per,
        "modules_of_school_size": round(mod_ok, 2),
        "units_of_school_size": round(unit_ok, 2),
        "lessons_of_school_size": round(lesson_ok, 2),
        "median_lessons_per_unit": (statistics.median(lessons_per_unit)
                                    if lessons_per_unit else 0),
        "median_concepts_per_lesson": (statistics.median(concepts_per_lesson)
                                       if concepts_per_lesson else 0),
        # Most of each level must sit in its school-sized band. 0.8 leaves room
        # for a legitimately short capstone module without excusing a course
        # where two thirds of the modules are a single week.
        "ok": mod_ok >= 0.8 and unit_ok >= 0.8 and lesson_ok >= 0.8,
    }


def assess(struct):
    """All four checks plus a single verdict."""
    out = {
        "balance": check_balance(struct),
        "titles": check_titles(struct),
        "units": check_units(struct),
        "uniformity": check_uniformity(struct),
        "school_shape": check_school_shape(struct),
        "instrument": "structural arithmetic (no model)",
    }
    graded = [v for v in out.values()
              if isinstance(v, dict) and v.get("checked")]
    passed = [v for v in graded
              if v.get("balanced") or v.get("specific") or v.get("ok")]
    out["passed"] = len(passed)
    out["graded"] = len(graded)
    out["verdict"] = "ok" if len(passed) == len(graded) else "STRUCTURAL_DEFECT"
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", required=True)
    p.add_argument("--out")
    a = p.parse_args()
    struct = json.load(open(a.course))
    r = assess(struct)

    b, t, u, f = r["balance"], r["titles"], r["units"], r["uniformity"]
    print("\n=== structure quality ===")
    print(f"    instrument: {r['instrument']}\n")
    if b.get("checked"):
        print(f"  {'OK  ' if b['balanced'] else 'FAIL'} balance     "
              f"{b['lessons_per_module']}  spread={b['spread']} "
              f"taper={b['taper_ratio']}")
    if t.get("checked"):
        print(f"  {'OK  ' if t['specific'] else 'FAIL'} titles      "
              f"{t['generic']}/{t['titles']} generic ({t['rate']:.1%})"
              + (f"  e.g. {t['examples'][:2]}" if t["examples"] else ""))
    if u.get("checked"):
        print(f"  {'OK  ' if u['ok'] else 'FAIL'} units       "
              f"{u['degenerate']}/{u['units']} hold one lesson or none")
    if f.get("checked"):
        print(f"  {'OK  ' if f['ok'] else 'FAIL'} uniformity  "
              f"units per module {f['units_per_module']}")
    sc = r.get("school_shape") or {}
    if sc.get("checked"):
        print(f"  {'OK  ' if sc['ok'] else 'FAIL'} school      "
              f"modules 2-4 units: {sc['modules_of_school_size']:.0%} · "
              f"units 2-5 lessons: {sc['units_of_school_size']:.0%} · "
              f"lessons 2-4 concepts: {sc['lessons_of_school_size']:.0%}")
    print(f"\n  VERDICT: {r['verdict']} ({r['passed']}/{r['graded']})")

    if a.out:
        json.dump(r, open(a.out, "w"), indent=2)
    return 0 if r["verdict"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
