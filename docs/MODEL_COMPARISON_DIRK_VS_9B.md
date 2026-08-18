# Dirk-Qwen3.8-27B (3-bit) vs qwen3.5:9b — non-tutoring comparison

_Measured 2026-08-18, Mac Mini M4 Pro 24 GB. **Verdict: Dirk rejected.**_

## Provenance
Downloaded from `peculiar-ragdoll/Dirk-Qwen3.8-27B-GGUF` to
`/Volumes/My Passport/AI-Models/llm/Dirk-Qwen3.8-27B-GGUF/`:
- `Dirk-Qwen3.8-27B-UD-IQ3_XXS.gguf` — 11.9 GB (the only 3-bit quant in the repo)
- `mmproj-F16.gguf` — 0.93 GB vision projector

**Ollama could not serve IQ3_XXS natively** — `ollama create` ran
`llama-quantize --allow-requantize`, so the registered model is NOT byte-identical
to the download. (Nail's IQ3_S imported without requantization.) A two-`FROM`
Modelfile for the vision projector triggered the same path and was abandoned, so
Dirk was registered text-only and its vision capability is untested.

## Results (build-role aspects only; tutoring excluded by scope)

| aspect | qwen3.5:9b | dirk-27b | winner |
|---|---|---|---|
| Decode rate (warm, real builder prompt, n=3) | **17.2 tok/s** | 6.6–6.8 tok/s | 9B — **2.6× faster** |
| Cold load from USB | **62 s** | 118 s | 9B |
| JSON validity (tolerant parse) | 3/3 | 3/3 | tie |
| Degeneration (8-gram repetition) | 0.0 | 0.0 | tie — neither collapses |
| Empty-response rate | 0/3 | 0/3 | tie (after the `reasoning_effort` fix) |
| **Real hydration concept** | 451 / 254 / 121 s | **TIMED OUT** (>2×120 s) | 9B |
| Constrained JSON (`format` schema) | not confirmed | **ReadTimeout** | neither proven |

## Why Dirk is rejected

1. **Too slow to be usable.** 2.6× slower than the 9B baseline and ~4.4× slower
   than Nail. It never completed a single hydration concept.
2. **Dense, not MoE.** 27 B dense fires every parameter per token; Nail's 35 B
   MoE activates ~3 B. On this box that architectural difference dominates.
3. **Requantized in transit**, so any quality reading would describe Ollama's
   conversion rather than the published weights.

The one genuinely positive result: **no repetition degeneration** on the real
builder prompt (0.0), so it does not reproduce the ternary-27B collapse. That is
not enough to offset the speed.

## Note on an earlier misreading
An initial gate run showed all three models failing the depth contract 3/3 on
`primary_source`/`any_source`. That was **environmental** — the research service
was down, so no model could cite a source that was never fetched. Not a model
verdict. Service restored 2026-08-18.
