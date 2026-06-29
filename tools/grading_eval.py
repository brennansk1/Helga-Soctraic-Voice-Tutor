#!/usr/bin/env python3
"""
grading_eval.py — Objective model-comparison harness for Helga's Socratic grader.

Compares LLM models (e.g. Qwen3-14B vs Qwen3.5-9B vs Qwen3.5-35B-A3B) on:
  - grading quality (does the model assign the right FSRS grade 1-4?)
  - JSON-parse reliability (does it return parseable JSON with a "grade"?)
  - latency + decode throughput (tokens/sec, when the backend reports it)

It drives each model through Helga's real grading prompt
(`services.common.prompts.get_socratic_grading_prompt`) via the project's own
`LLMClient`, so results reflect production behavior — not a toy prompt.

Grades are ORDINAL (1=Again, 2=Hard, 3=Good, 4=Easy), so this reports both
exact-match accuracy and within-1 accuracy.

Usage:
    python3 tools/grading_eval.py --models qwen3:14b,qwen3.5:9b
    python3 tools/grading_eval.py --models qwen3:14b --runs 3 --out results.json

The CLI imports and `--help`s cleanly with NO Ollama running. The connection to
Ollama is established lazily — only when cases are actually executed.
"""

import argparse
import json
import os
import statistics
import sys
import time

# --- Path setup so `services.*` imports resolve when run from the repo root ---
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "grading_eval_cases.json")


# ---------------------------------------------------------------------------
# Lazy imports — kept out of module top level so `--help` works without the
# `services` tree being importable and without any network dependency.
# ---------------------------------------------------------------------------

def _load_grading_prompt_fn():
    """Import and return get_socratic_grading_prompt (lazy)."""
    from services.common.prompts import get_socratic_grading_prompt
    return get_socratic_grading_prompt


