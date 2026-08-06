#!/usr/bin/env python3
"""substance_duel.py — is the small model's content actually GOOD?

WHY THIS EXISTS
---------------
`model_gate` says qwen3.5:4b writes a perfect course: 6/6 on the depth
contract, 0.00 repetition, twice as fast as anything else. That is a FORM
score. Every detector in `depth_contract` is structural — formal_definition,
worked_example, derivation_or_proof, named_result, exercise — and
`substance_check.py` already demonstrates the contract is defeatable:

    marker-stuffed nonsense at mastery 5 ... PASSES the contract (1020 words)
    genuine graduate prose without markers .. FAILS it

Meanwhile the same model scored **2.93/5 on accuracy** in live dialogue,
the worst of any candidate. A model that is factually shaky when talking is
not obviously trustworthy when writing the course.

So this generates the SAME concepts with two models and judges the output on
the axes the gate cannot see: factual correctness, explanatory depth, and
whether the required elements are real or decorative.

WHAT MAKES A JUDGE TRUSTWORTHY HERE
-----------------------------------
Two things this repo learned the hard way:

  * A judge must be validated before its numbers are used. HelgaBench was
    found manufacturing scores — a missing key read as `int(data.get(d,0))`
    and clamped to 1, inventing the worst possible score out of silence.
  * A single LLM-judge call swings +/-2 on an identical transcript. The
    measured noise floor is +/-1.4 on a 5-point scale, so a single sample is
    not a measurement.

Hence: blind (the judge is not told which model wrote what), position-swapped
(A/B order alternates so a position bias cancels), and repeated.

    python3 tools/substance_duel.py --a qwen3.5:4b --b qwen3:14b
"""
import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Concepts with checkable facts — a judge can be wrong about style, but
# "Pythagoras applies only to right triangles" is verifiable.
CONCEPTS = [
    ("The Pythagorean Theorem", "Euclidean Geometry", "formal"),
    ("Natural Selection", "Evolutionary Biology", "empirical"),
    ("Hypothesis Testing", "Statistics", "formal"),
]

JUDGE_PROMPT = """You are grading two explanations of the same concept, written for a learner at an undergraduate level. They were produced by different systems. You do not know which.

CONCEPT: {title}

--- EXPLANATION A ---
{a}

--- EXPLANATION B ---
{b}

Grade each on three axes, 1-5:

1. factual_accuracy — Are the claims TRUE? Penalise heavily any statement that
   is wrong, subtly misstated, or a real-sounding fabrication (invented names,
   dates, results, or citations). A confident wrong claim is worse than an
   omission.
2. explanatory_depth — Does it explain WHY, or only assert WHAT? Does a worked
   example actually work through the reasoning, or just present a result?
3. substance_vs_markers — The text was required to contain a definition, a
   worked example and a derivation. Are those REAL, or present in name only —
   a heading with filler under it, a "proof" that proves nothing?

Also list any specific factual errors you find, quoting them.

Return ONLY JSON:
{{"a": {{"factual_accuracy": n, "explanatory_depth": n, "substance_vs_markers": n}},
  "b": {{"factual_accuracy": n, "explanatory_depth": n, "substance_vs_markers": n}},
  "errors_in_a": ["..."], "errors_in_b": ["..."],
  "better_overall": "a" or "b" or "tie"}}"""

AXES = ("factual_accuracy", "explanatory_depth", "substance_vs_markers")


