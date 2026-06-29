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

---

## B3 — Tutoring Engine / Socratic (`services/core/fsm_logic.py`)

### 1. Understanding
9-state FSM; `transition()` dispatch (globals → state-specific). Socratic loop: select one
of 6 question types → grade the answer (1-4) → Bloom progression + mastery gate → advance.
Grading already hardened earlier: B3.3 (no false-pass → grade 2) + schema-constrained JSON.

### 2. Best tools / optimized?
- Cleaner than the audit implied: **0 bare excepts**; the only "DEBUG" logs are already
  commented out (LOG-1 effectively done).
- `handle_socratic_answer()` was ~290 lines with a large inline JSON-grade parser mixing
  LLM I/O, parsing, and side effects — hard to test in isolation.
- Real gaps are **pedagogy features**, not mechanical debt: no hint ladder, misconceptions
  are transient (not persisted for review), no answer-key verifier, prereqs built but not
  enforced. These need live-LLM behavior and can't be unit-tested in CI.
- `_call_llm()` flattens role history into one user string (loses turn structure). Could add
  `LLMClient.chat_messages()` — deferred: changes live-LLM I/O with no local way to validate
  the output difference; not worth blind change now.

### 3. Features weighed
- Pedagogy upgrades (LearnLM prompt, hint ladder, misconception persistence, answer-key
  verify, prereq gating, pyBKT mastery) — HIGH learning-outcome value but runtime/LLM-bound.
  **Queued as Task #9 (Tier C)**; validate with `tools/grading_eval.py` on real Ollama.
- Extracting the grade parser — pure, testable, improves readability now. Done.

### 4. Refactored (this commit), tests green
- Extracted `_parse_grade_response(content) -> {grade, missing_concepts, feedback, reason}`
  from `handle_socratic_answer()` — pure, tolerant of fences/prose/"Grade N", preserves the
  B3.3 grade-2-on-failure rule. `handle_socratic_answer` now calls it (shorter, clearer).
- Tests: existing grading tests still green; **+5 direct parser tests** (clean JSON, fenced,
  "Grade N" string, None→2, garbage→2). 12 grading tests pass.

**Deferred → Task #9 (runtime-validated):** the pedagogy features above; `chat_messages()`.

---

## B5 — Persistence / Data Integrity (`services/common/storage.py`, `background_ops.py`)

### 1. Understanding
Three-tier store (SQLite + JSON course trees + Markdown). Thread-local pooled connections
with WAL; column-whitelist upserts. Earlier fixes: B5.5 (course_uid preservation) and B5.6
(N+1 removal).

### 2. Best tools / optimized?
- **Dual migrations** (B5.4): `services/rag/migrate.py` (file-based `schema.sql` +
  `migrations/*.sql`, inserts a `description` column the inline schema lacks) is **imported
  nowhere** — the live path is the inline migrations in `storage._init_db`. It's dead +
  inconsistent. → delete.
- **`gamification` table** (B5.8): re-examined — it *is* created, lazily, by
  `librarian._get_profile_db()` (not `storage._init_db`). So it's not "never created", but
  the creation is split across modules and `background_ops._reset_daily_streak` only works
  after the gamification API is first hit. That reset also used **f-string SQL** for the
  date (not user-controlled, but violates the parameterized-query convention).
- **`get_streak()` over-count** (real bug): the "allow today to not have activity yet"
  tolerance ran on *every* row, so a gap (e.g. activity today + 2 days ago, none yesterday)
  was counted as a 2-day streak instead of 1.
- **`CourseStore`** opens a fresh `sqlite3.connect()` per metadata op rather than the
  thread-local pool. Assessed as low-impact: WAL is a persistent DB-file property so fresh
  connections still use it, and at single-user offline scale connection overhead is
  negligible. Left as-is (noted) to avoid commit/threading risk for no real gain.
- **`make clean`** ran `docker system prune -f` — nukes the whole build cache.

### 3. Features weighed
- DB backup (B5.7) — already had a `cp`-based target; hardened it (live-DB-safe + courses).
- Cross-store JSON↔SQLite transaction (B5.3) — larger; the AUTO-10 ordering already reduces
  the window. Deferred (noted) — not worth a risky change without a failure repro.

### 4. Refactored (this commit), tests green
- Fixed `get_streak()` with a correct anchor-walk; **+6 tests** (incl. the gap case that
  exposed the bug, yesterday-only, stale-activity).
- Deleted dead/inconsistent `services/rag/migrate.py` (B5.4).
- Parameterized the `_reset_daily_streak` date SQL (B5.8 convention).
- Hardened `make backup` (`sqlite3 .backup` + `data/courses` tarball); `make clean` now
  `docker image prune -f` (keeps build cache).
- Full suite 407 passing.

**Deferred (noted):** cross-store transaction (B5.3); CourseStore pooling (no real benefit).

---

## B6 — Web UI / Frontend (`app.py`, templates, `static/js/*`)

### 1. Understanding
Flask + Socket.IO proxy; vanilla-JS frontend with hand-rolled chat diffing/streaming. Event
flow Browser → `/api/event` → core → FSM; status/state pushed back over Socket.IO + a 2s
state poller. Already improved this session: B6.5 (voice-key unified) and the STT voice loop
(push-to-talk mic → `/api/stt` → `TEXT_INPUT`, barge-in).

### 2. Best tools / optimized?
The audit's structural items — free-text status-string parsing, multiple `io()` sockets,
stacked listeners, monkey-patched `updateChatStream`, polling instead of push, global FSM
state — are a **coherent overhaul**, not isolated bugs. They're scoped as **Tasks #6
(Learn UX) and #7 (app-wide)**, which also pull in dark mode, dashboards, onboarding, global
search, accessibility, mobile, and (optionally) a lightweight reactive layer / PWA.
Doing them piecemeal here would churn code I can't run-test (no browser in CI), so the
overhaul stays task-scoped. Confirmed-dead code, however, is safe to remove now.

### 3. Features weighed
- Full overhaul (structured events, SSE streaming, per-session scoping, dashboard) — high
  value, large, browser-validated → Tasks #6/#7. Not slammed in blind.
- Dead `update_settings` socket emit — no server handler; voice persists via localStorage
  since B6.5. Safe to remove now.
- `EDIT_MESSAGE` — has an FSM handler but the frontend `contentEditable` trigger never
  fires (rendered messages use `.chat-msg`, never set editable). Left wired + noted rather
  than ripping out both ends (it's a latent feature, not harmful).

### 4. Refactored (this commit)
- Removed the dead `socket.emit('update_settings')` from `handleVoiceChange` (no handler).
  `node --check` clean.
- The substantive Learn + app-wide UX work remains **Tasks #6 / #7** (browser-validated).

**Deferred → Tasks #6 / #7:** structured status events, SSE streaming, per-session scoping,
dashboard/analytics, dark mode, onboarding, global search, accessibility, mobile, PWA.