def _load_llm_client_cls():
    """Import and return the LLMClient class (lazy)."""
    from services.core.llm_client import LLMClient
    return LLMClient


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def load_cases(path):
    """Load curated grading cases. Supports either a top-level list or a
    {"cases": [...]} wrapper. Each case must have concept/question/
    student_answer/expected_grade."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"] if isinstance(data, dict) else data
    cleaned = []
    for i, c in enumerate(cases):
        missing = [k for k in ("concept", "question", "student_answer",
                               "expected_grade") if k not in c]
        if missing:
            raise ValueError(f"Case {i} missing required fields: {missing}")
        cleaned.append(c)
    return cleaned


# ---------------------------------------------------------------------------
# Prompt construction + response parsing
# ---------------------------------------------------------------------------

def build_grading_messages(get_prompt_fn, case):
    """Build the grading messages list for a single case using Helga's
    production prompt builder."""
    return get_prompt_fn(
        concept=case["concept"],
        question=case["question"],
        user_answer=case["student_answer"],
        context_text=case.get("context_text", ""),
        bloom_level=case.get("bloom_level"),
    )


def messages_to_system_user(messages):
    """LLMClient.chat() takes a system_prompt + user_message. The grading prompt
    is a single system message, so we route the grading instruction as the
    system prompt and supply a short user trigger to elicit the JSON verdict."""
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    user_parts = [m["content"] for m in messages if m.get("role") == "user"]
    system_prompt = "\n\n".join(system_parts).strip()
    user_message = "\n\n".join(user_parts).strip() or "Grade the student's answer now. Output JSON only."
    return system_prompt, user_message


def extract_grade(raw_text):
    """Parse a model's grading response.

    Returns (grade:int|None, json_ok:bool). json_ok is True only when the text
    parsed as JSON AND contained a usable integer "grade" in 1-4.
    """
    if not raw_text:
        return None, False

    parsed = None
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        # Salvage the first JSON object embedded in surrounding prose.
        import re
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = None

    if not isinstance(parsed, dict) or "grade" not in parsed:
        return None, False

    try:
        grade = int(parsed["grade"])
    except (ValueError, TypeError):
        return None, False

    if grade < 1 or grade > 4:
        return grade, False
    return grade, True


# ---------------------------------------------------------------------------
# Single-call timing
# ---------------------------------------------------------------------------

def run_single(client, system_prompt, user_message):
    """Execute one grading call, returning a dict with timing + parse results.

    tokens/sec is derived from wall-clock latency and an approximate output
    token count (whitespace-token heuristic) since LLMClient.chat() returns only
    the content string. This is a coarse but consistent cross-model proxy.
    """
    start = time.perf_counter()
    raw = client.chat(
        system_prompt,
        user_message,
        max_tokens=512,
        temperature=0.0,
        json_mode=True,
        timeout=120,
        retries=2,
    )
    latency = time.perf_counter() - start

    grade, json_ok = extract_grade(raw)
    approx_tokens = len((raw or "").split())
    tok_per_s = (approx_tokens / latency) if latency > 0 and approx_tokens else 0.0

    return {
        "latency_s": latency,
        "approx_output_tokens": approx_tokens,
        "tokens_per_s": tok_per_s,
        "grade": grade,
        "json_ok": json_ok,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Per-model evaluation
# ---------------------------------------------------------------------------

def evaluate_model(model, ollama_url, cases, runs, get_prompt_fn, llm_client_cls,
                   verbose=False):
    """Run every case (x `runs`) through one model and aggregate metrics."""
    client = llm_client_cls(base_url=ollama_url, model=model)

    exact_hits = 0
    within1_hits = 0
    json_ok_count = 0
    graded_total = 0          # calls that produced a usable grade
    scored_cases = 0          # case-runs counted toward accuracy
    latencies = []
    tok_rates = []
    per_case = []

    for case in cases:
        messages = build_grading_messages(get_prompt_fn, case)
        system_prompt, user_message = messages_to_system_user(messages)
        expected = int(case["expected_grade"])

        for run_idx in range(runs):
            result = run_single(client, system_prompt, user_message)
            latencies.append(result["latency_s"])
            if result["tokens_per_s"] > 0:
                tok_rates.append(result["tokens_per_s"])
            if result["json_ok"]:
                json_ok_count += 1

            graded = result["grade"]
            if graded is not None:
                graded_total += 1
            scored_cases += 1

            if graded is not None:
                if graded == expected:
                    exact_hits += 1
                if abs(graded - expected) <= 1:
                    within1_hits += 1

            if verbose:
                status = "ok" if result["json_ok"] else "PARSE-FAIL"
                print(f"  [{model}] {case.get('id', case['concept'])} "
                      f"run {run_idx + 1}/{runs}: expected={expected} "
                      f"got={graded} ({status}) {result['latency_s']:.2f}s")

            per_case.append({
                "case_id": case.get("id", case["concept"]),
                "expected_grade": expected,
                "predicted_grade": graded,
                "json_ok": result["json_ok"],
                "latency_s": round(result["latency_s"], 3),
                "tokens_per_s": round(result["tokens_per_s"], 1),
            })

    total = scored_cases or 1
    metrics = {
        "model": model,
        "n_cases": len(cases),
        "runs": runs,
        "total_calls": scored_cases,
        "exact_accuracy": exact_hits / total,
        "within1_accuracy": within1_hits / total,
        "json_parse_rate": json_ok_count / total,
        "graded_rate": graded_total / total,
        "mean_latency_s": statistics.mean(latencies) if latencies else 0.0,
        "p95_latency_s": (statistics.quantiles(latencies, n=20)[-1]
                          if len(latencies) >= 20 else
                          (max(latencies) if latencies else 0.0)),
        "mean_tokens_per_s": statistics.mean(tok_rates) if tok_rates else 0.0,
        "per_case": per_case,
    }
    return metrics


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_comparison_table(all_metrics):
    """Print an aligned comparison table across models."""
    header = (f"{'model':<22} {'exact-acc':>10} {'within1':>9} "
              f"{'json-rate':>10} {'mean-lat':>10} {'tok/s':>8}")
    print("\n" + header)
    print("-" * len(header))
    for m in all_metrics:
        print(f"{m['model']:<22} "
              f"{m['exact_accuracy'] * 100:>9.1f}% "
              f"{m['within1_accuracy'] * 100:>8.1f}% "
              f"{m['json_parse_rate'] * 100:>9.1f}% "
              f"{m['mean_latency_s']:>9.2f}s "
              f"{m['mean_tokens_per_s']:>8.1f}")
    print()


def write_results(path, args, all_metrics):
    """Persist full results (including per-case detail) to JSON."""
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ollama_url": args.ollama_url,
        "cases_file": args.cases,
        "runs": args.runs,
        "models": [m["model"] for m in all_metrics],
        "results": all_metrics,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote results to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="grading_eval.py",
        description="Compare LLM models on Helga's Socratic grading task "
                    "(grade accuracy, JSON reliability, latency, tok/s).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--models", required=True,
        help="Comma-separated Ollama model tags to compare, "
             "e.g. 'qwen3:14b,qwen3.5:9b,qwen3.5:35b-a3b'.",
    )
    parser.add_argument(
        "--ollama-url", default=DEFAULT_OLLAMA_URL,
        help="Base URL of the Ollama server (env OLLAMA_URL overrides default).",
    )
    parser.add_argument(
        "--cases", default=DEFAULT_CASES,
        help="Path to the curated grading-cases JSON file.",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="Repeat each case N times (for stable latency/throughput stats).",
    )
    parser.add_argument(
        "--out", default="grading_eval_results.json",
        help="Path to write the full JSON results file.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print per-case results as they run.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("error: --models did not contain any model tags", file=sys.stderr)
        return 2
    if args.runs < 1:
        print("error: --runs must be >= 1", file=sys.stderr)
        return 2

    # Lazy imports happen here — AFTER arg parsing — so --help never touches
    # the services tree or the network.
    try:
        get_prompt_fn = _load_grading_prompt_fn()
        llm_client_cls = _load_llm_client_cls()
    except ImportError as e:
        print(f"error: could not import Helga modules ({e}).\n"
              f"Run from the repo root so 'services.*' is importable.",
              file=sys.stderr)
        return 1

    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"error: failed to load cases from {args.cases}: {e}",
              file=sys.stderr)
        return 1

    print(f"Evaluating {len(models)} model(s) on {len(cases)} case(s) "
          f"x {args.runs} run(s) via {args.ollama_url}")

    all_metrics = []
    for model in models:
        print(f"\n=== {model} ===")
        try:
            metrics = evaluate_model(
                model, args.ollama_url, cases, args.runs,
                get_prompt_fn, llm_client_cls, verbose=args.verbose,
            )
        except Exception as e:  # noqa: BLE001 — surface any model failure, continue
            print(f"  error evaluating {model}: {e}", file=sys.stderr)
            continue
        all_metrics.append(metrics)

    if not all_metrics:
        print("error: no models produced results", file=sys.stderr)
        return 1

    print_comparison_table(all_metrics)
    write_results(args.out, args, all_metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
