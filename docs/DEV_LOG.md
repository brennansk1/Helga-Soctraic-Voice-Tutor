# Helga Dev Log

Section-by-section refactor pass over the whole program. For each section (B1…B11
in `docs/HELGA_BUILD_TREE.md`) the method is: **(1)** understand it, **(2)** ask whether
it uses the best tools available for the hardware/libraries and is optimized, **(3)**
weigh features worth adding vs. not, **(4)** refactor to professional standards — best
tools, added features, optimized — with tests. Each entry records the analysis and what
changed vs. what was deferred (with reasons). Tests must stay green.

Target hardware: Mac Mini M4 Pro 24GB, fully offline. Host Python for tests is 3.9
(containers run 3.11); some model code can't be exercised in this env and is noted.

---

## B1 — Course Creation Pipeline (`services/core/course_builder.py`)

### 1. Understanding
Three sequential stages on a ~2,950-line module:
- **SkeletonBuilder** — `compute_course_params(scope, mastery, starting_from)` (3-slider
  system) → module generation (3-retry LLM) → `_build_substructures_progressive()` builds
  Units→Lessons→Concepts via per-node LLM calls. Dedup via `_is_duplicate()` (exact /
  substring / word-overlap / `difflib` similarity) keyed by `used_titles_by_level`.
- **SyllabusAuditor** — programmatic dedup pass + an LLM quality/rename pass.
- **ContentHydrator** — per concept: research service (Wikipedia + SearXNG) then an LLM
  "condense & structure" call; parallelized with `ThreadPoolExecutor(max_workers≤3)`.

### 2. Best tools / optimized?
- LLM access is via Ollama's OpenAI-compatible API (good). **Gap:** generation does *not*
  use Ollama's schema-constrained `format` yet (the grading path now does, Task #2) — the
  module/unit/lesson/concept JSON is free-form + repaired. Constraining it would cut the
  retry rate. (Deferred — touches many call sites; high-value follow-up.)
- Dedup used `difflib.SequenceMatcher` rebuilt per candidate → **O(n²·L)** (PERF-2). Fixed.
- Three speculative `gc.collect()` calls — pointless latency on a 24GB host. Removed.
- Per-module Bloom target was computed by a duplicated inline formula at 2 sites with
  divergent `.get()` defaults (B1.1.4). Consolidated.
- Hydration parallelism is capped at 3 workers — conservative for 24GB; could rise, but
  it's bounded by Ollama's single-model throughput, so more workers ≠ faster generation.
  Left as-is.

### 3. Features weighed
- **Schema-constrained generation** (Ollama `format`) for skeleton + hydration JSON — HIGH
  value (reliability), moderate effort. **Queued** as the next B1 pass (kept out of this
  commit to keep it small and reviewable).
- MinHash/token-bucket dedup — rejected: difflib + the new upper-bound gating is already
  sub-quadratic in practice at course scale; MinHash adds complexity for no real win here.
- Richer content structuring / contextual chunk headers — belongs to B2 (RAG), tracked there.

### 4. Refactored (this commit) — all behavior-preserving, tests green
- `_is_duplicate()`: reuse one `SequenceMatcher` with `new_norm` cached as seq2 and gate
  `ratio()` behind `real_quick_ratio()`/`quick_ratio()` (mirrors `difflib.get_close_matches`).
  Identical results, far fewer full comparisons.
- Removed all `gc.collect()` and the unused `import gc`; `close()` is now a no-op.
- Extracted `progressive_bloom(index, total, floor, ceiling)` + module-level `BLOOM_LABELS`;
  replaced both inline recompute sites. Parity verified exhaustively (all n≤11, all
  floor≤ceiling) against the old formula.
- Tests: 31 course-builder tests pass; added a parity/property test for `progressive_bloom`.

**Deferred (tracked):** schema-constrained generation for the builder (next B1 pass).

---

## B2 — Knowledge / RAG Layer (`services/rag/librarian.py`)

### 1. Understanding
The RAG service does course CRUD, search, flashcards, quiz. Search was substring-only;
**B2.2 already replaced it with SQLite FTS5** (title + content, bm25) via `SearchStore`
(prior commit), with a reindex hook on course build. The embedding model
(`all-MiniLM-L6-v2`) was imported and **eagerly loaded at module import** (line 59) but
**never called** — search didn't use it.

### 2. Best tools / optimized?
- **Dead eager load:** the embedding model + `sentence_transformers`/`numpy` imports were
  pure startup cost (verified: `model` referenced only at its own definition; `np.` never
  used). On the host, the hard `sentence_transformers` import even prevents importing
  `librarian` without the heavy dep.
- **No semantic retrieval:** lexical FTS5 is good but misses paraphrase matches
  ("photosynthesis" vs "how plants make food"). Research (§2) says the highest-leverage
  RAG win is **hybrid** (FTS5 + dense) fused by **RRF**, then a **reranker** — bigger than
  the vector-store choice itself.
- **Deps not installable here:** `sentence-transformers` and `sqlite-vec` are absent on the
  Python-3.9 dev host, so dense retrieval can't be unit-tested locally — only in the
  rag-engine container (3.11).

### 3. Features weighed
- **Full hybrid retrieval** (sqlite-vec + bge-m3/nomic embeddings + bge-reranker-v2-m3 +
  header-aware chunking) — HIGHEST capability value, but model/runtime-dependent and
  untestable here. **Queued as Task #8 (runtime-validated)** rather than shipped unverified.
- **RRF fusion core** — pure, deterministic, testable *now*. Built it so the dense work has
  a tested foundation to plug into.
- Removing the embedding model entirely — rejected; it's the seam for the hybrid feature.
  Made it lazy instead.

### 4. Refactored (this commit), tests green
- `librarian.py`: removed the eager unused model load + dead `sentence_transformers`/`numpy`
  imports; added lazy `get_embed_model()` (loads only when dense retrieval calls it;
  `EMBED_MODEL` env-overridable). Cuts container startup cost; makes the module importable
  without the heavy dep.
- New `services/common/retrieval.py`: `reciprocal_rank_fusion(ranked_lists, k=60, key=…)` —
  score-normalization-free hybrid fusion, the reusable core for Task #8. +6 unit tests
  (formula, dedup-by-identity, k-sensitivity, ties, validation).

**Deferred → Task #8 (runtime-validated):** dense vectors (sqlite-vec) + reranker + chunking,
benchmarked in-container / on the M4.
