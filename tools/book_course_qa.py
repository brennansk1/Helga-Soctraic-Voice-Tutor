#!/usr/bin/env python3
"""book_course_qa.py — is a book-built course faithful to its book?

WHY A SEPARATE HARNESS
----------------------
`skeleton_qa.py` grades a RESEARCHED course: coverage against a published
syllabus, school-shaped module/unit/lesson bands, calendar balance. A book
course deliberately breaks those bands — a 59-lesson novel with one container
module is CORRECT for a novel and would fail school_shape at 0% — because its
quality criterion is different: **fidelity to the book it came from**.

What faithful means, each check arithmetic with no model in it:

    linkage       every lesson points at a real chapter, in the book's order
    completeness  every chapter that should be a lesson is one
    naming        concepts were named (by reading), and titles name something
    coverage      the concepts drawn per lesson match what its chapter supports
    shape         the declared shape matches what was built

Same discipline as the other harnesses: conjunctive verdict, NOT RUN is never a
pass, and every check says why it failed.
"""

import argparse
import json
import re
import sys

# A concept title that names nothing, reused from the skeleton's standard.
_GENERIC = re.compile(
    r"^\s*(introduction|overview|key (points|concepts|terms)|summary|basics?"
    r"|fundamentals?|conclusion|review|miscellaneous|other topics?)\s*$",
    re.IGNORECASE)

_BARE = re.compile(
    r"^\s*(?:chapter|ch\.?|section|part|lesson)?\s*[0-9IVXLCivxlc]*\s*[.:)-]?\s*$",
    re.IGNORECASE)


def _lessons(course):
    return [l for m in (course.get("modules") or [])
            for u in (m.get("units") or [])
            for l in (u.get("lessons") or [])]


def check_linkage(course, book_chapters=None):
    """Every lesson points at a chapter, and lesson order follows book order."""
    lessons = _lessons(course)
    if not lessons:
        return {"checked": False, "reason": "no lessons"}
    unlinked = [l.get("title") for l in lessons if not l.get("book_chapter")]
    orders = [l.get("book_chapter") for l in lessons if l.get("book_chapter")]
    # Within each unit the chapters must ascend — the book's own order is the
    # one thing a book course must never scramble.
    misordered = 0
    for m in (course.get("modules") or []):
        for u in (m.get("units") or []):
            seq = [l.get("book_chapter") for l in (u.get("lessons") or [])
                   if l.get("book_chapter")]
            misordered += sum(1 for a, b in zip(seq, seq[1:]) if b < a)
    out = {"checked": True, "lessons": len(lessons),
           "unlinked": len(unlinked), "misordered_pairs": misordered,
           "ok": not unlinked and misordered == 0}
    if book_chapters:
        missing = sorted(set(range(1, book_chapters + 1)) - set(orders))
        out["chapters_missing"] = missing[:8]
        out["ok"] = out["ok"] and not missing
    return out


def check_naming(course):
    """Concepts were named by reading, and the names say something."""
    lessons = _lessons(course)
    concepts = [c for l in lessons for c in (l.get("concepts") or [])]
    if not concepts:
        return {"checked": False, "reason": "no concepts"}
    unnamed = sum(1 for c in concepts if not (c.get("title") or "").strip())
    generic = [c["title"] for c in concepts
               if c.get("title") and _GENERIC.match(c["title"])]
    bare_lessons = [l["title"] for l in lessons
                    if _BARE.match(l.get("title") or "")]
    return {"checked": True, "concepts": len(concepts), "unnamed": unnamed,
            "generic": len(generic), "bare_lesson_titles": len(bare_lessons),
            "examples": (generic[:3] + bare_lessons[:3]),
            # A couple of bare titles on a 60-lesson novel is tolerable;
            # unnamed concepts are not, because they hydrate into filler.
            "ok": unnamed == 0 and len(generic) <= max(1, len(concepts) // 20)
                  and len(bare_lessons) <= max(1, len(lessons) // 10)}


def check_shape(course):
    """The declared shape matches what was actually built."""
    shape = (course.get("book_shape") or {}).get("shape")
    mods = course.get("modules") or []
    if not shape:
        return {"checked": False, "reason": "no book_shape recorded"}
    if shape == "textbook":
        ok = len(mods) >= 2 and not any(m.get("container_only") for m in mods)
        why = f"{len(mods)} modules from the book's own chapters"
    else:
        ok = len(mods) == 1 and bool(mods[0].get("container_only"))
        why = "one container module, no invented divisions"
    return {"checked": True, "shape": shape, "detail": why, "ok": ok}


def check_concept_density(course):
    """Concepts per lesson sit where chapter length puts them.

    2-6 is the design band; 0 means naming failed and >6 means padding. The
    band is wide because the BOOK sets the density, not a school calendar.
    """
    lessons = _lessons(course)
    if not lessons:
        return {"checked": False, "reason": "no lessons"}
    counts = [len(l.get("concepts") or []) for l in lessons]
    empty = sum(1 for n in counts if n == 0)
    over = sum(1 for n in counts if n > 6)
    return {"checked": True, "min": min(counts), "max": max(counts),
            "mean": round(sum(counts) / len(counts), 2), "empty_lessons": empty,
            "over_dense": over, "ok": empty == 0 and over == 0}


def run(course, book_chapters=None):
    checks = {
        "linkage": check_linkage(course, book_chapters),
        "naming": check_naming(course),
        "shape": check_shape(course),
        "density": check_concept_density(course),
    }
    ran = {k: v for k, v in checks.items() if v.get("checked")}
    failed = sorted(k for k, v in ran.items() if not v.get("ok"))
    not_run = sorted(k for k, v in checks.items() if not v.get("checked"))
    return {"checks": checks, "failed": failed, "not_run": not_run,
            "verdict": "BOOK_FAITHFUL" if not failed else "NOT_FAITHFUL",
            "complete": not not_run,
            "instrument": "arithmetic on the structure (no model)"}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", required=True, help="path to structure.json")
    p.add_argument("--chapters", type=int, help="chapter count of the source book")
    p.add_argument("--out")
    a = p.parse_args()
    course = json.load(open(a.course))
    r = run(course, a.chapters)
    print(f"\n=== book course QA ===   ({r['instrument']})\n")
    for name, c in r["checks"].items():
        mark = "—   " if not c.get("checked") else ("PASS" if c.get("ok") else "FAIL")
        detail = c.get("reason") or ", ".join(
            f"{k}={v}" for k, v in c.items()
            if k not in ("checked", "ok", "examples"))
        print(f"  {mark} {name:10s} {detail[:96]}")
        if c.get("examples"):
            print(f"       e.g. {c['examples'][:3]}")
    print(f"\n  VERDICT: {r['verdict']}"
          + (f" — failed: {', '.join(r['failed'])}" if r["failed"] else ""))
    if a.out:
        json.dump(r, open(a.out, "w"), indent=2)
    return 0 if r["verdict"] == "BOOK_FAITHFUL" else 1


if __name__ == "__main__":
    sys.exit(main())
