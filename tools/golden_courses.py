#!/usr/bin/env python3
"""Golden-course harness (sprint A0).

Creates and measures the evidence base that Helga has never had: before this,
exactly ONE generated course existed on disk, with an empty teaching_style, so
no claim about "course quality across settings" could be supported.

Usage:
    # Measure every course already on disk
    python3 tools/golden_courses.py evaluate

    # Measure one course
    python3 tools/golden_courses.py evaluate --course course_10e8a4de

    # Generate a single course at explicit slider settings
    python3 tools/golden_courses.py generate --topic "causal inference" \
        --scope 3 --mastery 4 --starting-from 2

    # Generate the full comparison matrix (slow — minutes per course)
    python3 tools/golden_courses.py matrix

Metrics are deliberately blunt and structural. They do not judge prose quality
(that is HelgaBench's job); they catch the failure modes we have actually
observed: empty/stub concepts, degenerate scaffolding, flat Bloom progression,
ungrounded content, and depth that does not respond to the sliders.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DATA_DIR = os.path.join(_ROOT, "data")
COURSES_DIR = os.path.join(DATA_DIR, "courses")

# A concept shorter than this is almost certainly a stub or a truncated
# generation rather than real teaching content.
MIN_REAL_WORDS = 200

STUB_MARKERS = (
    "currently unavailable",
    "content for this",
    "is currently being",
    "placeholder",
    "lorem ipsum",
    "todo",
)

# Strict: only a resolvable reference counts. An earlier looser pattern also
# matched "et al." and "(Author, 2019)" in ordinary prose, which inflates
# grounding — the metric must not be more generous than reality.
CITATION_RE = re.compile(r"https?://\S+|\bdoi:\s*\S+|\bISBN\b", re.IGNORECASE)

# The hydrator emits a "## Sources" block with tier-labelled links. Its
# presence is the strongest signal a concept was actually grounded.
SOURCES_RE = re.compile(r"^##\s+Sources\s*$", re.MULTILINE)


def _walk(structure):
    """Yield (module, unit, lesson, concept) for every concept in a course."""
    for m in structure.get("modules", []) or []:
        for u in m.get("units", []) or []:
            for les in u.get("lessons", []) or []:
                for c in les.get("concepts", []) or []:
                    yield m, u, les, c


def evaluate_course(uid):
    """Measure one course on disk. Returns a metrics dict."""
    cdir = os.path.join(COURSES_DIR, uid)
    spath = os.path.join(cdir, "structure.json")
    if not os.path.exists(spath):
        return {"uid": uid, "error": "structure.json not found"}

    with open(spath) as f:
        s = json.load(f)

    modules = s.get("modules", []) or []
    units = [u for m in modules for u in (m.get("units") or [])]
    lessons = [l for u in units for l in (u.get("lessons") or [])]
    concepts = [c for *_, c in _walk(s)]

    # --- content metrics -------------------------------------------------
    words, stubs, missing, cited, sourced, ungrounded = [], [], [], 0, 0, []
    urls = set()
    for c in concepts:
        cpath = os.path.join(cdir, "content", f"{c.get('uid')}.md")
        if not os.path.exists(cpath):
            missing.append(c.get("uid"))
            continue
        with open(cpath) as f:
            body = f.read()
        n = len(body.split())
        words.append(n)
        low = body.lower()
        if n < MIN_REAL_WORDS or any(mk in low for mk in STUB_MARKERS):
            stubs.append(c.get("uid"))
        found = CITATION_RE.findall(body)
        if found:
            cited += 1
            urls.update(u for u in re.findall(r"https?://\S+", body))
        if SOURCES_RE.search(body):
            sourced += 1
        else:
            ungrounded.append(c.get("uid"))

    # --- structure metrics -----------------------------------------------
    degenerate_lessons = sum(1 for l in lessons if len(l.get("concepts") or []) <= 1)
    degenerate_units = sum(1 for u in units if len(u.get("lessons") or []) <= 1)

    blooms = [m.get("bloom_target") for m in modules if m.get("bloom_target") is not None]
    ramps = all(b <= a for a, b in zip(blooms, blooms[1:])) is False and blooms == sorted(blooms)
    bloom_span = (max(blooms) - min(blooms)) if blooms else 0

    confs = [c.get("source_confidence") for c in concepts
             if isinstance(c.get("source_confidence"), (int, float))]

    return {
        "uid": uid,
        "title": s.get("title"),
        "status": s.get("status"),
        "settings": {
            "scope": s.get("scope"),
            "mastery": s.get("mastery"),
            "starting_from": s.get("starting_from"),
            "teaching_style": s.get("teaching_style") or "",
            "bloom_floor": s.get("bloom_floor"),
            "bloom_ceiling": s.get("bloom_ceiling"),
        },
        "structure": {
            "modules": len(modules),
            "units": len(units),
            "lessons": len(lessons),
            "concepts": len(concepts),
            "degenerate_lessons": degenerate_lessons,
            "degenerate_units": degenerate_units,
            "degenerate_lesson_pct": round(100 * degenerate_lessons / len(lessons), 1) if lessons else None,
        },
        "content": {
            "files_present": len(words),
            "files_missing": len(missing),
            "stub_count": len(stubs),
            "stub_uids": stubs[:10],
            "words_min": min(words) if words else None,
            "words_median": int(statistics.median(words)) if words else None,
            "words_max": max(words) if words else None,
            "words_total": sum(words),
            # A uniform band across every concept regardless of Bloom level is
            # the signature of a pipeline that ignores requested depth.
            "words_stdev": round(statistics.pstdev(words), 1) if len(words) > 1 else 0.0,
        },
        "grounding": {
            "concepts_with_citation": cited,
            "citation_coverage_pct": round(100 * cited / len(words), 1) if words else 0.0,
            "concepts_with_sources_block": sourced,
            "unique_sources": len(urls),
            "ungrounded_uids": ungrounded[:10],
            "source_confidence_mean": round(statistics.mean(confs), 3) if confs else None,
            "source_confidence_min": min(confs) if confs else None,
            "source_confidence_max": max(confs) if confs else None,
            "below_0.5": sum(1 for x in confs if x < 0.5),
        },
        "bloom": {
            "targets": blooms,
            "monotonic_nondecreasing": blooms == sorted(blooms) if blooms else None,
            "span": bloom_span,
        },
        # A1: the depth verdict. RECOMPUTED from the content against the
        # CURRENT contract rather than trusting the value recorded at build
        # time — a stored verdict goes stale the moment the contract changes,
        # and reporting a stale number as today's quality is exactly the kind
        # of dishonest artifact this harness exists to catch. The builder's
        # own recorded verdict is kept alongside for comparison.
        "depth_contract": _recompute_depth(cdir, s),
        "depth_contract_recorded": s.get("depth_contract"),
    }


def _recompute_depth(cdir, structure):
    """Score the course's content against the CURRENT depth contract."""
    try:
        sys.path.insert(0, _ROOT)
        from services.core.depth_contract import validate_course
    except Exception:
        return structure.get("depth_contract")
    mastery = structure.get("mastery")
    if not mastery:
        return structure.get("depth_contract")
    pairs = []
    cdir_content = os.path.join(cdir, "content")
    if not os.path.isdir(cdir_content):
        return structure.get("depth_contract")
    for fn in sorted(os.listdir(cdir_content)):
        if fn.endswith(".md"):
            with open(os.path.join(cdir_content, fn)) as f:
                pairs.append((fn[:-3], f.read()))
    if not pairs:
        return structure.get("depth_contract")
    r = validate_course(pairs, mastery, structure.get("title") or "")
    return {
        "mastery": mastery,
        "concepts_total": r["total"],
        "concepts_missing_contract": r["failing"],
        "met_pct": r["pass_rate"],
        "level_verified": r["pass_rate"] >= 80.0,
        "source": "recomputed",
    }


