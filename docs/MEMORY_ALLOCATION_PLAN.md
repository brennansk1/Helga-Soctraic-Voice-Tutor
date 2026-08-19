# Fitting the whole design into 24 GB — the allocation plan

_Measured 2026-08-19 on the actual machine, assuming Helga is the only workload._
_Companion to `MEMORY_BUDGET.md` (the raw measurements) and_
_`CONTENT_HYDRATION_PLAN.md` (the upgrades this has to accommodate)._

---

## The finding that settles it

**The safe ceiling for the model process is ~15.0–15.5 GB resident**, and
degradation past it is a **cliff, not a slope**.

Loaded at increasing sizes with generation verified each time:

| resident | free | decode | state |
|---|---|---|---|
| 13.18 GB | — | **30.1 tok/s** | comfortable (today) |
| **14.82 GB** | **24%** | **31.0 tok/s** | **healthy** |
| 15.75 GB | 14% | 30.9 tok/s | tight |
| 16.40 GB | 8% | — *(no clean output)* | swap 3.97 GB, thrashing |
| 17.72 GB | 6% | — *(no clean output)* | swap 4.79 GB, thrashing |

Throughput is **flat at ~31 tok/s right up to 15.75 GB**, then generation stops
returning usable results at all. There is no gentle slowdown to trade against —
you are either under the ceiling at full speed, or over it and effectively
broken. That makes the guard non-negotiable rather than a nicety.

Practical target: **stay at or under ~15.0 GB, ≥20% free.**

---

## What this rules out

**IQ4_XS is not reachable on this machine.** Its weights alone are ~15.7 GB —
already at the ceiling *before any KV cache or runtime overhead*. IQ4_NL, Q4_K_S
and Q4_K_M are further out still.

This matters beyond a configuration choice: **Stage 3 of the hydration plan — the
IQ3_S vs IQ4_XS experiment — is not executable on this hardware.** The research
proposed it as the test of whether aggressive quantisation drives the
false-claim rate. That test needs either a different machine or a different
model, and the plan should say so rather than leaving it as pending work.

The precision ceiling for nail-35b-a3b on 24 GB is **IQ3_M (3.66 bpw)**, and
even that is a small step from IQ3_S's 3.44. Both sit in the band the literature
calls degradation. **We cannot quantisation-fix the truth problem here.** The
ledger, retrieval and MiniCheck work in Stage 1 is not merely the first
priority — on this hardware it is the only available lever.

---

## The rule that makes everything fit: one model at a time

The upgrades add models, not just data:

| addition | size | when needed |
|---|---|---|
| MiniLM `all-MiniLM-L6-v2` | ~0.09 GB | ledger writes + retrieval |
| **MiniCheck-Flan-T5-Large 770M** | **~1.54 GB** fp16 / ~0.77 GB int8 | verification |
| embeddings on disk | ~0.06 GB | negligible, not resident |
| ledger / sources / claims in SQLite | <0.5 GB resident | negligible |

Run concurrently with the LLM, MiniCheck alone would force the model down a
tier — spending the entire precision budget on the verifier.

**So: run them as sequential phases, never concurrently.** Ollama already
unloads on `keep_alive: 0`, and verification is inherently a separate pass over
finished content. This is the existing "one model resident at a time" discipline
extended from the LLM to the whole pipeline.

---

## The allocation, phase by phase

### Phase 1 — Research sweep (no LLM)

| line | GB |
|---|---|
| OS floor | ~4.0 |
| Docker host-side | 0.88 |
| research + searxng | 0.31 *(measured)* |
| **total** | **~5.2** |

Network-bound, not memory-bound. Nothing to tune.

### Phase 2 — Skeleton + hydration (the constrained phase)

| line | GB |
|---|---|
| OS floor | ~4.0 |
| Docker host-side | 0.88 |
| research + searxng + core + rag | ~1.1 *(est)* |
| build process | ~0.5 *(est)* |
| MiniLM (ledger writes) | 0.09 |
| **available for the LLM** | **~15.4** |
| **guard** | keep the model ≤ **15.0** |

**Recommended configuration: `num_ctx 32768`, resident 13.51 GB.**

Not 64k, and the reason is the verifier. Sized from parameter counts:

| addition | resident |
|---|---|
| MiniLM `all-MiniLM-L6-v2` | 0.08 GB |
| bge-m3 *(already local, 566.7M @ F16)* | 1.06 GB |
| MiniCheck-Flan-T5-Large fp16 | 1.46 GB |
| MiniCheck-Flan-T5-Large int8 | 0.73 GB |

Against the 15.0 GB ceiling, co-residency works out as:

| LLM | + MiniLM | + bge-m3 | + MiniCheck fp16 | + MiniCheck int8 |
|---|---|---|---|---|
| @64k (14.17) | 14.25 OK | 15.23 **over** | 15.63 **over** | 14.90 OK |
| **@32k (13.51)** | **13.59 OK** | **14.57 OK** | **14.97 OK** | **14.24 OK** |
| @16k (13.18) | 13.26 OK | 14.24 OK | 14.64 OK | 13.91 OK |

