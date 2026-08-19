# Content hydration — the plan, from research

_Research delivered 2026-08-19 in answer to `RESEARCH_BRIEF_CONTENT_HYDRATION.md`.
This document records the findings, corrects one of them against local
measurement, and turns the rest into staged work. **Not yet implemented.**_

---

## The finding that reframes the work

**Q7 (the same idea re-taught across lessons) and Q8 (no whole-course awareness)
are not two problems. They are one problem with one answer**, and both are
consequences of hydrating each concept in isolation.

The answer is a **persistent taught-concepts ledger** — extracted claims,
concept ids, and MiniLM embeddings in SQLite — that is written after each
concept validates, retrieved into each subsequent hydration prompt, and enforced
by a **model-free** redundancy instrument rather than by prompt instructions.

This is also, per the research, the root cause of roughly half the hollowness.
It ranks first.

---

## Correction: the 16k context ceiling is not necessary

The brief presented `num_ctx 16384` as a hard memory constraint, and the
research reasoned carefully around it — flagging as an open question whether the
model is the hybrid (Gated-DeltaNet) or full-attention variant, and noting the
conservative full-attention figure of 96 KB/token.

**Resolved locally from the model's own config. It is the hybrid, and the KV
cost is far lower than either estimate:**

```
qwen35moe.block_count            40
qwen35moe.full_attention_interval 4      -> 10 layers hold a KV cache
qwen35moe.attention.head_count_kv 2
qwen35moe.attention.key_length    256
qwen35moe.attention.value_length  256
```

    per-token KV = 10 layers x 2 heads x (256 + 256) x 2 B  =  20 KB/token FP16
                                                               10 KB/token Q8_0

| num_ctx | KV @ FP16 | KV @ Q8_0 |
|---|---|---|
| 16,384 *(today)* | 0.31 GB | 0.16 GB |
| 32,768 | 0.62 GB | 0.31 GB |
| 65,536 | 1.25 GB | 0.62 GB |
| 131,072 | 2.50 GB | 1.25 GB |
| **262,144** *(model max)* | **5.00 GB** | **2.50 GB** |

Measured weights on disk: **13.35 GB** (the research estimated ~13.6 GB — close).
Against 24 GB that leaves roughly 8–9 GB, so **even the full 262k context fits**,
and 64k–128k fits with room to spare.

**Consequences:**

* The Q8 premise "the whole course does not fit" is **wrong as stated**. At 128k
  a substantial fraction of a course's teaching objects fits directly.
* Lesson-batched hydration is not merely affordable, it is comfortable, and
  **module-batched** hydration — which the research ruled out on the 16k
  figure — should be re-evaluated.
* The ledger-plus-retrieval design is still right. Retrieval beats stuffing
  regardless of budget: it stays linear as courses grow, and attention quality
  degrades on long contexts even when they fit. But the *reason* is quality and
  scaling, not memory.

Caveat: KV is not the only context-scaling cost — compute buffers grow too, and
this has not been measured. Raise `num_ctx` in steps with RSS measured at each,
rather than jumping to 262k.

---

## Findings that change existing design decisions

### 1. IQ3_S is below the safe quantisation band, on an MoE

The literature converges on **Q4_K as the upper bound of safe quantisation and
Q3_K as the start of degradation**, with K/Q layers the most sensitive. MoE
models are *more* quantisation-sensitive than dense ones: small perturbations
change top-k expert selection, altering the computation path — "expert-shift" —
and perplexity diverges sharply when it occurs.

IQ3_S is 3.44 bpw, below the safe band, on an MoE. **This is a mechanism-level
reason to suspect the quantisation is contributing to the verified-false-claims
finding**, and it fails silently: routing instability throws no error and no
structural check would ever see it.

Testable, not proven. The experiment is IQ4_XS vs IQ3_S on identical concepts,
measuring retries-per-concept and false-claim rate. IQ4_XS (~4.3 bpw, ~17.5 GB)
still fits — and with KV now known to be cheap, it fits more comfortably than
the research assumed.

### 2. There is a truth instrument we do not have

**MiniCheck-FT5** (770M Flan-T5 NLI) reaches 74.7% balanced accuracy on
LLM-AggreFact against GPT-4's 75.3%, at roughly 400× lower cost, CPU-runnable at
~500 docs/min. Per-sentence supported/unsupported against retained source
passages.

This is the first instrument that would read content **for truth**. Everything
we currently pass is structural. It does not eliminate the "grader shares the
generator's blind spots" risk — it is itself a model — so it must be validated
on a seeded false-claim set before anything is gated on it.

### 3. Storage: fold into SQLite, do not adopt DuckDB