def generate(model, url, title, course, domain, mastery):
    """Produce one concept document with `model`, exactly as the builder does."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("mg", os.path.join(ROOT, "tools/model_gate.py"))
    mg = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mg)
    except SystemExit:
        pass
    mg._install_mocks()
    from services.core.course_builder import ContentHydrator

    os.environ["HELGA_BUILD_MODEL"] = model
    os.environ["HELGA_BUILD_URL"] = url
    os.environ.pop("LLM_API_URL", None)

    h = ContentHydrator.__new__(ContentHydrator)
    h.status_callback = None
    h.mastery_level = mastery
    h.course_depth = mastery
    h.enforce_depth = False
    h.max_depth_retries = 0
    h.topic_domain = domain
    h._contract_failures = []
    h.source_document = ""
    return h._condense_and_structure_content(
        title, "", course, mastery, "core theory", "llm-only",
        hierarchy_context={"module": course, "unit": title, "lesson": title},
        bloom_level=min(mastery, 5),
        learning_objectives=[f"Understand {title}"],
        prerequisite_titles=[],
        research_sources=mg.GATE_SOURCES)


def judge(judge_model, url, title, text_a, text_b, timeout=600):
    import requests
    body = JUDGE_PROMPT.format(title=title, a=text_a[:9000], b=text_b[:9000])
    r = requests.post(url.rstrip("/") + "/v1/chat/completions", timeout=timeout, json={
        "model": judge_model,
        "messages": [{"role": "system", "content": "You return only JSON."},
                     {"role": "user", "content": body}],
        "max_tokens": 1500,
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
    })
    raw = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("judge returned no JSON")
    return json.loads(m.group(0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--judge", default=None,
                    help="defaults to --b (the larger model judges)")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--mastery", type=int, default=3)
    ap.add_argument("--out", default="docs/baselines/substance_duel.json")
    args = ap.parse_args()
    judge_model = args.judge or args.b

    print(f"\n  A: {args.a}\n  B: {args.b}\n  judge: {judge_model}\n")
    results = []
    for title, course, domain in CONCEPTS:
        print(f"  == {title}")
        docs = {}
        for label, model in (("a", args.a), ("b", args.b)):
            t0 = time.monotonic()
            try:
                docs[label] = generate(model, args.url, title, course, domain,
                                       args.mastery)
            except Exception as e:
                print(f"     {label}: generation failed: {type(e).__name__}: {e}")
                docs[label] = ""
            print(f"     {label} ({model[:34]}): {len(docs[label].split())}w "
                  f"{time.monotonic() - t0:.0f}s")
        if not docs["a"] or not docs["b"]:
            continue

        # POSITION-SWAPPED. An LLM judge favours whichever text it reads first
        # often enough to decide a close comparison, so each pair is judged in
        # both orders and the scores averaged back onto the right model.
        try:
            fwd = judge(judge_model, args.url, title, docs["a"], docs["b"])
            rev = judge(judge_model, args.url, title, docs["b"], docs["a"])
        except Exception as e:
            print(f"     judge failed: {type(e).__name__}: {e}")
            continue
        merged = {"title": title,
                  "a": {k: (fwd["a"][k] + rev["b"][k]) / 2 for k in AXES},
                  "b": {k: (fwd["b"][k] + rev["a"][k]) / 2 for k in AXES},
                  "errors_in_a": (fwd.get("errors_in_a", []) + rev.get("errors_in_b", [])),
                  "errors_in_b": (fwd.get("errors_in_b", []) + rev.get("errors_in_a", []))}
        results.append(merged)
        for lbl in ("a", "b"):
            print(f"     {lbl}: " + "  ".join(f"{k[:9]}={merged[lbl][k]:.1f}" for k in AXES))
        for e in merged["errors_in_a"][:2]:
            print(f"     ! A error: {str(e)[:88]}")
        for e in merged["errors_in_b"][:2]:
            print(f"     ! B error: {str(e)[:88]}")

    if not results:
        print("\n  no comparable results\n")
        return 1

    print("\n  " + "=" * 66)
    print(f"  {'':<24}{'A: ' + args.a[:18]:<24}{'B: ' + args.b[:18]}")
    for k in AXES:
        a = sum(r["a"][k] for r in results) / len(results)
        b = sum(r["b"][k] for r in results) / len(results)
        flag = "  <-- gap" if abs(a - b) >= 0.75 else ""
        print(f"  {k:<24}{a:<24.2f}{b:.2f}{flag}")
    ea = sum(len(r["errors_in_a"]) for r in results)
    eb = sum(len(r["errors_in_b"]) for r in results)
    print(f"  {'factual errors found':<24}{ea:<24}{eb}")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump({"a": args.a, "b": args.b, "judge": judge_model,
               "results": results}, open(args.out, "w"), indent=2)
    print(f"\n  wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
