# Where the time goes, and what was taken off it

*Measured 2026-08-04 with `tools/llm_profile.py`, which intercepts the Ollama
HTTP layer and records the shape of every request. Rates below assume a Mac
Mini M4 Pro running Qwen3.5-9B Q4 — measure yours with
`ollama run <model> --verbose` and pass `--prefill/--decode/--load`.*

## The only equation that matters

```
wait = calls × (prefill_tokens / prefill_rate + decode_tokens / decode_rate)
       ÷ how many stream at once
       + a model load, if it got unloaded
```

Every finding below is one of those four terms. **Decode is 93% of a course
build**, so the levers that matter are: generate fewer tokens, generate them
concurrently, and don't pay for a cold model.

---

## Course build — 12 concepts, mastery 4

| | calls | generated tokens | serialised | at 3 slots |
|---|---|---|---|---|
| before | 70 | 35,526 | ~1,474s (25 min) | — |
| after | 58 | 23,763 | ~1,005s | **~402s (7 min)** |

Where the time went, before:

| stage | calls | est s | share |
|---|---|---|---|
| depth-contract retry | 24 | 939 | 64% |
| structure concept | 12 | 468 | 32% |
| asset plan | 24 | 47 | 3% |
| level calibration | 6 | 12 | 1% |
| fact check | 4 | 8 | 1% |

### 1. Hydration ran one concept at a time against a four-way server

`ContentHydrator` sizes its thread pool from the GPU gate's background
capacity, and that defaulted to **1**. The `ThreadPoolExecutor` was decorative:
`max_workers = min(bg_slots, len(concepts))` = 1.

The gate's own design already says what the safe value is. `_can_dispatch_now`
refuses background work beyond `bg_slots`, and the constructor clamps to
`cap - 1` — so with `OLLAMA_NUM_PARALLEL=4`, three slots for background leaves
**one permanently reserved for a student**. An arriving interactive turn finds
it free and is dispatched immediately; it never queues behind a 30-second
generation. `_dispatch_next` also drains interactive waiters before background
ones. The conservative default was protecting an invariant the structure
already enforced.

**Fix.** `bg_slots` defaults to `cap - 1`. `HELGA_BG_SLOTS=1` restores the old
behaviour. Concurrent decode on one Apple Silicon GPU does not scale linearly —
generation is memory-bandwidth bound — so the profiler quotes a conservative
0.75 per extra stream (2.5x at three slots), not 3x.

*Consequence handled:* at one worker, writing `source_confidence` into the live
`course` dict was safe. At three it races a concurrent `update_course`, which
deepcopies and `json.dump`s that same dict — adding a key can resize it
mid-walk. That write now takes `_course_lock`.

### 2. The retry loop re-sent a byte-identical prompt

`_enforce_depth_contract` regenerates against a hint derived from the problem
set. When a retry reproduced *the same* problem set, the hint was unchanged, so
the next attempt sent an identical prompt at the same temperature and
regenerated the whole ~900-token document again.

The problems that repeat are the ones the model structurally cannot fix from
where it is standing — "cite a primary source" when the research pass returned
none — not ones it randomly missed. The codebase already agreed with this
reasoning one test away: *"It should stop rather than burn every retry on a
known-bad generator."*

**Fix.** Stop when a retry reproduces the same problem set. A retry that makes
partial progress still continues — that case has its own test. **12 calls and
469s off a 12-concept build**, and it scales with how often the contract is
missed (a real run recorded 12/12 missing).

### 3. Nothing kept the model in memory

No request carried `keep_alive` and nothing checked whether the host had set
it. `OLLAMA_KEEP_ALIVE=-1` was a *comment* in `.env.example` — advice, with no
verification that anyone had followed it.

`health_check()` looked like it covered this and does not: it reads
`/api/tags`, which lists models that are **installed**. Whether the weights are
resident is a different question, and `/api/ps` is the endpoint that answers it.

