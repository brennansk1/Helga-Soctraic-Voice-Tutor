#!/usr/bin/env python3
"""substance_check.py — is the rigor REAL, or just the markers? (gate criterion 3)

WHY
---
`depth_contract.py` checks that a concept contains a definition, a derivation,
a worked example, a primary source. That is a form check, and it is defeatable
— demonstrated in this repo:

    marker-stuffed nonsense at mastery 5 ... PASSES the contract (1020 words)
    genuine graduate prose without markers .. FAILS it

So criterion 1 of the quality gate can be satisfied by sprinkling the words
"Proof:", "Step 1:" and an arXiv link over filler. This tool closes that hole
by asking whether the rigor is actually there:

  * does the derivation actually derive the result, or just gesture at it?
  * does the worked example carry concrete values through to an answer?
  * is the named theorem a real result, correctly stated?
  * is the definition precise, or a circular restatement?
  * would a competent student LEARN something they could then apply?

Judged per concept, with a schema-constrained JSON response so smaller models
cannot break the parse.

VALIDATION OF THE INSTRUMENT ITSELF
    python3 tools/substance_check.py --self-test
runs the judge against known-good and known-garbage samples. Given how often
an instrument in this project has turned out to measure the wrong thing, a
checker that cannot detect deliberate nonsense should not be trusted on real
content.
"""

import argparse
import json
import os
import re
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DATA_DIR = os.path.join(_ROOT, "data")
COURSES_DIR = os.path.join(DATA_DIR, "courses")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning_is_real": {"type": "boolean"},
        "example_is_worked": {"type": "boolean"},
        "claims_are_correct": {"type": "boolean"},
        "is_filler": {"type": "boolean"},
        "teaches_something": {"type": "boolean"},
        "verdict": {"type": "string", "enum": ["SUBSTANTIVE", "HOLLOW"]},
        "evidence": {"type": "string"},
    },
    "required": ["verdict", "is_filler", "teaches_something", "evidence"],
}

JUDGE = """You are checking whether teaching material has REAL substance or
merely looks rigorous. Be adversarial: assume it may be padded.

This material is deliberately a SHORT micro-lesson, so do not mark it hollow
for being brief, or for lacking lecture-style exposition. Judge the substance.

Check each, honestly:
- reasoning_is_real: if it claims a derivation/proof, does it actually reason
  from premises to a conclusion? A sentence containing "therefore" is not a
  derivation.
- example_is_worked: if it offers an example, does it carry concrete values or
  steps through to a result? Naming a real-world application is NOT a worked
  example.
- claims_are_correct: is everything asserted factually and technically true?
  Any invented theorem, wrong formula or false statement makes this false.
- is_filler: is it padded with jargon, restatement, or empty scaffolding that
  conveys nothing?
- teaches_something: could a competent student come away able to DO or apply
  something they could not before?

verdict = HOLLOW if is_filler is true, or teaches_something is false, or
claims_are_correct is false. Otherwise SUBSTANTIVE.

evidence: one short quote or phrase justifying the verdict.

Return STRICT JSON only, matching the requested schema."""


# --- self-test fixtures -------------------------------------------------------
GARBAGE = ("""**Definition** Let X be a thing. Formally, a widget is defined as a widget.
Theorem (Widget): widgets are widgetful. Proof: therefore it follows that we obtain
the widget, hence the claim. Step 1: we compute the widget. Substituting into $Wx = w$
we obtain 42. Exercise: try it yourself. See https://arxiv.org/abs/0000.00000
""" * 12)

GOOD = """## Trimming in Propensity Score Matching

**Definition.** Given estimated propensity scores e(x) = P(T=1 | X=x), trimming
discards units whose scores fall outside [a, 1-a] for a chosen a.

**Why.** Units with e(x) near 0 or 1 have almost no counterfactual counterpart,
so their weights 1/e(x) or 1/(1-e(x)) explode and inflate variance.

**Worked example.** Take a = 0.1 and a treated unit with e(x) = 0.02. Its IPW
weight is 1/0.02 = 50, versus a weight of about 2 for a typical unit at
e(x) = 0.5. That single observation would carry roughly 25 times the influence
of a typical one, so trimming removes it.

**Trade-off.** Trimming reduces variance but changes the estimand: you now
estimate the effect on the overlap population, not the original one. Report
which population the estimate refers to.
"""


