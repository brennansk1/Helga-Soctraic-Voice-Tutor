#!/usr/bin/env python3
"""tier_probe.py — is each mastery level ATTAINABLE? (cheap preset verification)

WHY THIS EXISTS
---------------
Scoring an existing course against every contract proves the levels are
DIFFERENT — a mastery-2 course scores 100% at level 2 and 0% at levels 4 and 5.
It proves nothing about whether anything can PASS level 5. Those are separate
claims, and a preset named "Graduate Seminar" asserts the second one.

Verifying that by building a course per preset costs ~2 min/concept, i.e. hours.
But the depth contract is applied PER CONCEPT, so a single concept generated at
a level answers the attainability question at ~1/30th the cost.

This is a probe, not a proof: one concept per tier says the level is reachable,
not that every concept will reach it. Use golden_courses.py for the latter.

    python3 tools/tier_probe.py                 # all five tiers
    python3 tools/tier_probe.py --mastery 5     # one tier
"""

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# One topic per tier that a real course at that level would plausibly contain,
# so the probe is not measuring "can the model write about something trivial".
TIER_TOPICS = {
    1: ("Right angles", "Basic geometry", "geometry"),
    2: ("The hypotenuse", "Right triangles", "the pythagorean theorem"),
    3: ("Propensity score matching", "Causal methods", "causal inference"),
    4: ("Instrumental variables and the exclusion restriction",
        "Identification strategies", "econometrics"),
    5: ("The spectral theorem for self-adjoint operators",
        "Spectral theory", "functional analysis"),
}


def probe(mastery, research_url, verbose=False):
    from services.core.course_builder import ContentHydrator
    from services.core.depth_contract import validate_concept, contract_for
    import requests

    title, module, course = TIER_TOPICS[mastery]

    # Real grounding, same call the hydrator makes.
    sources, reference = [], ""
    try:
        r = requests.post(f"{research_url}/api/research_concept",
                          json={"title": title, "module_title": module,
                                "course_title": course, "mastery": mastery},
                          timeout=90)
        if r.status_code == 200:
            d = r.json()
            sources = d.get("sources") or []
            reference = d.get("combined_text") or ""
    except Exception as e:
        print(f"    research unavailable: {e}")

    h = ContentHydrator.__new__(ContentHydrator)
    h.status_callback = None
    h.mastery_level = mastery
    h.storage = None
    h.model = None
    h.used_source_ids = set()

    t0 = time.time()
    body = h._condense_and_structure_content(
        title, reference, course, mastery, "core theory",
        "research+llm" if sources else "llm-only",
        hierarchy_context={"module": module},
        previous_concepts=[], module_concepts=[],
        research_sources=sources, research_confidence=0.5,
        user_note="", bloom_level=min(mastery + 1, 6),
        learning_objectives=[], prerequisite_titles=[],
        research_structured=None,
    )
    elapsed = time.time() - t0

    ok, problems, detail = validate_concept(body, mastery, course,
                                            sources=sources)
    c = contract_for(mastery, course)
    return {
        "mastery": mastery, "label": c["label"], "title": title,
        "ok": ok, "problems": problems, "words": detail["words"],
        "band": detail["word_band"], "missing": detail["missing"],
        "required": c["required"], "sources": len(sources),
        "seconds": round(elapsed), "body": body,
    }


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mastery", type=int, choices=[1, 2, 3, 4, 5])
    p.add_argument("--research-url",
                   default=os.getenv("RESEARCH_URL", "http://localhost:5006"))
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    tiers = [args.mastery] if args.mastery else [1, 2, 3, 4, 5]
    print("Tier attainability probe — one concept per level\n")
    results = []
    for m in tiers:
        print(f"  mastery {m} — generating…", flush=True)
        try:
            r = probe(m, args.research_url, args.verbose)
        except Exception as e:
            print(f"    FAILED: {e}")
            continue
        results.append(r)
        status = "PASS" if r["ok"] else "FAIL"
        print(f"    {status}  {r['label']:14} {r['words']:5}w "
              f"(band {r['band'][0]}-{r['band'][1]})  "
              f"{r['sources']} sources  {r['seconds']}s")
        if not r["ok"]:
            for prob in r["problems"][:4]:
                print(f"        - {prob}")

    print("\n" + "=" * 62)
    passed = sum(1 for r in results if r["ok"])
    print(f"  {passed}/{len(results)} tiers attainable")
    for r in results:
        mark = "ok " if r["ok"] else "FAIL"
        print(f"    {mark} mastery {r['mastery']} {r['label']:14} "
              f"requires {len(r['required'])} element(s)")
    if passed < len(results):
        print("\n  A tier that cannot be reached must not be offered as a preset.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
