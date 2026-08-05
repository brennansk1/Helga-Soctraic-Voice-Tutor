#!/usr/bin/env python3
"""model_gate.py — can this model actually do the builder's job?

WHY THIS EXISTS

This project has already been burned once by evaluating a candidate on a
simplified prompt. From docs/MODE_A_STATUS.md §5:

    The ternary 27B is not viable for generation: it degenerates into
    repetition on the real builder prompt (3/3), while producing clean output
    on a simplified version (4/4).

Four clean runs on a toy prompt and three collapses on the real one. That is
not a marginal difference in score — it is the opposite conclusion, from the
same model, on the same machine, the same day. So this gate runs the REAL
`_condense_and_structure_content` prompt, at the real mastery level, and judges
the output with the REAL `validate_concept` — the same contract the pipeline
will hold it to at 3am with nobody watching.

WHAT IT MEASURES

  contract    % of concepts meeting the depth contract FIRST TIME. This is the
              headline number, and it is a LATENCY number as much as a quality
              one: on a 12-concept mastery-4 build, 12 of 58 calls (~469s) were
              depth-contract retries. A model that meets the contract first
              time can be faster in wall clock than a quicker one that does not.

  collapse    repetition degeneration — the exact failure the ternary 27B hit.
              Measured as the share of output made of repeated sentences, which
              catches the "same paragraph forever" mode that word-count and
              section checks both sail straight past.

  schema      whether grammar-constrained JSON works on this server. Skeletons,
              aid plans and grading all pass a JSON schema via `format`, and it
              is load-bearing — it is why malformed JSON stopped being the
              normal failure. A candidate whose server ignores it makes course
              creation WORSE no matter how good the prose is.

  tok/s       decode rate, so the contract win can be traded against it.

USAGE
    # against Ollama (default port), the model already pulled
    python3 tools/model_gate.py --model qwen3.6:27b

    # against an MLX model served by mlx_lm.server on another port
    python3 tools/model_gate.py --model mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit \\
                                --url http://127.0.0.1:8080

    # compare candidates
    python3 tools/model_gate.py --model a --model b --concepts 6

Needs a running model server. It is meant to be run on the Mac, not in CI.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Real concepts, deliberately spanning the domains the contract treats
# differently: a formal one that must carry a derivation, an empirical one, and
# a humanities one where demanding notation would be wrong.
PROBES = [
    ("The Pythagorean Theorem", "Euclidean Geometry", "formal"),
    ("Eigenvalues and Eigenvectors", "Linear Algebra", "formal"),
    ("Natural Selection", "Evolutionary Biology", "empirical"),
    ("Opportunity Cost", "Microeconomics", "empirical"),
    ("The Columbian Exchange", "World History", "humanities"),
    ("Hypothesis Testing", "Statistics", "formal"),
]

AID_SCHEMA = {
    "type": "object",
    "properties": {
        "aids": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["kind", "title"],
            },
        }
    },
    "required": ["aids"],
}


def repetition_score(text):
    """Fraction of the output that is repeated sentences.

    Degeneration does not shorten a document or remove its headings, so word
    counts and section checks both pass while the content is a loop. Counting
    duplicate sentences is what actually sees it.
    """
    sentences = [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", text)
                 if len(s.strip()) > 25]
    if len(sentences) < 6:
        return 0.0
    counts = Counter(sentences)
    repeated = sum(n - 1 for n in counts.values() if n > 1)
    return repeated / len(sentences)


def _install_mocks():
    from unittest.mock import MagicMock
    for name in ("kuzu", "psutil", "yaml", "sentence_transformers", "torch",
                 "sklearn", "sklearn.feature_extraction",
                 "sklearn.feature_extraction.text", "sklearn.metrics",
                 "sklearn.metrics.pairwise", "libzim", "docker",
                 "docker.errors"):
        sys.modules.setdefault(name, MagicMock())


# Representative research sources, so the citation elements of the depth
# contract are SATISFIABLE. Production gets these from phase-2 research before
# hydration; the gate used to pass none, which made two required elements
# impossible to meet without fabricating a URL.
#
# Field names match what the prompt renders: title + url (course_builder.py
# ~2503). One primary source (doi.org) and one general source, which is the
# minimum that lets `primary_source` and `any_source` both be met honestly.
GATE_SOURCES = [
    {"title": "Encyclopaedia Britannica entry",
     "url": "https://www.britannica.com/science/Pythagorean-theorem",
     "type": "reference"},
    {"title": "A peer-reviewed treatment",
     "url": "https://doi.org/10.1080/00029890.2019.1565821",
     "type": "primary"},
    {"title": "Open textbook chapter",
     "url": "https://en.wikibooks.org/wiki/Geometry",
     "type": "textbook"},
]


def _sources_block(sources, confidence=0.85):
    """The `## Sources` section exactly as `hydrate()` appends it.

    Mirrors course_builder.py ~2466 so the gate validates the same document
    shape production stores — same bullet format, same trailing confidence
    line. If that format changes there and not here, the gate silently starts
    measuring something else, so this is deliberately a copy of a small thing
    rather than a clever import of a large one.
    """
    if not sources:
        return ""
    out = "\n\n## Sources\n"
    for src in sources:
        tier = src.get("domain_tier", "")
        out += (f"- [{src.get('title', 'Untitled')}]({src.get('url', '')}) — "
                f"{src.get('type', 'web')}"
                + (f" (Tier {tier})" if tier else "") + "\n")
    return out + f"\n*Source confidence: {confidence:.2f}*\n"


def probe_schema(url, model, timeout=300):
    """Does this server honour grammar-constrained JSON?

    Asked as a question the model would plausibly get WRONG unconstrained —
    a nested object with a required field — so a pass means the grammar is
    doing the work rather than the model happening to comply.
    """
    import requests
    endpoint = url.rstrip("/") + "/v1/chat/completions"

    # WARM THE MODEL FIRST, AND DO NOT COUNT THE LOAD AGAINST THE PROBE.
    #
    # probe_schema is the first thing in the run that touches the model, so it
    # pays the cold load — and on this machine the weights live on a USB drive
    # where a 13.5 GB load measured ~4 minutes. Against a 120s timeout that is
    # a guaranteed ReadTimeout, reported as "constrained JSON: NO".
    #
    # Three candidates were marked json=N that way (gemma-3-12b,
    # Mistral-Small-24B, GLM). The gate's own rule says a N "disqualifies the
    # build role regardless of the other columns", so a slow disk was
    # eliminating models on a capability they were never asked to demonstrate.
    try:
        requests.post(endpoint, timeout=900, json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        })
    except Exception as e:
        return False, f"model would not load: {type(e).__name__}"

    try:
        resp = requests.post(endpoint, timeout=timeout, json={
            "model": model,
            "messages": [
                {"role": "system", "content": "You return only JSON."},
                {"role": "user", "content":
                 "Propose two teaching diagrams for the Pythagorean theorem."},
            ],
            # 400 was too low and it read as a MODEL failure. Measured against
            # qwen3:14b, the constrained response came back as valid-but-
            # truncated JSON ("Unterminated string") — the grammar was working
            # and the budget was not. A probe that reports "this model cannot
            # do constrained JSON" when it actually can is worse than no probe,
            # because it eliminates good candidates.
            "max_tokens": 1200,
            # THINKING OFF. Without this a reasoning model spends the whole
            # budget on its thinking block and returns empty content — measured
            # here as an empty string on two of three request shapes, reported
            # as "returned text that is not JSON". llm_utils has disabled this
            # since the A1/A6 fix; the probe never did, so it was not measuring
            # what production does.
            "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False},
            "format": AID_SCHEMA,          # Ollama
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "aids",
                                                "schema": AID_SCHEMA}},  # OpenAI-style
        })
        body = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(body)
        return isinstance(parsed.get("aids"), list), ""
    except json.JSONDecodeError:
        return False, "returned text that is not JSON"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"


def run_model(model, url, mastery, concepts):
    from services.core.course_builder import ContentHydrator
    from services.core.depth_contract import validate_concept

    os.environ["HELGA_BUILD_MODEL"] = model
    os.environ["HELGA_BUILD_URL"] = url
    os.environ.pop("LLM_API_URL", None)      # would override the role's URL

    hydrator = ContentHydrator.__new__(ContentHydrator)
    hydrator.status_callback = None
    hydrator.mastery_level = mastery
    hydrator.course_depth = mastery
    hydrator.enforce_depth = False           # measure FIRST-TRY quality
    hydrator.max_depth_retries = 0
    hydrator.topic_domain = None
    hydrator._contract_failures = []
    hydrator.source_document = ""

    rows, total_chars, total_secs = [], 0, 0.0
    for title, course, domain in PROBES[:concepts]:
        hydrator.topic_domain = domain
        started = time.monotonic()
        try:
            md = hydrator._condense_and_structure_content(
                title, "", course, mastery, "core theory", "llm-only",
                hierarchy_context={"module": course, "unit": title,
                                   "lesson": title},
                bloom_level=min(mastery, 5),
                learning_objectives=[f"Understand {title}"],
                prerequisite_titles=[],
                # WITHOUT THIS THE CONTRACT IS UNSATISFIABLE.
                #
                # Two required elements are citation detectors that scan the
                # generated body: `any_source` is literally `https?://\S+` and
                # `primary_source` matches arxiv/doi/pubmed/JSTOR. The gate
                # used to pass no sources at all, so the only way to satisfy
                # them was to INVENT a URL — and every model that behaved
                # correctly by not fabricating one scored zero on both.
                #
                # That is why four consecutive candidates scored exactly 0/6
                # while otherwise looking very different: qwen3:14b produced
                # ~1,100 clean words per concept with repetition 0.00 and
                # still "failed" every one, on nothing but the two source
                # elements.
                #
                # Production always has these — phase-2 research runs before
                # hydration. Supplying a representative set measures what the
                # model does WITH sources, which is the actual job.
                research_sources=GATE_SOURCES)
        except Exception as e:
            rows.append({"title": title, "error": f"{type(e).__name__}: {e}"})
            continue
        elapsed = time.monotonic() - started
        total_secs += elapsed
        total_chars += len(md)

        # VALIDATE THE DOCUMENT PRODUCTION ACTUALLY STORES.
        #
        # `_condense_and_structure_content` returns the model's prose; the
        # `## Sources` block is appended afterwards by `hydrate()`
        # (course_builder.py ~2466). The gate validated the raw return value,
        # so the two citation elements — `any_source` (literally
        # `https?://\S+`) and `primary_source` (doi/arxiv/pubmed) — could
        # never match, and EVERY model scored 0 on both regardless of quality.
        #
        # That is what four consecutive 0/6 results were measuring. qwen3:14b
        # wrote ~1,200 clean words per concept with repetition 0.00 and failed
        # all six on nothing else.
        #
        # A real concept file on disk carries these URLs, so appending the same
        # block here is restoring what production does, not padding the score.
        md_validated = md + _sources_block(GATE_SOURCES)
        ok, problems, _detail = validate_concept(
            md_validated, mastery, course, domain)
        rows.append({
            "title": title,
            "ok": ok,
            "problems": problems[:3],
            "words": len(md.split()),
            "repetition": round(repetition_score(md), 3),
            "seconds": round(elapsed, 1),
        })
        mark = "PASS" if ok else "fail"
        flag = "  <-- REPETITION" if repetition_score(md) > 0.15 else ""
        print(f"    [{mark}] {title:<32} {len(md.split()):>4}w "
              f"{elapsed:>5.1f}s{flag}")
        if not ok:
            print(f"           {'; '.join(problems[:2])}")

    graded = [r for r in rows if "ok" in r]
    met = sum(1 for r in graded if r["ok"])
    tps = (total_chars / 3.6 / total_secs) if total_secs else 0.0
    return {
        "model": model, "url": url,
        "contract_met": met, "graded": len(graded),
        "errors": sum(1 for r in rows if "error" in r),
        "worst_repetition": max((r["repetition"] for r in graded), default=0.0),
        "tokens_per_sec": round(tps, 1),
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", required=True,
                    help="repeatable; compare several candidates")
    ap.add_argument("--url", default=os.getenv("OLLAMA_URL",
                                               "http://127.0.0.1:11434"))
    ap.add_argument("--mastery", type=int, default=4)
    ap.add_argument("--concepts", type=int, default=len(PROBES))
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()

    _install_mocks()

    # A dead server otherwise reports as 0% contract compliance, which reads as
    # a damning verdict on the model rather than "nothing was asked".
    import requests
    try:
        requests.get(args.url.rstrip("/") + "/api/tags", timeout=5)
    except Exception:
        try:
            requests.get(args.url.rstrip("/") + "/v1/models", timeout=5)
        except Exception as e:
            print(f"No model server at {args.url} ({type(e).__name__}).\n"
                  f"Start one first:\n"
                  f"  ollama serve                       # then --model qwen3.6:27b\n"
                  f"  mlx_lm.server --model <repo> --port 8080   # then --url "
                  f"http://127.0.0.1:8080", file=sys.stderr)
            return 2

    results = []
    for model in args.model:
        print(f"\n=== {model} @ {args.url}")
        schema_ok, schema_note = probe_schema(args.url, model)
        print(f"    constrained JSON: "
              f"{'yes' if schema_ok else 'NO — ' + schema_note}")
        result = run_model(model, args.url, args.mastery, args.concepts)
        result["schema"] = schema_ok
        results.append(result)

    print("\n" + "=" * 74)
    print(f"{'model':<44}{'contract':>10}{'repeat':>8}{'tok/s':>7}{'json':>5}")
    for r in results:
        pct = (100 * r["contract_met"] / r["graded"]) if r["graded"] else 0
        print(f"{r['model'][:43]:<44}"
              f"{r['contract_met']}/{r['graded']} ({pct:>3.0f}%)".ljust(54)
              + f"{r['worst_repetition']:>8.2f}{r['tokens_per_sec']:>7.1f}"
                f"{'y' if r['schema'] else 'N':>5}")
        if r["errors"]:
            print(f"{'':<44}{r['errors']} call(s) failed outright")

    print("\nHow to read this:")
    print("  contract  first-try pass rate. Also a latency number — a retry")
    print("            regenerates the whole document (~900 tokens).")
    print("  repeat    >0.15 is the degeneration mode that killed the ternary")
    print("            27B. Headings and word count both pass while it happens.")
    print("  json      constrained decoding. A 'N' disqualifies the build role")
    print("            regardless of the other columns.")

    if args.as_json:
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
