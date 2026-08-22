# Model & latency testing log — 2026-08-21

Everything below was measured on the machine on this date. Where a number is an
estimate or an extrapolation it says so. Where a conclusion I stated earlier in
the day turned out to be wrong, the correction is recorded rather than the
mistake being quietly removed — three of them changed what the right answer is.

---

## 0. The headline

**Decode speed was never the bottleneck, and it should not drive model choice.**

Every model tested already exceeds what a human can consume. The real defect is
**~47 seconds of turn latency, ~80% of which is prompt prefill**, and prompt
caching fixes 27 of those seconds for free. Meanwhile the thing that actually
gates release — tutoring quality — was not measured at all until the comparison
that is running as this is written.

---

## 1. The hardware is not what the docs say

`CLAUDE.md` states "Mac Mini M4 Pro (24GB)". Verified via `system_profiler` and
`mlx-dspark doctor`:

```
Chip: Apple M4 (NOT M4 Pro) — 10 cores (4P + 6E), 24 GB, 120 GB/s
iogpu.wired_limit_mb = 20480
```

**120 GB/s, not the 273 GB/s of an M4 Pro.** I reasoned from the wrong figure
for most of the session. `CLAUDE.md` should be corrected.

### Why it matters

Decode on this machine is memory-bandwidth-bound. The ceiling is
`120 / weights_GB` tokens/sec:

| model | weights | ceiling | measured | % of ceiling |
|---|---|---|---|---|
| Qwen3.8-27B 3-bit dense | 11.8 GB | 10.2 t/s | **8.65** | **85%** |
| Muse-Glimmer-30B Q2_K_XL dense | 12.4 GB | 9.7 t/s | **5.4** | 56% |

**Correction #1.** Using 273 GB/s I concluded these models were *compute*-bound
and that smaller quants would not help. With the real figure, Qwen runs at 85%
of theoretical — it is bandwidth-bound, there is essentially no tuning headroom,
and reading fewer bytes per token is the only lever that works.

---

## 2. Memory: what actually fits

The measured thrash line sits between **11.9 GB and 13.7 GB**.

| model | size | outcome |
|---|---|---|
| dirk-27b Q4_K_S | 11.9 GB | swap stable ✓ |
| **nail-35b-a3b IQ3_S (production)** | **13.7 GB** | **112 MB free, 12 GB swap exhausted ✗** |
| GLM-4.7-Flash-REAP-23B-A3B Q4_K_M | 14.1 GB | thrashed ✗ |
| Muse MLX 3-bit + DSpark drafter | 23.7 GB | impossible |

**The production model has never fit.** `nail-35b-a3b-ctx` at 13.7 GB has been
running in swap, which means **every figure in `docs/RELEASE_CRITERIA.md` was
collected on a thrashing machine.** That costs time, not accuracy — but it
explains the slowness and it means the IQ3_S quant was chosen to fit a ceiling
it still exceeds.

An OOM guard (`/tmp/task0/oom_guard.sh`) watched the kernel's own pressure level
plus swap headroom for the whole session and **never fired**. Note its blind
spot, found the hard way: Metal can time out while the kernel still reports 88%
free, so a pressure-based guard does not catch GPU-side exhaustion.

---

## 3. Latency: the real defect

The UI streams text token-by-token (`chat_stream` → `_send_stream_token`) and
TTS is on-demand via a play button. So the bar is set by **reading speed**, not
by intuition:

| consumption mode | rate | tok/s required |
|---|---|---|
| adult silent reading | 250 wpm | **5.5** |
| teen reading | 180 wpm | 4.0 |
| TTS speech (Kokoro) | 150 wpm | 3.3 |
| K-5 child reading | 120 wpm | 2.7 |

**Every model tested clears this.** Muse 5.4, Qwen 8.65, nail ~20. Above the
bar, extra throughput is invisible — and for Mode B with TTS it buys literally
nothing, because audio cannot play faster than speech.

### Where the 47 seconds go

A Socratic turn makes **two sequential LLM calls** — grade the answer
(`fsm_logic.py` ~3071), then generate the reply (~563). Measured prefill on a
realistic prompt: **65 tok/s**.

