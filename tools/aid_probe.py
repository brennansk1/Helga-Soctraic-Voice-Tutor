#!/usr/bin/env python3
"""aid_probe.py — can THIS model actually draw? Measured, not assumed.

WHY THIS EXISTS
---------------
Visual aids ask the model for something it is not asked for anywhere else in
this system: nested JSON with cross-references (a segment naming two points that
must both exist), emitted INSIDE a conversational message, with no retry and no
constrained decoding. The grading path can use Ollama's `format` schema and
retry through `llm_generate_json()`. A fence embedded mid-prose can use neither.

So the failure mode is not a broken screen — a malformed aid is dropped and the
message is delivered intact. The failure mode is the feature QUIETLY NEVER
FIRING, which is precisely the class of problem this repo keeps discovering
late (the research service that never started; criterion 6 that never ran).

This probe answers three questions with numbers:

  1. EMISSION  — when a diagram would obviously help, does the model draw one?
     A model that never emits a fence is the silent failure.
  2. PARSE     — is the JSON well-formed?
  3. VALIDITY  — does it survive normalize_aid(), and if not, why?

Reported per KIND, because the kinds are not equally hard and the right response
to a bad score is usually to retire one kind, not the feature. `geometry` needs
coordinate reasoning and cross-referenced point names; `steps` needs a list of
strings. Expect those to score very differently.

WHAT THIS DOES NOT MEASURE
--------------------------
Semantic correctness. A triangle can be perfectly valid JSON, pass every check
here, and not be right-angled. No validator catches that — only a human or a
vision pass looking at the rendered figure. Read a HIGH score here as "the model
can express itself", never as "the diagrams are correct".

USAGE
    python3 tools/aid_probe.py                    # all cases, 1 attempt each
    python3 tools/aid_probe.py --repeat 3         # 3 attempts (JSON is stochastic)
    python3 tools/aid_probe.py --kind geometry    # just the hard one
    python3 tools/aid_probe.py --mode tool        # tool-calling instead of the fence
"""

import argparse
import json
import os
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.common.prompts import VISUAL_AID_RULES          # noqa: E402
from services.common.visual_aids import extract_aids, normalize_aid  # noqa: E402

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# One case per kind: a teaching moment where that diagram is the obvious move.
# The prompts describe the SITUATION, never the JSON — measuring whether the
# model reaches for the right tool unprompted is half the point.
CASES = [
    {"kind": "number_line", "topic": "inequalities",
     "ask": "A student is confused about why x > 2 and x <= 5 overlap. Ask them one question that makes the overlap visible."},
    {"kind": "geometry", "topic": "Pythagoras",
     "ask": "A student is about to meet the Pythagorean theorem for the first time, using a 3-4-5 triangle. Ask the question that starts them off. Do not state the theorem."},
    {"kind": "plot", "topic": "quadratics",
     "ask": "A student thinks y = x squared is a straight line. Ask one question that confronts this."},
    {"kind": "bars", "topic": "data handling",
     "ask": "Rainfall was Mon 4cm, Tue 7, Wed 3, Thu 9, Fri 6. Ask the student one question about the pattern."},
    {"kind": "graph", "topic": "photosynthesis",
     "ask": "A student has just learned the inputs and outputs of photosynthesis. Ask one question about how they connect."},
    {"kind": "timeline", "topic": "history of science",
     "ask": "A student is placing Newton's Principia (1687), Darwin's Origin (1859) and the DNA structure (1953). Ask one question about the spacing between them."},
    {"kind": "table", "topic": "cell division",
     "ask": "A student keeps mixing up mitosis and meiosis. Ask one question that forces them to separate the two."},
    {"kind": "venn", "topic": "classification",
     "ask": "A student says a whale is a fish. Ask one question that surfaces the contradiction."},
    {"kind": "cycle", "topic": "water cycle",
     "ask": "A student thinks rain comes from nowhere. Ask one question about where it comes from."},
    {"kind": "steps", "topic": "algebra method",
     "ask": "A student solved sqrt(x) = 4 and got x = 2. Ask one question about their method."},
    {"kind": "fraction", "topic": "comparing fractions",
     "ask": "A student says 3/8 is bigger than 1/2 because 8 is bigger than 2. Ask one question that confronts this."},
]


def call_ollama(url, model, system, user, timeout=120):
    import requests
    r = requests.post(f"{url}/api/chat", timeout=timeout, json={
        "model": model, "stream": False,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "options": {"temperature": 0.4},
    })
    r.raise_for_status()
    return (r.json().get("message") or {}).get("content", "")


def probe_fence(url, model, case):
    """The production path: rules in the system prompt, fence in the reply."""
    system = ("You are a Socratic tutor. Ask exactly ONE question. Keep it under "
              "60 words.\n\n" + VISUAL_AID_RULES)
    text = call_ollama(url, model, system, case["ask"])
    clean, aids, errors = extract_aids(text)
    return text, clean, aids, errors


