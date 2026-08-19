# Memory budget — measured, 2026-08-19

Machine: **Mac mini, Apple M4 (4P + 6E), 24 GB unified.**

> Not an M4 Pro. `CLAUDE.md`, `RESEARCH_BRIEF_CONTENT_HYDRATION.md` and the
> hydration research all say M4 Pro. The M4 has roughly **half** the memory
> bandwidth (~120 vs ~273 GB/s), and decode is bandwidth-bound, so every
> throughput estimate derived from an M4 Pro figure is about 2× optimistic.
> **Measured at IQ3_S: 30.1 tok/s decode, 247 tok/s prefill.**

Everything below is measured on this machine unless marked *(est)*.

---

## 1. The floor — what is used before the model loads

Measured with the model unloaded and Helga's research + searxng up:

| component | GB | note |
|---|---|---|
| kernel wired | 1.68 | irreducible |
| active (apps) | 1.97 | includes the Claude desktop app; unattended runs are lower |
| compressor | 2.04 | compressed inactive pages |
| **floor total** | **5.69** | 82% system-wide free at this point |

Of what is running, **0.59 GB is unrelated to Helga** and reclaimable:
`openhands-daemon` (544 MB) and `bodybuilding-postgres-1` (47 MB).

---

## 2. Docker

| component | GB | note |
|---|---|---|
| `Virtualization.VirtualMachine` (hypervisor) | 0.68 | measured |
| `com.docker.backend` (×3) | 0.20 | measured |
| **Docker host-side total** | **0.88** | present whenever Docker runs |

### Container limits vs actual

`mem_limit` is a **cap, not consumption** — measured usage runs well under it.

| service | limit | measured | needed for |
|---|---|---|---|
| `research` | 384 M | **139 M** | build |
| `searxng` | 256 M | **168 M** | build |
| `core-logic` | 512 M | *(est 250 M)* | build + tutoring |
| `rag-engine` | 768 M | *(est 400 M)* | build + tutoring |
| `web-ui` | 256 M | *(est 120 M)* | tutoring (status during build) |
| `tts` (Kokoro) | 2048 M | *(est 900 M)* | **tutoring only** |
| **all six** | **4.13 GB** | *(est ~2.0 GB)* | |
| **build subset** (no tts) | **2.13 GB** | *(est ~1.1 GB)* | |

STT (Nemotron-3.5-ASR, MLX) runs **host-native**, not in a container, and is
tutoring-only. Not currently running; not measured.

---

## 3. Ollama — the dominant term

Weights file: **12.74 GB** at IQ3_S (3.44 bpw). Runtime overhead + KV at the
current 16k context: **0.44 GB**.

### Context is cheap — measured

Predicted from the model's own config — `full_attention_interval 4` over 40
blocks means only 10 layers hold KV, at 2 KV heads × (256+256) = **20 KB/token
FP16** — then verified by loading at each size:

| num_ctx | total resident | Δ vs 16k predicted | Δ measured |
|---|---|---|---|
| 16,384 *(today)* | **13.18 GB** | — | — |
| 32,768 | **13.51 GB** | +0.31 | **+0.33** |
| 65,536 | **14.17 GB** | +0.94 | **+0.99** |
| 131,072 | **15.09 GB** | +2.19 | **+1.91** |

**128k of context costs under 2 GB.** Context is not the constraint; weights are.

### Quantisation options for nail-35b-a3b

Weights scaled from the measured 12.74 GB / 3.44 bpw, plus measured runtime+KV.

| quant | bpw | weights | @16k | @32k | @64k |
|---|---|---|---|---|---|
| **IQ3_S** *(current)* | 3.44 | 12.74 | **13.18** | 13.51 | 14.17 |
| IQ3_M | 3.66 | 13.55 | 13.99 | 14.32 | 14.98 |
| **IQ4_XS** | 4.25 | 15.74 | **16.18** | **16.51** | 17.17 |
| IQ4_NL | 4.50 | 16.67 | 17.11 | 17.44 | 18.10 |
| Q4_K_S | 4.58 | 16.96 | 17.40 | 17.73 | 18.39 |
| Q4_K_M | 4.83 | 17.89 | 18.33 | 18.66 | 19.32 |