| component | tokens | time |
|---|---|---|
| grade call | ~500 | ~10 s |
| **reply prefill** | ~2,450 | **~37.5 s** |
| **time to first token** | | **≈47 s** |

### Prompt caching: measured 31.6x

Controlled A/B, same model, same token counts, back to back
(`/tmp/task0/cache_test.py`):

| | first token | 40 tokens total |
|---|---|---|
| no cache | **27.66 s** | 32.28 s |
| with cache | **0.88 s** | 5.50 s |

The decode phase is **identical** in both arms (4.62 s for the same 39 tokens),
so the entire difference is prefill. One-off cache build costs 26.5 s at lesson
start and amortises over every turn after.

**Implementation notes.** Use the Python API — `make_prompt_cache` +
`stream_generate(prompt_cache=...)`, `copy.deepcopy` per turn. The CLI's
`--prompt-cache-file` re-applies the chat template so the cached prefix stops
matching as a literal token prefix and trimming leaves nothing. If saving to
disk the filename **must** end `.safetensors`.

**Prerequisite:** the prompt must be ordered so everything invariant (system,
concept doc, contract, exemplars) comes first and everything per-turn
(`TurnState`) comes last. Otherwise the cache misses every turn and buys zero.

### The other lever

**Move grading off the critical path** — the grade feeds the *next* turn's
state, not this turn's reply, so it can run after the reply streams. ~10 s per
turn, no quality cost. Precedent: `fsm_logic.py` ~2516 already carries a comment
about deleting an LLM classifier that cost "~15s per question cycle".

| | now | + caching | + grading async |
|---|---|---|---|
| time to first token | ≈47 s | ≈11 s | **≈1 s** |

---

## 4. Speculative decoding: not possible on this machine

Three attempts, three distinct failures.