def probe_tool(url, model, case):
    """The tool-calling path, for comparison. Some models emit clean structured
    arguments far more reliably than clean JSON inside prose — if the gap is
    large, that is an argument for routing aids through tools despite the cost."""
    import requests
    from services.common.tutor_tools import build_default_registry
    captured = []
    registry = build_default_registry(aid_sink=captured.append)
    r = requests.post(f"{url}/api/chat", timeout=120, json={
        "model": model, "stream": False,
        "messages": [
            {"role": "system", "content": "You are a Socratic tutor. Ask exactly ONE "
                                          "question. Draw a diagram when it helps."},
            {"role": "user", "content": case["ask"]}],
        "tools": registry.to_ollama_tools(model),
        "options": {"temperature": 0.4},
    })
    r.raise_for_status()
    msg = (r.json().get("message") or {})
    errors = []
    for call in (msg.get("tool_calls") or []):
        fn = call.get("function", {})
        out = registry.execute(model, fn.get("name", ""), fn.get("arguments") or {})
        if not out.get("ok"):
            errors.append(out.get("error", "?"))
        else:
            try:
                payload = json.loads(out["result"])
                if not payload.get("shown_to_student"):
                    errors.append(payload.get("error", "?"))
            except (json.JSONDecodeError, KeyError):
                pass
    return msg.get("content", ""), msg.get("content", ""), captured, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--repeat", type=int, default=1,
                    help="attempts per case (JSON emission is stochastic; 3+ for a real number)")
    ap.add_argument("--kind", help="probe a single kind")
    ap.add_argument("--mode", choices=["fence", "tool"], default="fence")
    ap.add_argument("--json", dest="as_json", action="store_true", help="machine-readable output")
    ap.add_argument("--verbose", action="store_true", help="print each rejected payload")
    args = ap.parse_args()

    cases = [c for c in CASES if not args.kind or c["kind"] == args.kind]
    if not cases:
        print(f"No case for kind '{args.kind}'. Known: {sorted({c['kind'] for c in CASES})}")
        return 2

    runner = probe_fence if args.mode == "fence" else probe_tool
    stats = defaultdict(lambda: {"runs": 0, "emitted": 0, "valid": 0,
                                 "right_kind": 0, "staged": 0, "errors": []})
    leaked = 0

    print(f"aid_probe · model={args.model} · mode={args.mode} · "
          f"{len(cases)} cases × {args.repeat}\n")
    for case in cases:
        st = stats[case["kind"]]
        for _ in range(args.repeat):
            st["runs"] += 1
            try:
                raw, clean, aids, errors = runner(args.url, args.model, case)
            except Exception as e:
                st["errors"].append(f"call failed: {e}")
                print(f"  {case['kind']:<12} ERROR  {e}")
                continue

            # A leak is the one outcome that reaches the learner as damage:
            # raw JSON left sitting in the chat.
            if "```aid" in clean or '"kind"' in clean:
                leaked += 1

            if aids:
                st["emitted"] += 1
                st["valid"] += 1
                if aids[0]["kind"] == case["kind"]:
                    st["right_kind"] += 1
                if aids[0].get("stages_total", 0) > 0:
                    st["staged"] += 1
                mark = "OK " if aids[0]["kind"] == case["kind"] else "alt"
                print(f"  {case['kind']:<12} {mark}  drew {aids[0]['kind']}"
                      f"{'  (staged)' if aids[0].get('stages_total') else ''}")
            elif errors:
                st["emitted"] += 1        # it tried; the payload was bad
                st["errors"].extend(errors)
                print(f"  {case['kind']:<12} BAD  {errors[0][:70]}")
                if args.verbose:
                    print(f"       raw: {raw[:400]}")
            else:
                print(f"  {case['kind']:<12} --   no diagram attempted")
                if args.verbose:
                    print(f"       raw: {raw[:200]}")

    runs = sum(s["runs"] for s in stats.values())
    emitted = sum(s["emitted"] for s in stats.values())
    valid = sum(s["valid"] for s in stats.values())
    right = sum(s["right_kind"] for s in stats.values())

    if args.as_json:
        print(json.dumps({"model": args.model, "mode": args.mode, "runs": runs,
                          "emitted": emitted, "valid": valid, "right_kind": right,
                          "leaked": leaked,
                          "by_kind": {k: dict(v) for k, v in stats.items()}}, indent=2))
        return 0

    print("\n" + "─" * 66)
    print(f"{'kind':<14}{'tried':>7}{'valid':>7}{'right kind':>12}{'staged':>8}")
    print("─" * 66)
    for kind in sorted(stats):
        s = stats[kind]
        print(f"{kind:<14}{s['emitted']}/{s['runs']:<5}{s['valid']:>5}  "
              f"{s['right_kind']:>9}   {s['staged']:>6}")
    print("─" * 66)
    print(f"{'TOTAL':<14}{emitted}/{runs:<5}{valid:>5}  {right:>9}")
    print()
    print(f"  emission rate : {emitted}/{runs} ({pct(emitted, runs)})  "
          "— did it reach for a diagram at all?")
    print(f"  validity rate : {valid}/{emitted or 1} ({pct(valid, emitted)})  "
          "— of those, how many survived validation?")
    print(f"  leaked JSON   : {leaked}  "
          "— MUST be 0; anything else is raw JSON shown to a learner")

    bad = [e for s in stats.values() for e in s["errors"]]
    if bad:
        print("\n  rejection reasons (what to fix in the prompt):")
        counts = defaultdict(int)
        for e in bad:
            counts[e.split("(")[0].strip()[:60]] += 1
        for reason, n in sorted(counts.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {n:>3}×  {reason}")

    print("\n  NOTE: this measures whether the model can EXPRESS a diagram, not "
          "whether\n        the diagram is correct. A valid triangle may still not be "
          "right-angled.")
    return 0


def pct(a, b):
    return f"{(100.0 * a / b):.0f}%" if b else "n/a"


if __name__ == "__main__":
    sys.exit(main())
