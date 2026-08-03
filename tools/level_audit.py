#!/usr/bin/env python3
"""level_audit.py — does the content read at the level it CLAIMS? (sprint A1)

WHY THIS EXISTS (and why depth_contract.py is not enough)
---------------------------------------------------------
`services/core/depth_contract.py` checks for structural markers: a definition,
a derivation, a worked example, a primary source. That is useful for catching
the gross absence of rigor — it correctly showed that a course claiming
mastery=4 had worked examples in 0 of 36 concepts. But it measures FORM, not
SUBSTANCE, and it is demonstrably fooled in both directions:

    marker-stuffed nonsense at mastery=5 ....... PASSES (1020 words, nothing missing)
    genuine graduate prose without markers ..... FAILS  (5 elements "missing")

So passing the contract is not evidence of quality. This tool asks the harder
question directly: give a judge the content with NO knowledge of what level it
claims to be, ask what level it actually reads at, and compare.

That is the calibration question — "is a college-level setting producing
college-level work?" — expressed as something measurable.

METHOD
    1. Read concept bodies from a course on disk.
    2. Strip metadata that leaks the claimed level (the Metadata block names
       the Bloom target and depth; leaving it in would let the judge simply
       read off the answer).
    3. Ask a judge to classify the academic level 1-5 and justify it.
    4. Compare judged level against the course's claimed mastery.
    5. Repeat and report dispersion — LLM judges are noisy. Measured on this
       repo's other benchmark, identical runs swung ±1.4 on a 5-point scale,
       so a single pass is not evidence.

LEVELS (deliberately worded as recognisable audiences, not as our own jargon,
so the judge is not primed with our vocabulary)
    1 popular / general audience     2 high school
    3 undergraduate                  4 advanced undergrad / masters
    5 graduate / research

USAGE
    python3 tools/level_audit.py --course course_10e8a4de
    python3 tools/level_audit.py --course X --sample 8 --repeat 3
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

JUDGE = """You classify the academic level of teaching material. Be strict and
concrete. Judge the SUBSTANCE, not the formatting — headings, bullet points,
bold text and citation lists tell you nothing about level.

Levels:
1 = popular science / general audience explainer
2 = high school
3 = undergraduate course
4 = advanced undergraduate or masters
5 = graduate / research level

Ask yourself: what does the reader have to already know? Does it derive or
merely assert? Does it handle edge cases, assumptions, failure modes? Is there
anything a competent undergraduate would find new?

Material that only describes a topic in careful prose — no derivation, no
non-trivial worked example, no engagement with when the method fails — is
level 3 at most, however polished it reads.

Return STRICT JSON only:
{"level": n, "reason": "<one sentence>", "hardest_thing_required": "<the most
advanced prerequisite a reader genuinely needs, or 'none'>"}"""


def _strip_level_hints(body):
    """Remove blocks that state the level outright.

    The Metadata block contains 'Bloom Target' and 'Depth', and Mastery
    Criteria restates the Bloom level. Leaving those in lets the judge read the
    answer off the page instead of assessing the writing.
    """
    out = re.sub(r"^## Metadata\n.*?(?=\n## |\Z)", "", body,
                 flags=re.DOTALL | re.MULTILINE)
    out = re.sub(r"^## Mastery Criteria\n.*?(?=\n## |\Z)", "", out,
                 flags=re.DOTALL | re.MULTILINE)
    out = re.sub(r"^## Sources\n.*?(?=\n## |\Z)", "", out,
                 flags=re.DOTALL | re.MULTILINE)
    out = re.sub(r"(?i)bloom[^\n]*", "", out)
    return out.strip()


def _judge_one(url, model, text):
    import requests
    try:
        r = requests.post(
            f"{url}/v1/chat/completions",
            json={"model": model,
                  "messages": [{"role": "system", "content": JUDGE},
                               {"role": "user", "content": text[:6000]}],
                  "max_tokens": 400, "temperature": 0.2, "stream": False,
                  "reasoning_effort": "none"},
            timeout=180)
        if r.status_code != 200:
            return None
        c = r.json()["choices"][0]["message"].get("content") or ""
        c = re.sub(r"<think>.*?</think>", "", c, flags=re.DOTALL)
        d = json.loads(c[c.find("{"):c.rfind("}") + 1])
        lvl = int(d.get("level", 0))
        if not 1 <= lvl <= 5:
            return None
        return {"level": lvl,
                "reason": str(d.get("reason", ""))[:200],
                "requires": str(d.get("hardest_thing_required", ""))[:120]}
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", required=True)
    p.add_argument("--sample", type=int, default=6,
                   help="concepts to judge (default 6)")
    p.add_argument("--repeat", type=int, default=2,
                   help="judgements per concept; LLM judges are noisy")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--out")
    args = p.parse_args()

    cdir = os.path.join(COURSES_DIR, args.course)
    spath = os.path.join(cdir, "structure.json")
    if not os.path.exists(spath):
        print(f"No such course: {args.course}")
        return 1
    with open(spath) as f:
        struct = json.load(f)

    claimed = struct.get("mastery")
    title = struct.get("title")
    files = sorted(os.listdir(os.path.join(cdir, "content")))
    files = [f for f in files if f.endswith(".md")]
    # Even spread through the course, so we don't only sample the intro.
    step = max(1, len(files) // max(1, args.sample))
    picked = files[::step][:args.sample]

    print(f"Course: {title!r}  claimed mastery = {claimed}")
    print(f"Judging {len(picked)} concepts x {args.repeat} repeats "
          f"(judge is blind to the claimed level)\n")

    rows = []
    for fn in picked:
        with open(os.path.join(cdir, "content", fn)) as f:
            body = _strip_level_hints(f.read())
        verdicts = [v for v in (_judge_one(args.url, args.model, body)
                                for _ in range(args.repeat)) if v]
        if not verdicts:
            print(f"  {fn[:22]:24} judge failed")
            continue
        lv = [v["level"] for v in verdicts]
        mean = round(statistics.mean(lv), 2)
        rows.append({"file": fn, "levels": lv, "mean": mean,
                     "reason": verdicts[0]["reason"],
                     "requires": verdicts[0]["requires"]})
        print(f"  {fn[:22]:24} judged {lv} -> {mean}")
        print(f"      {verdicts[0]['reason'][:100]}")

    if not rows:
        print("\nNo judgements completed — is Ollama running?")
        return 1

    means = [r["mean"] for r in rows]
    overall = round(statistics.mean(means), 2)
    sd = round(statistics.pstdev(means), 2) if len(means) > 1 else 0.0

    print("\n" + "=" * 68)
    print(f"  claimed mastery level : {claimed}")
    print(f"  judged level (blind)  : {overall}   (sd {sd}, n={len(means)})")
    if claimed:
        gap = round(overall - claimed, 2)
        print(f"  calibration gap       : {gap:+}")
        if gap <= -1:
            print("\n  VERDICT: the content reads BELOW the level it claims.")
            print("  A course advertised at this level is not delivering it.")
        elif gap >= 1:
            print("\n  VERDICT: content reads ABOVE its claimed level "
                  "(may be inaccessible to the intended learner).")
        else:
            print("\n  VERDICT: content is broadly calibrated to its claim.")
    print(f"\n  Note: judged levels are noisy. With n={len(means)} concepts x "
          f"{args.repeat} repeats,\n  treat gaps smaller than ~0.5 as inconclusive.")

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"course": args.course, "claimed": claimed,
                       "judged": overall, "sd": sd, "rows": rows}, f, indent=2)
        print(f"\n  Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
