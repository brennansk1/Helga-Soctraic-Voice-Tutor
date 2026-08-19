# The project model — Nail-Qwen3.6-35B-A3B

Helga runs on **one** model for every role (build and tutor). This file is the
whole story: what it is, why, how to install it, and what it costs.

## What / why

| | |
|---|---|
| Model | `Nail-Qwen3.6-35B-A3B`, Unsloth Dynamic **IQ3_S** GGUF (13.7 GB) |
| Source | `peculiar-ragdoll/Nail-Qwen3.6-35B-A3B-GGUF` |
| Architecture | `qwen35moe` — 34.7 B total, **~3 B active per token** |
| Context | 262,144 (256 K) |
| Ollama tag | `nail-35b-a3b` |

Measured on the target box (Mac Mini M4 Pro, 24 GB), 2026-08-18:

| metric | Nail | old `qwen3.5:9b` |
|---|---|---|
| Decode, real builder prompt (warm, n=3) | **29.0 tok/s** | 17.2 tok/s |
| Per hydrated concept (real prompt, n=3) | **~119 s** | ~275 s |
| 8-gram repetition (degeneration) | **0.0** | 0.0 |
| JSON validity | 3/3 | 3/3 |
| Cold load from external USB | 133 s | 62 s |

The MoE architecture is why a 35 B model beats a 9 B on speed: only ~3 B
parameters fire per token. Same 256 K context as the 9B — **context is not the
reason for the switch, capability at equal-or-better speed is.**

## Install (one-time, and NOT `ollama pull`)

This is a local GGUF import. There is no registry tag, so `ollama pull
nail-35b-a3b` **will fail** — `deploy.sh` knows this and skips the pull when the
model is already present.

```bash
# 1. Fetch the GGUF (~13.7 GB) into the models folder
hf download peculiar-ragdoll/Nail-Qwen3.6-35B-A3B-GGUF \
    Nail-Qwen3.6-35B-A3B-UD-IQ3_S.gguf \
    --local-dir "/Volumes/My Passport/AI-Models/llm/Nail-Qwen3.6-35B-A3B-GGUF"

# 2. Register it with Ollama
printf 'FROM "%s"\n' \
  "/Volumes/My Passport/AI-Models/llm/Nail-Qwen3.6-35B-A3B-GGUF/Nail-Qwen3.6-35B-A3B-UD-IQ3_S.gguf" \
  > /tmp/Nail.Modelfile
ollama create nail-35b-a3b -f /tmp/Nail.Modelfile

# 3. Verify
ollama show nail-35b-a3b | grep -E "architecture|parameters|context"
```

Ollama imports `IQ3_S` **natively**, without requantizing. (It could not do this
for a sibling candidate's `IQ3_XXS`, which it silently requantized — see
`docs/MODEL_COMPARISON_DIRK_VS_9B.md`. If you ever swap quants, check the import
log for `llama-quantize`.)

## Host settings that matter

```bash
launchctl setenv OLLAMA_MAX_LOADED_MODELS 1   # deterministic eviction, not a memory race
launchctl setenv OLLAMA_KEEP_ALIVE -1         # stay resident; a reload costs ~133 s
launchctl setenv OLLAMA_NUM_PARALLEL 4        # must equal gpu_gate cap
```

**Memory reality:** 13 GB resident on a 24 GB box leaves ~5 GB. That is enough
for Nail *alone*. Kernel pressure measured **level 1 (normal)** with zero jetsam
kills in that state — but it is NOT enough for two models at once, which is why
one model serves both roles and `OLLAMA_MAX_LOADED_MODELS=1` is set. A
BUILD/TUTOR split would cost a 133 s swap on every alternation.

## Thinking models — do not remove this

Qwen3.x route their answer into a `reasoning` field and leave `content` **empty**
unless told otherwise, burning the whole token budget. Both LLM paths therefore
send the union of the disable-fields:

```python
"reasoning_effort": "none",                        # Ollama /v1
"chat_template_kwargs": {"enable_thinking": False} # mlx_lm /v1
```

Measured, same prompt: without → 0 content chars / 300 tokens burned; with →
598 chars of valid JSON in 108 tokens. Deleting either field silently empties
every structured generation in the product.

---

## REQUIRED: `num_ctx` — the model must be registered with a real context window

**Ollama serves a model at 4096 tokens unless its Modelfile says otherwise**, and
that is smaller than this project's prompts. `ollama show` reporting a 262144
context is describing the *architecture*, not what the server is doing.

Measured consequence of missing this: the one-shot subtree prompt is ~4212
tokens, so it returned

```
400 — request (4212 tokens) exceeds the available context size (4096 tokens)
```

for **5 of 6 modules in every build**. The builder treats that as an empty
result and falls back to the chunked path, so nothing surfaces as an error — the
course is simply a third shorter than its calendar. It also gets **worse as the
prompts improve**: adding real syllabus evidence to a module's scope pushes more
prompts over the line.

### Setup

```bash
printf 'FROM nail-35b-a3b\nPARAMETER num_ctx 16384\n' > Modelfile.ctx
ollama create nail-35b-a3b-ctx -f Modelfile.ctx     # reuses the blob, no re-download
```

`nail-35b-a3b-ctx` is now the default in `docker-compose.yml` and in the code
defaults, so nothing needs setting by hand. Override with `OLLAMA_MODEL` if you
register it under a different name.

### It is enforced, not just documented

`SkeletonBuilder._run_preflight_checks` probes the serving context and **refuses
to build below 8192 tokens**, naming the exact fix. An unreachable probe reports
UNKNOWN and proceeds — refusing to build because a probe went unanswered would be
the absent-vs-zero error in the place that blocks every course.

### Measured effect

| | 4096 (default) | 16384 |
|---|---|---|
| context 400s per build | 24 | **0** |
| one-shot fallbacks | 5 of 6 | **0 of 6** |
| coverage vs MIT 18.06 | 80% median | **100% median (3/3)** |
| concepts per course | 87–90 | **145** (target 144) |