SQLite's own benchmark: small blobs read/write **~35% faster in-database** than
as individual files, using **~20% less disk**, with the filesystem only winning
above ~100 KB. Concept bodies are 1–6 KB, ceiling ~15 KB — far below the
crossover.

DuckDB is columnar OLAP; our access pattern is single-row-by-primary-key OLTP.
It would be a category error and **a third storage bet after KuzuDB and ZIM**.

The real argument for folding content in is not the 35%. It is that
absent-vs-zero becomes structural (a row with an empty body is not a missing
row), that structure/state/content become transactionally consistent, and that
the Q3/Q7/Q8 retrieval indices become co-resident with what they index.

### 4. Prose is the wrong primary representation

The human never reads these files and the model re-expresses them anyway. Split
into two layers:

1. **A structured teaching object** (primary): atomic claims each carrying a
   source-span id, ordered worked-example steps, belief/correction pairs, edge
   cases, prerequisite concept-ids, question seeds per Bloom band. Addressable
   without regex, diffable for redundancy detection, directly checkable by
   MiniCheck.
2. **A short prose Core Explanation** (demoted): the LECTURE fallback when a
   learner is lost, and a human-audit artifact.

Missing today and needed: explicit prerequisite links (required for "cite, don't
re-teach"), source spans per claim (required for verification), and a
difficulty note per question seed.

**Retire the 150/250/400/600/800 word targets as generation targets.** We
already measured that length is the wrong proxy; the depth contract's structural
bands are the real instrument. Keep the numbers as a soft hint at most.

### 5. The target for repetition is not zero

Bruner, *The Process of Education* (1960): a curriculum "should revisit these
basic ideas repeatedly, building upon them until the student has grasped the
full formal apparatus that goes with them." Spaced re-exposure is the retention
mechanism the companion plan depends on.

So the ledger must distinguish three moves rather than suppress repetition:

| move | when |
|---|---|
| **Re-teach** (full introduction) | exactly **once**, at the concept's owning lesson |
| **Assume** ("you know X — recall it") | downstream; for a Socratic tutor often the best move |
| **Cite** (prerequisite link, no re-explanation) | the default downstream move |

The sharp gate is **structural, not similarity-based**: flag a concept that
*introduces from scratch* a claim already introduced upstream. Legitimate
reinforcement references a prior claim-id and adds new claims or complexity.
That distinction is computable from the claim graph with no model in it.

### 6. Hybrid retrieval, and turn on the MiniLM we already load

BM25-class lexical wins on exact terms, named results and identifiers; dense
wins on domain vocabulary and paraphrase. Fused with Reciprocal Rank Fusion,
hybrid beats either on nearly every BEIR benchmark. For a per-course corpus of a
few thousand passages, **brute-force cosine is milliseconds — no FAISS, no
vector DB**, which would be a third storage bet.

The unused `all-MiniLM-L6-v2` should be wired in, not removed: 384-dim, ~90 MB,
CPU-fine, and the same instrument Q7 needs.

### 7. Source text belongs at generation and grading time, not mid-session

A Socratic tutor asking a question needs distilled claims and mastery criteria,
not raw textbook prose in the window. It needs source text (a) at hydration, to
ground generation, and (b) at grading, to check an answer against fact.

So: a `sources` table plus a `claim_sources` join is the **durable home**. The
24h/7d research cache stays a speed layer and must never be the only copy.

### 8. Structured output: validate-and-correct, never constrained decoding

Because `minItems` is stripped for /v1 and /v1 ignores `format`, **no
pre-generation schema constraint binds in this pipeline**. A library that
promises constrained decoding (Outlines, Guidance) would give false confidence.

Instructor's Pydantic validate-then-retry loop feeds the validation error back
to the model — which is exactly the correction-round-naming-the-offender pattern
already measured at 5/5 here against 0/5 for prompt-only.

---

## Staged implementation

### Stage 1 — The ledger (Q7 + Q8) and the truth instrument

1. `taught` table: concept_id, atomic claims, MiniLM embedding, prerequisite links —
   written **after** validation.
2. Turn on MiniLM; hybrid FTS5 + dense over the ledger; inject k-nearest
   already-taught neighbours as **titles + one-line claims + ids**, never full
   bodies.
3. Hydrate in teaching order; batch a lesson's concepts into one call.
4. Model-free redundancy detector: MinHash/Jaccard over shingles (`datasketch`)
   for near-verbatim, MiniLM cosine + claim-overlap for paraphrase. Gate on
   "introduces a claim already introduced upstream"; correct by **naming the
   specific offending concept and claim**.
5. MiniCheck per-sentence against retained sources; gate presentation of
   unverified claims.

**Change course if:** redundancy flags don't correlate with human "this is
re-taught" judgements on a labelled slice, or MiniCheck's verdicts don't
correlate with known-false claims on a seeded set. Retune before gating on
either.

### Stage 2 — The substrate (Q1, Q2, Q3)

6. Fold concept bodies into a `concepts` table; restructure into teaching object
   + demoted prose; retire word targets as generation targets.
7. `sources` + `claim_sources` as the durable home; cache demoted to a speed
   layer; absent-vs-zero preserved through every new layer.
8. Separate append-only `session_notes` table **with its compaction boundary
   designed in now** — keep verbatim N months, then compact to FSRS state plus a
   summary. Retrofitting compaction onto years of rows is the painful path.

### Stage 3 — Quantisation and speed (Q6), measured

9. ~~A/B **IQ3_S vs IQ4_XS**~~ — **NOT EXECUTABLE ON THIS HARDWARE.**
   Measured after this plan was written (`docs/MEMORY_ALLOCATION_PLAN.md`):
   IQ4_XS weights are ~15.7 GB, and the safe ceiling for the model process on
   this 24 GB machine is ~15.0 GB — so it is over budget *before any KV cache
   or runtime overhead*. Past ~16 GB resident the machine does not degrade
   gracefully; throughput is flat at ~31 tok/s and then generation stops
   returning usable output entirely.

   The precision ceiling for nail-35b-a3b here is **IQ3_M (3.66 bpw)**, barely
   a step from IQ3_S's 3.44 and still inside the band the literature calls
   degradation. **The quantisation hypothesis for the false-claim rate cannot
   be tested on this box**, which makes Stage 1 not merely the first priority
   but the only available lever. Re-scope to different hardware or drop it.
10. Restructure prompts for **prefix caching** — invariant material (section
    template, depth-contract instructions, lesson context) first, per-concept
    material last, byte-identical across a lesson.
11. Raise `num_ctx` in steps with RSS measured at each. The KV table above says
    64k–128k is affordable; verify compute buffers agree.
12. MLX offers 2–3× decode on this MoE, but **stock MLX does not support IQ3_S
    or any GGUF i-quant — it upcasts to fp16 and blows past 24 GB.** Reaching it
    means an MLX-native 4-bit conversion, which is a migration, not a flag. It
    does compose with (9), since both move precision up.

### Stage 4 — Sources (Q5) and disk (Q4)

13. Extend domain routing to **LibreTexts, PhET, OpenAlex** (CC0, bulk snapshot —
    ideal for offline), **PubChem, DOAB, Gutenberg, NIST/OEIS, Wikidata**.
14. **License discipline:** avoid NC/ND for derived content; Stack Exchange dump
    terms exclude commercial use and LLM training since 2024; arXiv metadata is
    CC0 but full text is not ours to mirror. Check per item, not per source.
15. Instrument supplementary share **by claim, not by source count** — one weak
    book can dominate content while being a minority of the source list. This
    directly corrects the cap implemented in `course_builder.py`, which counts
    sources.

---

## What to measure — and what looks like success but is not

**Working:**

* False-claim rate per concept (MiniCheck against retained sources) — the core
  KPI, and the number that decides whether any speed work counts as progress
* Retries-per-concept (depth contract + fact-check) — the hidden cost that makes
  a "slower" quant potentially faster end to end
* Claims introduced-from-scratch that were already introduced upstream — the
  re-teaching signal
* Share of claims grounded **only** in supplementary sources, and their
  false-claim rate against at-or-above-bar claims — the 2.8 stress test

**Looks like working, is not:**

| signal | what it really means |
|---|---|
| every structural check passes | says nothing about truth — an alphabetical index once scored 100% coverage |
| redundancy near zero | the spiral has been suppressed; legitimate reinforcement was destroyed |
| prefix caching enabled, no speedup | a varying early token (timestamp, reordered field) silently broke the byte-identical prefix — **assert it** |
| faster per concept after a quant drop | retries may have risen; measure end-to-end, not per-call |
| zero retrieved neighbours | ledger empty vs nothing similar — absent-vs-zero, again |
| MoE quality drop | expert-shift throws no error; only an external truth instrument surfaces it |

---

## Caveats carried forward

* Every instrument today runs **inside** the system that generated the content.
  MiniCheck and the model-free detectors are the first that do not share the
  generator — but MiniCheck is still a model, so it reduces rather than
  eliminates shared blind spots.
* The M4 Pro decode figure (~30–55 tok/s at IQ3-class) is **inferred, not
  measured** on this exact quant and hardware. Measure before promising latency.
* The KV arithmetic above is computed from the model's config and is solid; the
  **compute-buffer** growth with context is not, and is the reason to raise
  `num_ctx` in steps.
* The supplementary-to-hydration argument remains an argument. Stage 4 item 15
  is what turns it into a measurement.