def _judge(url, model, text):
    import requests
    try:
        r = requests.post(
            f"{url}/v1/chat/completions",
            json={"model": model,
                  "messages": [{"role": "system", "content": JUDGE},
                               {"role": "user", "content": text[:6000]}],
                  "max_tokens": 500, "temperature": 0.0, "stream": False,
                  "reasoning_effort": "none",
                  # Constrained decoding: smaller models otherwise emit
                  # unescaped quotes inside "evidence" and break the parse.
                  "format": SCHEMA},
            timeout=180)
        if r.status_code != 200:
            return None
        c = r.json()["choices"][0]["message"].get("content") or ""
        c = re.sub(r"<think>.*?</think>", "", c, flags=re.DOTALL)
        from services.common.llm_utils import parse_llm_json
        return parse_llm_json(c, expected_type="dict")
    except Exception:
        return None


def _strip_meta(body):
    for sec in ("Metadata", "Mastery Criteria", "Sources"):
        body = re.sub(rf"^## {sec}\n.*?(?=\n## |\Z)", "", body,
                      flags=re.DOTALL | re.MULTILINE)
    return body.strip()


def self_test(url, model, repeat=3):
    """The checker must reject deliberate nonsense and accept real content."""
    print("Self-test — can this instrument tell substance from filler?\n")
    ok = True
    for name, text, expect in (("marker-stuffed garbage", GARBAGE, "HOLLOW"),
                               ("genuine worked content", GOOD, "SUBSTANTIVE")):
        verdicts = []
        for _ in range(repeat):
            d = _judge(url, model, text)
            if d and d.get("verdict"):
                verdicts.append(d["verdict"].upper())
        if not verdicts:
            print(f"  {name:26} judge failed — cannot validate")
            ok = False
            continue
        hits = sum(1 for v in verdicts if v == expect)
        passed = hits > len(verdicts) / 2
        ok = ok and passed
        print(f"  {name:26} {verdicts}  expected {expect}  "
              f"{'PASS' if passed else 'FAIL'}")
    print("\n  " + ("Instrument is usable." if ok else
                    "INSTRUMENT UNRELIABLE — do not trust its verdicts on real content."))
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course")
    p.add_argument("--sample", type=int, default=6)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--out")
    args = p.parse_args()

    if args.self_test:
        return self_test(args.url, args.model)
    if not args.course:
        print("--course is required (or use --self-test)")
        return 2

    cdir = os.path.join(COURSES_DIR, args.course, "content")
    if not os.path.isdir(cdir):
        print(f"No content for {args.course}")
        return 1
    files = sorted(f for f in os.listdir(cdir) if f.endswith(".md"))
    step = max(1, len(files) // max(1, args.sample))
    picked = files[::step][:args.sample]

    print(f"Substance check — {args.course}, {len(picked)} concepts\n")
    rows, substantive, total = [], 0, 0
    for fn in picked:
        with open(os.path.join(cdir, fn)) as f:
            body = _strip_meta(f.read())
        verds = []
        for _ in range(args.repeat):
            d = _judge(args.url, args.model, body)
            if d and d.get("verdict"):
                verds.append(d)
        if not verds:
            print(f"  {fn[:22]:24} judge failed")
            continue
        subs = sum(1 for d in verds if d["verdict"].upper() == "SUBSTANTIVE")
        is_sub = subs > len(verds) / 2
        total += 1
        substantive += 1 if is_sub else 0
        rows.append({"file": fn, "substantive": is_sub,
                     "evidence": verds[0].get("evidence", "")[:180]})
        print(f"  {fn[:22]:24} {'SUBSTANTIVE' if is_sub else 'HOLLOW'}")
        print(f"      {verds[0].get('evidence','')[:100]}")

    if not total:
        print("\nNo verdicts — is Ollama running?")
        return 1
    pct = round(100 * substantive / total, 1)
    print("\n" + "=" * 62)
    print(f"  SUBSTANTIVE: {substantive}/{total} = {pct}%")
    print(f"  Gate criterion 3 target: >=80%")
    print(f"  STATUS: {'PASS' if pct >= 80 else 'FAIL'}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"course": args.course, "pct": pct, "rows": rows}, f, indent=2)
        print(f"\n  Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