def _gate_failures(m):
    """Return the list of A0/A1/A2 gate criteria this course fails."""
    f = []
    c, st, g, b = m["content"], m["structure"], m["grounding"], m["bloom"]
    if c["files_missing"]:
        f.append(f"{c['files_missing']} concept file(s) missing")
    if c["stub_count"]:
        f.append(f"{c['stub_count']} stub/short concept(s) (<{MIN_REAL_WORDS} words)")
    if st["degenerate_lesson_pct"] and st["degenerate_lesson_pct"] > 20:
        f.append(f"{st['degenerate_lesson_pct']}% degenerate lessons (<=1 concept)")
    if b["monotonic_nondecreasing"] is False:
        f.append("Bloom targets are not monotonically non-decreasing")
    if b["span"] is not None and b["span"] < 2:
        f.append(f"Bloom span only {b['span']} — progression is nearly flat")
    if g["citation_coverage_pct"] < 90:
        f.append(f"citation coverage {g['citation_coverage_pct']}% (<90% — A2 gate)")

    dc = m.get("depth_contract")
    if dc is None:
        f.append("no depth-contract verdict (built before A1 enforcement) — regenerate to verify level")
    elif not dc.get("level_verified"):
        f.append(
            f"level NOT verified: only {dc.get('met_pct')}% of concepts met the "
            f"mastery-{dc.get('mastery')} contract "
            f"({dc.get('concepts_missing_contract')}/{dc.get('concepts_total')} missed)")
    return f


