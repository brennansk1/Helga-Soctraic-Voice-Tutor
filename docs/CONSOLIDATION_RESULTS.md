# Course-creation consolidation on Nail — measured results

_2026-08-18, Mac Mini M4 Pro 24 GB, `nail-35b-a3b`, research + searxng live._

## Verified improvements

| metric | before (chunked) | after (consolidated) | change |
|---|---|---|---|
| LLM calls per build | 27 | **6** | **−78%** |
| Wall clock | 185.3 s | **151.0 s** | **−18%** |
| Concepts produced | 19 | 20 | +1 |
| Duplicate titles | 0 | 0 | — |
| Fallback titles | 0 | 0 | — |
| Empty lessons | 0 | 0 | — |
| Went UNGUIDED | **yes** | **no** | fixed |
| Repeat brief lookup | 34.9 s | **0.00 s** | cached |

Structure is identical (4 modules / 4 units / 4 lessons); no quality metric
regressed. Test suite: 1358 passed.

## What was actually wrong

1. **Constrained decoding never worked.** Both LLM paths sent Ollama's native
   `format` field to the OpenAI-compatible `/v1` endpoint, which reads only
   `response_format`. Nested-schema test: native → 2408 chars of invented shape,
   strict parse failed; `response_format` → 202 chars, exact schema. This is the
   mechanism `model_roles.py` calls load-bearing, and it was inert.
2. **Grounding was fetched and discarded.** `subject_outline()` takes
   `broader_subjects` for exactly the narrow-topic case that produced the
   42%-coverage course — and `curriculum_brief()` never passed it. The builder
   compensated with one full Wikibooks+Wikiversity+Wikipedia+Archive sweep per
   candidate; Wikimedia throttles bursts, so the third candidate (the
   discipline-level one that works) returned empty and the build went unguided.
   `curriculum_brief('Geometry')` standalone → 31 chapters; same call third in a
   burst → 0.
3. **Per-level decomposition was small-model scaffolding.** Generating units,
   then lessons per unit, then concepts per lesson meant each call was blind to
   its siblings, with uniqueness enforced from outside via an injected blacklist.
   One call per module lets the model see the whole subtree while naming it.

## Open: coverage is still incomplete

Removing the unguided failure mode was **necessary but not sufficient.** The
syllabus check still reports missing topics on `The Pythagorean Theorem`
(including the formula itself, Pythagorean triples, the converse, and the
distance formula).

Diagnosis: broadening found *Primary Mathematics* (24 chapters) — a
whole-discipline textbook whose chapter structure is generic arithmetic, not the
theorem's sub-topics. So the builder is now guided, but guided by structure that
is too coarse to enforce breadth on a narrow topic. The generated concepts are
also very fine-grained ("Cathetus Terminology", "Standard Vertex Labeling"),
covering depth at the expense of breadth.

Next lever, in order:
1. Prefer a **topically-matched** outline over the first discipline-level hit —
   rank candidate books by title/section overlap with the topic before accepting.
2. Feed the syllabus check's `not covered` list back into module generation as a
   required-coverage list (the depth contract's named-element retry pattern,
   which already converges).
3. Only then consider parallelising the 4 subtree calls, which trades the
   cross-module coverage context for speed and should be measured for duplicates.
