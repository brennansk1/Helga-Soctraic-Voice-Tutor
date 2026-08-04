#!/usr/bin/env python3
"""llm_profile.py — what a course build and a tutoring turn actually cost.

WHY THIS EXISTS

Latency on this stack is not mysterious. Every second the user waits is one of
three things:

    calls × (prefill_tokens / prefill_rate  +  decode_tokens / decode_rate)
                                            +  model load, if it got unloaded

So the only numbers worth arguing about are: how many LLM calls, how many
tokens go IN, and how many come OUT. Those are all measurable without a GPU —
you intercept the HTTP layer, count, and multiply by rates measured once on the
real machine.

Guessing at this goes wrong in a specific way: the expensive call is rarely the
one you would name. Grading a one-sentence student answer looks cheap and was
the single most expensive prompt in the tutoring loop, because it pasted the
entire 10,000-character concept document in as "Source Truth Context" on every
turn.

USAGE
    python3 tools/llm_profile.py                # both profiles
    python3 tools/llm_profile.py --turn         # tutoring turn only
    python3 tools/llm_profile.py --build        # course build only
    python3 tools/llm_profile.py --json

RATES
    Defaults are for a Mac Mini M4 Pro running Qwen3.5-9B Q4 under Ollama.
    Override with --prefill / --decode / --load once you have measured your own
    (`ollama run <model> --verbose` prints both).
"""

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Rough but stable: ~3.6 characters per token for English prose with markdown.
CHARS_PER_TOKEN = 3.6

DEFAULT_PREFILL_TPS = 700.0    # prompt tokens/sec
DEFAULT_DECODE_TPS = 26.0      # generated tokens/sec
DEFAULT_LOAD_S = 9.0           # cold model load from disk


class Recorder:
    """Intercepts the Ollama HTTP call and records the shape of each request."""

    def __init__(self):
        self.calls = []

    def record(self, payload, stage=None):
        messages = payload.get("messages") or []
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        self.calls.append({
            "stage": stage or _caller_stage(),
            "prompt_chars": prompt_chars,
            "max_tokens": int(payload.get("max_tokens") or 0),
            "constrained": bool(payload.get("format")),
            "keep_alive": payload.get("keep_alive"),
            "system_head": str(messages[0].get("content", ""))[:70] if messages else "",
            # Filled in by _fake_post once the (canned) response exists. Decode
            # is the dominant cost and `max_tokens` is only a CEILING — the
            # structuring call budgets 7,680 tokens and generates around 900,
            # so estimating from the ceiling overstates a build by ~4x.
            "out_chars": None,
        })

    # ── reporting ────────────────────────────────────────────────────────────
    @staticmethod
    def _out_tokens(call, decode_ratio=0.35):
        """Generated tokens: measured when a response was seen, otherwise a
        fraction of the ceiling."""
        if call.get("out_chars") is not None:
            return call["out_chars"] / CHARS_PER_TOKEN
        return call["max_tokens"] * (0.2 if call["constrained"] else decode_ratio)

    def totals(self, decode_ratio=0.35):
        prompt_tokens = sum(c["prompt_chars"] for c in self.calls) / CHARS_PER_TOKEN
        decode_tokens = sum(self._out_tokens(c, decode_ratio) for c in self.calls)
        return {
            "calls": len(self.calls),
            "prompt_tokens": round(prompt_tokens),
            "decode_tokens": round(decode_tokens),
        }

    def seconds(self, prefill_tps, decode_tps, load_s, loads=0):
        t = self.totals()
        return (t["prompt_tokens"] / prefill_tps
                + t["decode_tokens"] / decode_tps
                + loads * load_s)