def cmd_evaluate(args):
    uids = [args.course] if args.course else sorted(
        d for d in os.listdir(COURSES_DIR)
        if os.path.isdir(os.path.join(COURSES_DIR, d))
    ) if os.path.isdir(COURSES_DIR) else []

    if not uids:
        print("No courses found on disk. Generate some first:")
        print("  python3 tools/golden_courses.py matrix")
        return 1

    results = []
    for uid in uids:
        m = evaluate_course(uid)
        results.append(m)
        if "error" in m:
            print(f"\n{uid}: ERROR {m['error']}")
            continue

        s, c, g, b = m["structure"], m["content"], m["grounding"], m["bloom"]
        st = m["settings"]
        print(f"\n{'='*72}")
        print(f"{m['uid']}  |  {m['title']}  [{m['status']}]")
        print(f"  settings   scope={st['scope']} mastery={st['mastery']} "
              f"start={st['starting_from']} style={st['teaching_style']!r}")
        print(f"  structure  {s['modules']}M / {s['units']}U / {s['lessons']}L / "
              f"{s['concepts']}C   degenerate lessons: "
              f"{s['degenerate_lessons']} ({s['degenerate_lesson_pct']}%)")
        print(f"  content    words min/med/max = {c['words_min']}/{c['words_median']}/"
              f"{c['words_max']}  stdev={c['words_stdev']}  total={c['words_total']}")
        print(f"             stubs={c['stub_count']}  missing={c['files_missing']}")
        print(f"  grounding  citations {g['citation_coverage_pct']}%  "
              f"sources-block {g['concepts_with_sources_block']}/{c['files_present']}  "
              f"{g['unique_sources']} unique URLs")
        print(f"             source_confidence mean={g['source_confidence_mean']} "
              f"(<0.5: {g['below_0.5']})")
        print(f"  bloom      {b['targets']}  span={b['span']}  "
              f"monotonic={b['monotonic_nondecreasing']}")
        dc = m.get("depth_contract")
        if dc:
            print(f"  depth      mastery={dc.get('mastery')} "
                  f"met={dc.get('met_pct')}%  "
                  f"level_verified={dc.get('level_verified')}")
        else:
            print("  depth      (no verdict — built before A1 enforcement)")

        fails = _gate_failures(m)
        if fails:
            print("  GATE: FAIL")
            for x in fails:
                print(f"    - {x}")
        else:
            print("  GATE: PASS")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote {args.json}")
    return 0


