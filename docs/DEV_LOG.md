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