def _install_mocks():
    """Mock the container-only imports so this runs on a bare checkout."""
    for name in ("kuzu", "pyaudio", "aiortc", "webrtcvad", "smbus2",
                 "faster_whisper", "yaml", "docker", "docker.errors", "libzim",
                 "evdev", "av", "torch", "sentence_transformers", "audioop",
                 "psutil", "sklearn", "sklearn.feature_extraction",
                 "sklearn.feature_extraction.text", "sklearn.metrics",
                 "sklearn.metrics.pairwise"):
        sys.modules.setdefault(name, MagicMock())


class _Resp:
    status_code = 200

    def __init__(self, text):
        self._text = text

    def json(self):
        return {"choices": [{"message": {"content": self._text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

    def raise_for_status(self):
        pass


# Frames that identify which pipeline stage issued a call. Order matters: the
# first match walking OUT from the HTTP call wins, so the most specific
# function names come first.
_STAGES = [
    ("_enforce_depth_contract", "depth-contract retry"),
    ("_condense_and_structure_content", "structure concept"),
    ("_apply_fact_check", "fact check"),
    ("check_content", "fact check"),
    ("_plan_slots", "asset plan"),
    ("plan_slots", "asset plan"),
    ("_draw", "asset draw"),
    ("_judge_level", "level calibration"),
    ("_aids_for_concept", "asset plan"),
    ("audit", "syllabus audit"),
    ("_build_substructures_progressive", "skeleton"),
    ("generate_skeleton", "skeleton"),
]


def _caller_stage():
    """Which pipeline stage issued this call, read off the live stack.

    Tagging at the call site would mean threading a label through a dozen
    signatures; the stack already knows.
    """
    import inspect
    names = {f.function for f in inspect.stack()[1:24]}
    for fn, label in _STAGES:
        if fn in names:
            return label
    return "other"


def _fake_post(recorder, canned):
    """Stand in for every outbound POST, but only COUNT the ones going to the
    model.

    Patching `requests.post` catches the research service too, and counting
    those as LLM calls overstated a 6-concept build by six ~1,000-token
    generations that never happen — the research call returns JSON, not prose.
    """
    def post(url, json=None, timeout=None, **kw):     # noqa: A002
        payload = json or {}
        if "chat/completions" not in str(url):
            # Research / other services: answer plausibly, do not profile.
            return _Resp('{"combined_text": "Reference material.", '
                         '"sources": [], "confidence": 0.8}')
        recorder.record(payload)
        text = canned(payload)
        recorder.calls[-1]["out_chars"] = len(text)
        return _Resp(text)
    return post


# ── the tutoring turn ────────────────────────────────────────────────────────

def profile_turn(recorder):
    """One student answer: grade it, then respond to it.

    Driven through the real LLMClient against a patched HTTP layer, so what is
    measured is the payload Ollama would actually receive — including
    keep_alive and the reasoning flags. Building the prompts and measuring
    those instead misses everything the client adds.
    """
    import requests as _requests
    from services.common.prompts import (
        get_socratic_grading_prompt, get_typed_socratic_prompt,
    )
    from services.common.concept_doc import tutor_context
    from services.core.llm_client import LLMClient

    doc = _production_sized_doc()
    history = [("I'm ready.", "Let's begin."),
               ("Something about squares?", "Which squares, exactly?")]

    grading = get_socratic_grading_prompt(
        "The Pythagorean Theorem", "Which squares, exactly?",
        "The ones on the two short sides.",
        context_text=tutor_context(doc, "grading"), bloom_level=4,
        learning_objectives=["State the theorem"], mastery_criteria="…")
    answering = get_typed_socratic_prompt(
        "MECHANISM", tutor_context(doc, "socratic"), history,
        system_note="The student was partly right.", bloom_level=4)

    client = LLMClient()
    with patch.object(_requests, "post",
                      _fake_post(recorder, lambda p: "A short tutor reply?" * 6)):
        for messages, budget in ((grading, 500), (answering, 400)):
            sys_msg = messages[0]["content"]
            user_msg = "\n".join(str(m.get("content", "")) for m in messages[1:])
            client.chat(sys_msg, user_msg, max_tokens=budget, retries=1)


def _production_sized_doc():
    """A concept document the size hydration actually writes.

    The test fixture is deliberately small so its assertions stay readable;
    profiling against it understates every prompt. A mastery-4 contract allows
    500-1800 words, so the realistic case is several times the fixture.
    """
    from tests.common.test_concept_doc import DOC
    from services.common.concept_doc import split_sections
    preamble, sections = split_sections(DOC)
    grown = [preamble]
    for name, body in sections.items():
        # Pad the prose sections up to what the contract permits; leave the
        # short structural ones alone so the shape stays honest.
        if name in ("Core Explanation", "Key Facts", "Derivation",
                    "Real-World Examples", "Misconceptions"):
            body = body + "\n" + (body + "\n") * 3
        grown.append(f"## {name}\n{body}")
    return "\n\n".join(grown)


# ── the course build ─────────────────────────────────────────────────────────

def profile_build(recorder, concepts=12):
    """A build of `concepts` concepts, with research and Ollama stubbed."""
    import requests as _requests

    canned_md = (
        "## Mastery Criteria\nGrade 3 requires understanding.\n\n"
        "## Core Explanation\nFormally, we define the operator T as the unique "
        "map satisfying the stated axioms. " + "Explanation sentence. " * 120 + "\n\n"
        "## Key Facts\n- One.\n- Two.\n- Three.\n\n"
        "## Real-World Examples\nStep 1: a = 3. Step 2: b = 4. Step 3: c = 5.\n\n"
        "## Misconceptions\n- **Belief**: X.\n  **Correction**: Y.\n\n"
        "## Edge Cases & Limitations\n- Breaks down at the boundary.\n\n"
        "## Socratic Hooks\n- Bloom 1-2: Q?\n- Bloom 3-4: Q?\n- Bloom 5-6: Q?\n\n"
        "## Analogies\n- **Simple**: Like a box.\n- **Technical**: An inner product.\n\n"
        "## Governing Result\nThe spectral theorem states that every self-adjoint "
        "operator admits an orthonormal eigenbasis.\n\n"
        "## Derivation\nStep 1: assume the premise. Step 2: therefore the norm is "
        "preserved. Step 3: hence the result follows.\n\n"
        "## Exercise\nShow that the bound is tight.\n\n"
        "## Sources\n- [Rudin, Functional Analysis](https://example.org/rudin) "
        "— textbook (Tier 1)\n")

    def canned(payload):
        if payload.get("format"):
            return '{"ok": true, "items": []}'
        return canned_md

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATA_ROOT"] = tmp
        from services.common.storage import StorageManager
        from services.core.course_builder import ContentHydrator

        storage = StorageManager(tmp)
        course = {
            "title": "Euclidean Geometry", "modules": [{
                "uid": "mod_1", "title": "Triangles", "units": [{
                    "uid": "unit_1", "title": "Right Triangles", "lessons": [{
                        "uid": "less_1", "title": "Metric Relations",
                        "concepts": [
                            {"uid": f"con_{i:08x}", "title": f"Concept {i}",
                             "bloom_level": 4, "depth_level": 4,
                             "learning_objectives": ["Understand it"]}
                            for i in range(concepts)],
                    }],
                }],
            }],
        }
        uid = storage.courses.create_course(course)

        hydrator = ContentHydrator(storage=storage, course_depth=4, mastery=4)
        # Research off: this profiles LLM cost, and the research service is a
        # separate (parallelisable) I/O cost measured elsewhere.
        hydrator.confidence_floor = 0.0

        with patch.object(_requests, "post", _fake_post(recorder, canned)):
            try:
                hydrator.hydrate(uid)
            except Exception as e:                       # pragma: no cover
                print(f"  (build stopped early: {type(e).__name__}: {e})")
    return concepts


# ── report ───────────────────────────────────────────────────────────────────

def _report(name, rec, args, loads, workers=1):
    t = rec.totals()
    secs = rec.seconds(args.prefill, args.decode, args.load, loads)
    print(f"\n{name}")
    print(f"  LLM calls          {t['calls']}")
    print(f"  prompt tokens in   {t['prompt_tokens']:,}"
          f"   ({t['prompt_tokens']/args.prefill:.1f}s prefill)")
    print(f"  tokens generated   {t['decode_tokens']:,}"
          f"   ({t['decode_tokens']/args.decode:.1f}s decode)")
    if loads:
        print(f"  model loads        {loads}   ({loads*args.load:.1f}s)")
    print(f"  ESTIMATED          {secs:.1f}s   (serialised — one call at a time)")
    if workers and workers > 1:
        # Concurrent decodes on one Apple Silicon GPU do not scale linearly:
        # generation is memory-bandwidth bound, and batching amortises the
        # weight reads well but not perfectly. ~0.75 per extra stream is a
        # conservative reading; measure yours before quoting it.
        speedup = 1 + (workers - 1) * 0.75
        print(f"  at {workers} background slots: ~{secs/speedup:.0f}s "
              f"(assuming {speedup:.2f}x aggregate decode throughput)")
    by_stage = defaultdict(lambda: [0, 0, 0])
    for c in rec.calls:
        row = by_stage[c.get("stage", "other")]
        row[0] += 1
        row[1] += c["prompt_chars"] / CHARS_PER_TOKEN
        row[2] += Recorder._out_tokens(c)
    if len(by_stage) > 1:
        print("  ── by stage ──────────────  calls    in tok   out tok    est s")
        rows = sorted(by_stage.items(),
                      key=lambda kv: -(kv[1][1] / args.prefill + kv[1][2] / args.decode))
        for stage, (n, tin, tout) in rows:
            est = tin / args.prefill + tout / args.decode
            print(f"  {stage:<28}{n:>5}{tin:>10,.0f}{tout:>10,.0f}{est:>9.1f}")
    if rec.calls:
        worst = max(rec.calls, key=lambda c: c["prompt_chars"])
        print(f"  largest prompt     {worst['prompt_chars']:,} chars"
              f"  ({worst['prompt_chars']/CHARS_PER_TOKEN:.0f} tok)"
              f"  — {worst['system_head'].strip()[:50]}…")
    if not any(c["keep_alive"] for c in rec.calls):
        print("  keep_alive         NOT SET on any call — Ollama unloads the "
              "model after 5 idle minutes")
    return {"name": name, **t, "estimated_seconds": round(secs, 1)}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--turn", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--concepts", type=int, default=12)
    ap.add_argument("--prefill", type=float, default=DEFAULT_PREFILL_TPS)
    ap.add_argument("--decode", type=float, default=DEFAULT_DECODE_TPS)
    ap.add_argument("--load", type=float, default=DEFAULT_LOAD_S)
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()
    both = not (args.turn or args.build)

    _install_mocks()
    out = []

    if args.turn or both:
        rec = Recorder()
        profile_turn(rec)
        # A turn after any pause longer than Ollama's 5-minute idle timeout
        # pays a model load. Without keep_alive that is most turns in a real
        # session, where a student reads and thinks between answers.
        out.append(_report("TUTORING TURN (one student answer)", rec, args, loads=1))

    if args.build or both:
        rec = Recorder()
        n = profile_build(rec, args.concepts)
        try:
            from services.core.gpu_gate import get_gpu_gate
            workers = max(1, getattr(get_gpu_gate(), "bg_slots", 1))
        except Exception:
            workers = 1
        out.append(_report(f"COURSE BUILD (hydration, {n} concepts)",
                           rec, args, loads=0, workers=workers))

    if args.as_json:
        print(json.dumps(out, indent=2))
    print("\nRates are estimates — measure yours with `ollama run <model> "
          "--verbose` and pass --prefill/--decode/--load.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