def _build(topic, scope, mastery, starting_from, style="", hydrate=True):
    """Build a course skeleton and (by default) hydrate its content.

    Hydration is where the A1 depth contract and A2 grounding actually run, so
    skipping it produces a course with no content files and nothing to measure
    — an earlier version of this harness did exactly that and reported a
    "course" whose 15 concepts were all missing.
    """
    from services.common.storage import StorageManager
    from services.core.course_builder import SkeletonBuilder, ContentHydrator

    storage = StorageManager(data_dir=DATA_DIR)

    def status(msg):
        print(f"    [build] {str(msg)[:110]}")

    sb = SkeletonBuilder(
        storage=storage,
        status_callback=status,
        scope=scope,
        mastery=mastery,
        starting_from=starting_from,
        teaching_style=style,
    )
    t0 = time.time()
    try:
        uid = sb.build(topic)
    finally:
        sb.close()
    if not uid or not hydrate:
        return uid, time.time() - t0

    print(f"    [hydrate] starting for {uid} (depth contract enforced)")
    hy = ContentHydrator(
        storage=storage,
        status_callback=status,
        course_depth=mastery,
        mastery=mastery,
    )
    try:
        hy.hydrate(uid)
    finally:
        hy.close()
    return uid, time.time() - t0


def cmd_generate(args):
    print(f"Generating: {args.topic!r} scope={args.scope} mastery={args.mastery} "
          f"start={args.starting_from}")
    uid, secs = _build(args.topic, args.scope, args.mastery,
                       args.starting_from, args.style,
                       hydrate=not getattr(args, "no_hydrate", False))
    if not uid:
        print("BUILD FAILED — returned no course uid")
        return 1
    print(f"Built {uid} in {secs:.0f}s")
    args.course, args.json = uid, None
    return cmd_evaluate(args)


# The comparison matrix. Deliberately varies ONE axis at a time against a
# baseline so that a difference in output can be attributed to a setting.
MATRIX = [
    # (topic,                  scope, mastery, starting_from, style)
    ("causal inference",           2, 2, 1, ""),        # baseline, shallow
    ("causal inference",           5, 5, 1, ""),        # max depth, same topic
    ("causal inference",           3, 4, 2, "socratic"),# mid + style set
    ("photosynthesis",             2, 2, 1, ""),        # different domain, shallow
    ("photosynthesis",             5, 5, 1, ""),        # different domain, deep
    ("the french revolution",      4, 4, 1, ""),        # humanities domain
]


def cmd_matrix(args):
    print(f"Generating {len(MATRIX)} golden courses. This takes minutes each.\n")
    built = []
    for i, (topic, sc, ma, sf, style) in enumerate(MATRIX, 1):
        print(f"[{i}/{len(MATRIX)}] {topic!r} scope={sc} mastery={ma} start={sf}")
        try:
            uid, secs = _build(topic, sc, ma, sf, style)
            if uid:
                print(f"    -> {uid} ({secs:.0f}s)")
                built.append(uid)
            else:
                print("    -> FAILED (no uid returned)")
        except Exception as e:
            print(f"    -> FAILED: {e}")
    print(f"\nBuilt {len(built)}/{len(MATRIX)}.")
    args.course, args.json = None, args.json
    return cmd_evaluate(args)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser("evaluate", help="measure courses already on disk")
    ev.add_argument("--course", help="single course uid")
    ev.add_argument("--json", help="write full metrics to this path")
    ev.set_defaults(func=cmd_evaluate)

    ge = sub.add_parser("generate", help="build one course then measure it")
    ge.add_argument("--topic", required=True)
    ge.add_argument("--scope", type=int, default=3)
    ge.add_argument("--mastery", type=int, default=3)
    ge.add_argument("--starting-from", type=int, default=1, dest="starting_from")
    ge.add_argument("--style", default="")
    ge.add_argument("--no-hydrate", action="store_true",
                    help="skeleton only; skips the depth contract and grounding")
    ge.set_defaults(func=cmd_generate)

    mx = sub.add_parser("matrix", help="build the full comparison matrix")
    mx.add_argument("--json", help="write full metrics to this path")
    mx.set_defaults(func=cmd_matrix)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