| attempt | failure |
|---|---|
| `mlx-community/Qwen3.8-27B-MTP-8bit` via mlx-lm `--draft-model` | `Model type qwen3_5_mtp not supported` |
| same drafter via mlx-dspark | `not a DeepSpec-format DSpark drafter checkpoint` — it is an **mlx-vlm** adapter |
| `incoai/Qwen3.8-27B-DFlash2` (mlx-dspark's own preset) | **`[METAL] Command buffer execution failed: GPU Timeout Error`** |

The third is the real answer. Speculation needs the drafter resident *alongside*
the target — 11.79 + 3.85 = **15.64 GB** — and a 10-core base-M4 GPU times out
materialising it. Every published speedup for this model class (2.47x, 3.1x, 4x)
was measured on M4 Pro / M4 Max / M5 Max.

**It would not have mattered.** Speculation accelerates decode; decode is not
the bottleneck. Caching delivered 31.6x on the part that hurts, for no extra
memory.

---

## 5. Serving traps — three models nearly written off for server bugs

**Muse-Glimmer via Ollama returns empty.** Every reasoning-strength arm produced
exactly 3 tokens. Ollama's Harmony parsing yields empty `content` AND empty
`thinking`; with stop tokens removed the raw stream shows unparsed channel
structure (`to=self<|message|>…`). Served through **llama.cpp with `--jinja`**
the same weights produce good Socratic questions.

**Correction #2.** I initially reported Muse as disqualified. That was a serving
bug, not a model defect.

**Reasoning traps differ per model and neither default is safe:**

| model | fix | symptom without it |
|---|---|---|
| Muse-Glimmer | `Reasoning strength: low` in the system prompt | `high` burns all 700 tokens, emits no answer |
| Qwen3.8-27B | `chat_template_kwargs: {"enable_thinking": false}` | 120 tokens consumed, `content` empty |

`reasoning_effort: "none"` is an Ollama field and does **nothing** on mlx_lm.
`llm_client.chat()` already sends both fields; the bench harness's
`_chat_messages` was the one call site that did not.

**Correction #3 — the one that would have faked a result.**
`get_socratic_grading_prompt` returns a **system-only** messages array. Ollama
completes it; `mlx_lm.server` returns `404 {"error": "No user query found in
messages."}`. So every grading call on the Qwen arm failed silently, `TurnState`
recorded nothing, and **A.2 would have been inert for Qwen while active for
nail** — Qwen tutoring blind, and the gap reading as "nail is the better tutor".
Fixed in `_ensure_user_turn()`, which retypes the last system message as `user`
so both servers see byte-identical content.

---

## 6. Harness changes

| change | why |
|---|---|
| `--generate-only` + full-battery `--rescore` | tutor + judge need 26 GB together; the harness swapped a 13 GB model in and out **per dialogue**. Now one model resident, one load each. |
| `HELGA_JUDGE_URL` | a tutor on llama-server/mlx serves exactly one model, so a judge on the same URL silently *is* the tutor |
| calibration gate skipped in generate-only, and reads `HELGA_JUDGE_MODEL` | it tested `OLLAMA_MODEL` — in a two-phase run that is the **tutor**, so it would calibrate the model under test as its own judge and refuse to run |
| `_ensure_user_turn()` | §5, correction #3 |
| `chat_template_kwargs` in `_chat_messages` | §5 |
| `HELGA_SYSTEM_DIRECTIVE` | sweep reasoning strength from outside, so arms stay comparable without source edits |

`rescore()` previously re-judged only `visual_integration`. As the second half
of a two-phase run that would have left `socratic`, `adaptation`, `accuracy`,
`misconception_handling` and `honest_telling` permanently missing while
`summarise` reported the run as complete. Now runs the full battery.

75 harness tests pass.

---

## 7. Models measured

| model | serving | decode | weights | fits | notes |
|---|---|---|---|---|---|
| nail-35b-a3b-ctx IQ3_S | Ollama | ~20 t/s | 13.7 GB | ✗ swaps | MoE, 256 experts / 8 active |
| Qwen3.8-27B 3-bit | mlx_lm | 8.65 t/s | 11.8 GB | ✓ | dense; no vision (TextOnly) |
| Muse-Glimmer-30B Q2_K_XL | llama.cpp | 5.4 t/s | 12.4 GB | ✓ | dense, multimodal; needs `--jinja` |
| dirk-27b Q4_K_S | Ollama | not measured | 11.9 GB | ✓ | dense Qwen3.5-27B |

**Why nail is faster despite being larger:** it is MoE — 256 experts, 8 active,
~3B of 34.7B doing work per token. Against a dense 27–30B that is ~2x less
memory read and ~9x less compute.

**Correction #4.** Smaller quant is *not* always faster. Muse at 2-bit
(12.4 GB) is slower than Qwen at 3-bit (11.8 GB), because Q2_K dequantisation
costs more arithmetic per byte. Below ~4-bit the trade reverses.

---

## 8. What is still open

- **The tutoring comparison is running** — Qwen vs nail, mathematics, judge held
  fixed on nail, two-phase. No quality result yet.
- **`RELEASE_CRITERIA.md` is unchanged.** Tier 2 fails in all seven domains on
  `socratic`, `adaptation`, `honest_telling`. Nothing today moved a tutoring
  score; today was latency.
- **Caching is measured but not wired into the FSM.** Needs the prompt reordered
  so per-turn content comes last.
- **Untested candidate:** `GLM-4.7-Flash-REAP-23B-A3B` at **IQ4_XS (~11.9 GB)** —
  MoE like nail (fast), fits (unlike nail), 4-bit (better than nail's IQ3_S).
  Plausibly better than every model above on all three axes; a ~12 GB download
  rather than a runtime change.

### Caveats on whatever the comparison returns

One domain, one run, composite noise floor **0.53**. A gap smaller than that is
not a finding. And Muse's scores, if run, would measure *Muse-at-2-bit* — a loss
there is not evidence about Muse.

---

## 9. Environment left behind

- `/tmp/task0/mlxenv2` — Python 3.12 + **mlx-lm from git main** (PyPI's is too
  old: `Model type qwen3_5 not supported`) + mlx-dspark 0.15.1. Isolated,
  because the system `hf` CLI needs huggingface-hub 1.8 while mlx-lm needs <1.0.
- `/tmp/task0/oom_guard.sh` — kernel-pressure + swap guard.
- `/tmp/task0/cache_test.py` — the 31.6x A/B.
- Models under `/Volumes/My Passport/AI-Models/` (external, ~11 MB/s via
  Ollama; the `hf` CLI pulls the same drive at ~50 MB/s).
- Docker and its containers were stopped to free ~2 GB. Restart with
  `docker start helga-research helga-searxng`.