**Fix.** Three layers, because the reliable one is outside this repo:
* every request now carries `keep_alive` (the `/v1` shim may ignore it
  depending on Ollama version; it ignores unknown fields harmlessly either way);
* `LLMClient.residency()` reads `/api/ps` and reports `expires_at`;
* `warn_if_not_pinned()` runs at FSM startup and logs the exact `launchctl`
  command. A malformed expiry reports *unknown*, not *not pinned* — a warning
  that is sometimes wrong is a warning people learn to ignore.

---

## Tutoring turn — one student answer

Two serialised calls: grade the answer, then respond to it. The response
depends on the grade (it selects LECTURE vs QUESTION), so they cannot overlap.

### 4. Grading shipped the entire concept document

`get_socratic_grading_prompt` embeds `context_text` verbatim as "Source Truth
Context", and the FSM passed `self.current_context` — the whole document, up to
the 10,000-character slice taken when the concept loaded. That is **~2,780
prefill tokens on every student answer** to produce a ~90-token JSON verdict.

Socratic hooks, analogies and misconceptions cannot change a grade. They were
being re-read by the model every turn for nothing.

**Fix.** A `grading` mode in `concept_doc`: mastery criteria (the standard),
key facts and the core explanation (to check claims against), and the worked
example — withheld from the questioner as a spoiler, but here it *is* the
rubric. Measured on a 4,281-char document the prompt drops 1,763 → 996 tokens;
on a full-size mastery-4 document the saving is ~2,000 tokens, about 3s a turn.

### 5. What was left alone, and why

The Socratic system prompt is ~7,800 characters of pedagogy rules — the largest
single item in a turn at ~2,170 tokens. It is doing real work (the
anti-false-praise rules, one-question-per-turn, the markdown ban) and it is
**byte-identical every turn and sits at the very start of the message**, which
is exactly the shape Ollama's prefix cache handles. Cutting it would trade
measurable quality for prefill that is probably already free. Left as is.

The asset-plan stage is prefill-heavy (~1,200 tokens in for ~7 out) but is 3%
of a build. Not worth the risk.

---

## Still open

### A. `num_ctx` may be truncating mastery 4–5 output *(hypothesis, untested here)*

Nothing in the stack sets `num_ctx`, and many Modelfiles default it to 4096
regardless of what the model supports. A mastery-5 concept is contracted at up
to 2,200 words (~3,000 tokens) on top of a ~900-token prompt. That does not
fit.

If it is truncating, the failure is self-concealing and expensive: the
generation is cut off, the depth contract reads it as "too short", and a full
regeneration is triggered that cannot succeed either — which matches the
observed "12/12 concepts missed the contract, too short" runs exactly.

Check with `ollama show <model>` before blaming the model for short output;
`OLLAMA_CONTEXT_LENGTH=8192` on the host is the fix. This is stated as a
hypothesis because verifying it needs a live Ollama, which this environment
does not have.

### B. The retry regenerates the whole document to fix one section

"Too short by 60 words" and "missing a primary source" are *additive*
deficiencies. Rewriting nine sections to add one is the expensive way to fix
them, and it puts the sections that already passed at risk of getting worse. A
patch-one-section path would cut retry cost far below what the early abort
saves, but it is a redesign of the enforcement loop rather than a tuning
change.

### C. Grade and respond cannot overlap, but could merge

One call that returns both a verdict and a reply would halve per-turn latency.
It costs streaming — the student currently watches the reply arrive token by
token, which is worth more perceived latency than it looks — and it puts a
prose field inside grammar-constrained JSON. Worth prototyping behind a flag,
not worth doing blind.

---

## Verification

`tests/core/test_llm_throughput.py` (16) covers the gate default, the
never-starve-a-student property the raised default depends on, `keep_alive` on
both client paths, and the residency probe.
`tests/core/test_depth_enforcement.py` gained two: the identical-failure abort,
and that a retry making partial progress is still allowed to continue.
`tests/common/test_concept_doc.py` gained six for the grading mode.

Reproduce the numbers with `python3 tools/llm_profile.py`.
