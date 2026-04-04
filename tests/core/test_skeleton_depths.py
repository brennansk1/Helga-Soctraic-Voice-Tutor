#!/usr/bin/env python3
"""
Integration test: Run SkeletonBuilder with the REAL LLM at all depth levels (1-5)
for a given topic. Uses a mock DB so kuzu is NOT required.

Tests: prompt leakage, fallback patterns, vague titles, duplicates, structure sizing.

Usage:
    # All depths
    python tests/core/test_skeleton_depths.py

    # Single depth
    python tests/core/test_skeleton_depths.py --depth 3

    # Custom topic
    python tests/core/test_skeleton_depths.py --topic "Quantum Mechanics"

    # Custom LLM endpoint
    LLM_API_URL=http://localhost:8080/v1/chat/completions python tests/core/test_skeleton_depths.py

Requires:
    - LLM endpoint running (set LLM_API_URL or default http://helga-qwen-engine:8080/v1/chat/completions)
"""

import sys
import os
import json
import re
import argparse
import time
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Mock kuzu so we don't need the native library
sys.modules['kuzu'] = MagicMock()
sys.modules['libzim'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()

from services.core.course_builder import SkeletonBuilder

# ============================================================
# Quality validation rules
# ============================================================

PROMPT_LEAKAGE_EXACT = {
    'unit title', 'lesson title', 'concept title', 'module name',
    'objective 1', 'objective 2',
    'fundamental axioms', 'systemic dynamics', 'complex synthesis',
    'symbolic language', 'compositional structure', 'stylistic evolution',
    'theory layer 1', 'mechanism layer 2', 'synthesis layer 3',
    'principle a', 'variable b', 'model c',
    'component x', 'component y', 'component z',
}

FALLBACK_PATTERNS = [
    r'^.*component \d+$',
    r'^key analysis \d+$',
    r'^core specific \d+$',
    r'^unit \d+$',
    r'^lesson \d+$',
    r'^concept \d+$',
    r'^module \d+$',
    r'^.*extension$',
]

VAGUE_SINGLE_WORDS = {
    'introduction', 'overview', 'summary', 'basics', 'advanced',
    'review', 'conclusion', 'miscellaneous', 'other', 'general',
}

DEPTH_LABELS = {
    1: "Introductory / Basic Access",
    2: "Undergraduate / Intermediate",
    3: "Graduate / Advanced technical",
    4: "Postgraduate / Professional Expert",
    5: "Doctoral / Research-level Theoretical",
}

# Minimum structure sizing per depth
EXPECTED_MINS = {
    1: (2, 2, 2, 4),      # (modules, units, lessons, concepts)
    2: (3, 3, 3, 9),
    3: (3, 6, 10, 30),
    4: (3, 6, 10, 40),
    5: (4, 10, 20, 80),
}


# ============================================================
# Structure extraction from mock DB calls
# ============================================================

def extract_structure_from_mock(mock_conn):
    """Reconstruct course hierarchy from captured mock DB execute calls."""
    structure = {"course": None, "modules": []}
    modules, units, lessons, concepts = {}, {}, {}, {}

    for call in mock_conn.execute.call_args_list:
        if not call.args:
            continue
        query = call.args[0]
        params = call.args[1] if len(call.args) > 1 else {}

        if "CREATE (c:Course" in query:
            structure["course"] = {"uid": params.get("uid", ""), "title": params.get("t", "")}

        elif "CREATE (m:Module" in query:
            m = {"uid": params.get("uid", ""), "title": params.get("t", ""),
                 "ordinal": params.get("o", 0), "units": []}
            modules[m["uid"]] = m
            structure["modules"].append(m)

        elif "CREATE (u:Unit" in query:
            u = {"uid": params.get("uid", ""), "title": params.get("t", ""),
                 "ordinal": params.get("o", 0), "lessons": []}
            units[u["uid"]] = u

        elif "HAS_UNIT" in query:
            m_uid, u_uid = params.get("mid", ""), params.get("uid", "")
            if m_uid in modules and u_uid in units:
                modules[m_uid]["units"].append(units[u_uid])

        elif "CREATE (l:Lesson" in query:
            l = {"uid": params.get("uid", ""), "title": params.get("t", ""),
                 "ordinal": params.get("o", 0), "concepts": []}
            lessons[l["uid"]] = l

        elif "HAS_LESSON" in query:
            u_uid, l_uid = params.get("uid", ""), params.get("lid", "")
            if u_uid in units and l_uid in lessons:
                units[u_uid]["lessons"].append(lessons[l_uid])

        elif "CREATE (c:Concept" in query:
            con = {"uid": params.get("uid", ""), "title": params.get("t", ""),
                   "objectives": params.get("obj", "[]")}
            concepts[con["uid"]] = con

        elif "HAS_CONCEPT" in query:
            l_uid, c_uid = params.get("lid", ""), params.get("cid", "")
            if l_uid in lessons and c_uid in concepts:
                lessons[l_uid]["concepts"].append(concepts[c_uid])

    return structure


def collect_titles(structure):
    """Flatten all titles from nested structure."""
    titles = []
    for m in structure.get("modules", []):
        titles.append(("Module", m["title"]))
        for u in m.get("units", []):
            titles.append(("Unit", u["title"]))
            for l in u.get("lessons", []):
                titles.append(("Lesson", l["title"]))
                for c in l.get("concepts", []):
                    titles.append(("Concept", c["title"]))
    return titles


# ============================================================
# Validation
# ============================================================

def validate(titles, depth, topic):
    """Run quality checks. Returns (passed, failed, issues)."""
    issues = []
    passed = failed = 0

    # 1. Prompt leakage
    leaked = []
    for level, title in titles:
        t = title.lower().strip()
        if t in PROMPT_LEAKAGE_EXACT:
            leaked.append(f"    {level}: \"{title}\" → EXACT PROMPT EXAMPLE")
        for pat in FALLBACK_PATTERNS:
            if re.match(pat, t):
                leaked.append(f"    {level}: \"{title}\" → FALLBACK ({pat})")
    if leaked:
        failed += 1; issues.append(f"  ❌ PROMPT LEAKAGE ({len(leaked)}):")
        issues.extend(leaked)
    else:
        passed += 1; issues.append(f"  ✅ No prompt leakage ({len(titles)} titles clean)")

    # 2. Vague single-word titles
    vague = [(lv, t) for lv, t in titles if t.lower().strip() in VAGUE_SINGLE_WORDS]
    if vague:
        failed += 1; issues.append(f"  ❌ VAGUE TITLES ({len(vague)}):")
        for lv, t in vague: issues.append(f"    {lv}: \"{t}\"")
    else:
        passed += 1; issues.append(f"  ✅ No vague titles")

    # 3. Duplicates
    seen, dupes = {}, []
    for lv, t in titles:
        k = t.lower().strip()
        if k in seen: dupes.append(f"    {lv}: \"{t}\" (dup of {seen[k]})")
        seen[k] = f"{lv}: \"{t}\""
    if dupes:
        failed += 1; issues.append(f"  ❌ DUPLICATES ({len(dupes)}):")
        issues.extend(dupes)
    else:
        passed += 1; issues.append(f"  ✅ All {len(titles)} titles unique")

    # 4. Structure sizing
    counts = {}
    for lv, _ in titles:
        counts[lv] = counts.get(lv, 0) + 1
    mins = EXPECTED_MINS.get(depth, (2, 2, 2, 4))
    short = []
    for actual, expected, label in [
        (counts.get("Module", 0), mins[0], "Modules"),
        (counts.get("Unit", 0), mins[1], "Units"),
        (counts.get("Lesson", 0), mins[2], "Lessons"),
        (counts.get("Concept", 0), mins[3], "Concepts"),
    ]:
        if actual < expected:
            short.append(f"    {label}: {actual} < {expected} minimum")
    if short:
        failed += 1; issues.append(f"  ❌ STRUCTURE TOO SMALL:")
        issues.extend(short)
    else:
        passed += 1
        issues.append(f"  ✅ Structure sizing OK "
                      f"({counts.get('Module',0)}M {counts.get('Unit',0)}U "
                      f"{counts.get('Lesson',0)}L {counts.get('Concept',0)}C)")

    # 5. Substantive (multi-word, domain-specific)
    structural = {'part', 'unit', 'lesson', 'module', 'concept', 'section', 'chapter'}
    hollow = []
    for lv, t in titles:
        words = [w for w in t.lower().split() if len(w) > 3 and w not in structural]
        if not words:
            hollow.append(f"    {lv}: \"{t}\"")
    if hollow:
        failed += 1; issues.append(f"  ❌ NON-SUBSTANTIVE TITLES ({len(hollow)}):")
        issues.extend(hollow)
    else:
        passed += 1; issues.append(f"  ✅ All titles substantive")

    return passed, failed, issues


# ============================================================
# Print helpers
# ============================================================

def print_structure(structure, depth):
    label = DEPTH_LABELS.get(depth, "Unknown")
    course = structure.get("course", {}).get("title", "?")
    print(f"\n{'='*70}")
    print(f"DEPTH {depth}: {label} — \"{course}\"")
    print(f"{'='*70}")

    for m in structure["modules"]:
        print(f"\n  📦 Module {m['ordinal']}: {m['title']}")
        if not m.get("units"):
            print(f"     ⚠️  (no units)")
        for u in m.get("units", []):
            print(f"    📂 Unit {u['ordinal']}: {u['title']}")
            if not u.get("lessons"):
                print(f"       ⚠️  (no lessons)")
            for l in u.get("lessons", []):
                concepts = l.get("concepts", [])
                print(f"      📄 {l['title']}")
                if concepts:
                    if len(concepts) <= 4:
                        print(f"        🔹 {', '.join(c['title'] for c in concepts)}")
                    else:
                        for c in concepts:
                            print(f"        🔹 {c['title']}")
                else:
                    print(f"        ⚠️  (no concepts)")


def save_json(structure, depth, output_dir):
    clean = {"course": structure["course"]["title"], "depth": depth,
             "depth_label": DEPTH_LABELS.get(depth, "?"), "modules": []}
    for m in structure["modules"]:
        cm = {"title": m["title"], "ordinal": m["ordinal"], "units": []}
        for u in m.get("units", []):
            cu = {"title": u["title"], "ordinal": u["ordinal"], "lessons": []}
            for l in u.get("lessons", []):
                cl = {"title": l["title"], "ordinal": l["ordinal"],
                      "concepts": [{"title": c["title"]} for c in l.get("concepts", [])]}
                cu["lessons"].append(cl)
            cm["units"].append(cu)
        clean["modules"].append(cm)

    path = os.path.join(output_dir, f"depth_{depth}_structure.json")
    with open(path, 'w') as f:
        json.dump(clean, f, indent=2)
    return path


# ============================================================
# Run one depth
# ============================================================

def run_depth(topic, depth, output_dir):
    """Build at given depth with real LLM, mock DB. Return (structure, passed, failed, issues)."""

    # Set up mock kuzu
    kuzu_mock = sys.modules['kuzu']
    mock_result = MagicMock()
    mock_result.has_next.return_value = False
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_result
    kuzu_mock.Database.return_value = MagicMock()
    kuzu_mock.Connection.return_value = mock_conn

    print(f"\n{'#'*70}")
    print(f"  Building depth={depth} for \"{topic}\"...")
    print(f"{'#'*70}")

    start = time.time()
    try:
        builder = SkeletonBuilder(db_path="dummy", course_depth=depth)
        builder.build(topic, max_depth=depth)
        elapsed = time.time() - start
        print(f"  ⏱️  Build completed in {elapsed:.1f}s")
    except Exception as e:
        elapsed = time.time() - start
        print(f"  ❌ BUILD FAILED after {elapsed:.1f}s: {e}")
        import traceback; traceback.print_exc()
        return None, 0, 1, [f"  ❌ Build failed: {e}"]

    # Extract structure from captured DB calls
    structure = extract_structure_from_mock(mock_conn)
    titles = collect_titles(structure)
    print_structure(structure, depth)

    passed, failed, issues = validate(titles, depth, topic)
    json_path = save_json(structure, depth, output_dir)
    print(f"\n  📁 JSON saved: {json_path}")

    return structure, passed, failed, issues


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Integration test: SkeletonBuilder × real LLM at all depths")
    parser.add_argument("--topic", default="Causal Inference", help="Course topic (default: Causal Inference)")
    parser.add_argument("--depth", type=int, default=None, help="Single depth to test (1-5), omit for all")
    parser.add_argument("--output", default=None, help="Output directory for JSON files")
    args = parser.parse_args()

    depths = [args.depth] if args.depth else [1, 2, 3, 4, 5]
    output_dir = args.output or os.path.join(os.path.dirname(__file__), "skeleton_depth_results")
    os.makedirs(output_dir, exist_ok=True)

    llm_url = os.getenv("LLM_API_URL", "http://helga-qwen-engine:8080/v1/chat/completions")
    print(f"\n{'='*70}")
    print(f"  SKELETON BUILDER — REAL LLM INTEGRATION TEST")
    print(f"  Topic:  \"{args.topic}\"")
    print(f"  Depths: {depths}")
    print(f"  LLM:    {llm_url}")
    print(f"  Output: {output_dir}")
    print(f"{'='*70}")

    total_passed = total_failed = 0
    results = {}

    for depth in depths:
        structure, p, f, issues = run_depth(args.topic, depth, output_dir)
        print(f"\n  --- Depth {depth} Validation ---")
        for line in issues:
            print(line)
        total_passed += p; total_failed += f
        results[depth] = (structure, p, f, issues)

    # Summary
    print(f"\n{'='*70}")
    print(f"  FINAL SUMMARY")
    print(f"{'='*70}")
    for d in depths:
        s, p, f, _ = results[d]
        status = "✅ PASS" if f == 0 else "❌ FAIL"
        n = len(collect_titles(s)) if s else 0
        print(f"  Depth {d} ({DEPTH_LABELS.get(d,'?'):40s}): {status} ({p}/{p+f} checks, {n} titles)")

    print(f"\n  Total: {total_passed} passed, {total_failed} failed")
    print(f"  Output: {output_dir}")

    if total_failed > 0:
        print(f"\n  ⚠️  SOME CHECKS FAILED")
        sys.exit(1)
    else:
        print(f"\n  🎉 ALL CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