---

## 4. The two budgets

The workloads are different and must be budgeted separately. **Course building
and tutoring never need to run at once**, which is what makes the ceiling
workable.

### A. Course building (the constrained case)

| line | GB |
|---|---|
| physical | 24.00 |
| − kernel wired | −1.68 |
| − apps, unattended | −1.20 |
| − compressor | −2.04 |
| − Docker host-side | −0.88 |
| − containers: research + searxng + core + rag *(est)* | −1.10 |
| − build process (python) *(est)* | −0.50 |
| − **OOM guard** | **−2.00** |
| **available for Ollama** | **≈ 14.60** |

Freeing the two unrelated containers returns **+0.59** → **≈ 15.2 GB**.

### B. Tutoring (the roomy case)

No build process, no research/searxng sweep, but TTS and STT are live:

| line | GB |
|---|---|
| physical | 24.00 |
| − floor (wired + apps + compressor) | −4.92 |
| − Docker host-side | −0.88 |
| − containers: web-ui + core + rag + tts *(est)* | −1.67 |
| − STT host-native *(est, not measured)* | −0.80 |
| − **OOM guard** | **−2.00** |
| **available for Ollama** | **≈ 13.73** |

---

## 5. What fits

Against the **building** budget of ~15.2 GB (unrelated containers freed):

| configuration | resident | verdict |
|---|---|---|
| IQ3_S @ 16k *(today)* | 13.18 | fits, 2.0 GB spare |
| IQ3_S @ 64k | 14.17 | fits, 1.0 GB spare |
| IQ3_S @ 128k | 15.09 | marginal, 0.1 GB — no |
| **IQ4_XS @ 16k** | **16.18** | **over by 1.0** |
| **IQ4_XS @ 32k** | **16.51** | **over by 1.3** |
| Q4_K_M @ anything | 18.33+ | far over |

**IQ4_XS does not fit the building budget with a 2 GB guard.** It fits only if
the guard is cut to ~1 GB, or the OS floor is reduced further (no desktop apps,
Docker stopped between build phases).

Against the **tutoring** budget of ~13.7 GB, IQ4_XS does not fit either — TTS
and STT are expensive and the model would have to shrink, not grow.

### The trade-off, stated plainly

**Precision and context compete for the same ~3 GB, and only one is affordable.**

* Going IQ3_S → IQ4_XS costs **+3.0 GB** of weights.
* Going 16k → 64k costs **+1.0 GB** of KV.

There is not room for both, and on the building budget there is not comfortably
room for the precision bump alone.

---

## 6. Warning signs already present

* **Swap: 6.7 GB used of 8 GB** while running only IQ3_S. The machine is already
  paging under today's configuration, so the static arithmetic above is
  optimistic — real headroom is tighter than the tables suggest.
* Free pages fell to **0.06 GB** with the model loaded and a build running.
* `memory_pressure` reported **28% free** under load vs **82% free** with the
  model unloaded — the model is essentially the entire budget.

**Before any quant change, measure peak RSS across a full build**, rather than
trusting the static budget. A ceiling discovered during an overnight run is the
expensive way to find it.

---

## 7. Recommendation

1. **Do not pull IQ4_XS yet.** It exceeds the building budget by ~1 GB with a
   2 GB guard, and the machine is already swapping.
2. **Take the free win instead: raise `num_ctx` to 32k or 64k.** Measured cost
   is 0.33–0.99 GB, it fits today, and it is what lesson-batched hydration
   (Stage 1) actually needs. Module-batching at 128k is not affordable.
3. **Reclaim 0.59 GB** by stopping `openhands-daemon` and
   `bodybuilding-postgres-1` during builds.
4. **Instrument a full build for peak RSS** before revisiting the quant
   question. If real peak leaves more room than the conservative estimates
   here — particularly if the container estimates marked *(est)* prove high —
   IQ4_XS at 16k comes back into range.
5. If the false-claim rate turns out to demand IQ4_XS, the honest options are:
   stop Docker between build phases, drop the desktop apps during unattended
   runs, or accept a ~1 GB guard with peak monitoring — **not** to assume the
   headroom is there.