**At 32k every planned addition fits co-resident with the model; at 64k most do
not.** That is worth more than the extra context: it means verification and
ledger embedding can run *without* unloading and reloading a 12.7 GB model
between phases, which costs ~142 s of cold load each time.

32k is still 2× today's window and comfortably more than lesson-batched
hydration needs (a lesson is 5–10 concepts). 64k stays available for a
generation-only pass with nothing co-resident. 128k (15.09 GB) is under the
ceiling on paper but leaves nothing for the container estimates being wrong.

**No new Modelfile is needed** — `num_ctx` is accepted as a per-request option,
which is how every measurement here was taken. Per-phase context is a request
parameter, not a model variant, and avoids duplicating a 12.7 GB blob on disk.

### Phase 3 — Verification (LLM unloaded)

| line | GB |
|---|---|
| OS floor + Docker | ~4.9 |
| MiniCheck fp16 | 1.54 |
| MiniLM | 0.09 |
| **total** | **~6.5** |

Enormous headroom. MiniCheck could run at full fp16 with room for batching, and
this phase could even run *concurrently with a build of a different course* if
that ever becomes useful — though serial is simpler and there is no deadline.

### Phase 4 — Tutoring (LLM + speech)

| line | GB |
|---|---|
| OS floor | ~4.0 |
| Docker host-side | 0.88 |
| web-ui + core + rag + tts | ~1.67 *(est)* |
| STT host-native | ~0.80 *(est, unmeasured)* |
| **available for the LLM** | **~14.6** |
| **guard** | keep the model ≤ **13.5** |

**Tutoring is tighter than building**, because TTS and STT are live. Two options,
both fine:

    IQ3_S  num_ctx 32768  ->  13.51 GB
    IQ3_S  num_ctx 16384  ->  13.18 GB   (today; safest)

A tutoring turn needs the concept's teaching object and recent dialogue, not a
course-sized window, so 16–32k is ample. **Do not run tutoring at the build-phase
context size** — the extra KV is wasted there and it is the phase with the least
headroom.

---

## Summary — the answer to "what fits safely"

| phase | model config | resident | headroom |
|---|---|---|---|
| research | none | ~5.2 total | vast |
| **build / hydrate** | **IQ3_S @ 32k** | **13.51** | **~1.9 GB** |
| build + MiniCheck fp16 co-resident | both | 14.97 | ~0.03 GB — works, no margin |
| build + MiniCheck int8 co-resident | both | **14.24** | **~0.8 GB** |
| verify (LLM unloaded) | MiniCheck only | ~6.5 total | vast |
| **tutor** | **IQ3_S @ 16–32k** | **13.18–13.51** | **~1.1 GB** |

Three rules hold it together:

1. **Never exceed ~15.0 GB resident.** The cliff at ~16 GB is abrupt and total —
   throughput does not taper, generation simply stops returning usable output.
2. **Prefer sequential phases; co-residency only at int8.** Unloading is always
   safe. If avoiding the ~142 s reload is worth it, MiniCheck at **int8**
   alongside the model at 32k leaves ~0.8 GB — fp16 leaves 0.03 GB, which is not
   a margin.
3. **Match `num_ctx` to the phase** via the per-request option: 32k building,
   16–32k tutoring. Context is cheap (20 KB/token measured) but not free, and
   tutoring is the phase with the least room.

---

## What to do next, in order

1. **Raise the build context to 32k** via the per-request `num_ctx` option — no
   new Modelfile, no download, measured cost +0.33 GB, and it keeps every
   planned addition co-resident.
2. **Instrument a full build for peak RSS.** The container and build-process
   figures above are estimates; they are the only soft numbers left, and they
   determine whether the 1.2 GB margin is real.
3. **Pull and measure IQ3_M** if the precision question stays live. It is the
   only tier above IQ3_S that fits, and its size here is scaled from bpw rather
   than measured.
4. **Amend `CONTENT_HYDRATION_PLAN.md` Stage 3**: the IQ4_XS experiment cannot
   run on this hardware. Either drop it or scope it to a different machine.
5. **Do not pull IQ4_XS.** It is a ~16 GB download for a configuration that
   cannot fit.

---

## Caveats

* The container figures marked *(est)* were taken from services that were not
  running (`core-logic`, `rag-engine`, `web-ui`, `tts`) or from `mem_limit`
  caps, which measured services undershoot substantially (research used 139 M of
  its 384 M cap). If the estimates are high, there is more room than shown.
* **STT was not running and is unmeasured.** It is the largest unknown in the
  tutoring budget.
* IQ3_M's size is scaled from bits-per-weight, not measured. Treat 13.55 GB of
  weights as ±0.3 GB until confirmed.
* `vm_stat`'s `active` figure is misleading here — it counts reclaimable file
  cache and read 10.10 GB while `memory_pressure` simultaneously reported 86%
  free. The kernel's pressure percentage is the number to trust, which is what
  the ceiling above is anchored to.
* Swap was already at 6.7 GB before this sweep began. A reboot before any
  overnight run would start from a cleaner state than any of these measurements.
