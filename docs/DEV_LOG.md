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

---

## B12 — Online Search / Content Augmentation (`services/research/`, SearXNG)

*(New tree section — this course-creation path was previously scattered under B1.3 / B9.)*

### 1. Understanding
During course creation only (never live tutoring), `ContentHydrator` calls the research
service per concept (`POST /api/research_concept`, 15s, inside its ThreadPool≤3). The service
(`research_server.py`, port 5006): Wikipedia summary → 2-4 **SearXNG** queries → domain-tier
filter + dedup → top-3 page extractions (`trafilatura`) → ~3000-word combined reference +
confidence, fed into the LLM "condense & structure" prompt. `diskcache` (24h search / 7d
pages). Domain tiers prefer .edu/.gov/wiki and **block** cheating sites (chegg/quizlet/…).

### 2. Best tools / optimized?
- Solid design (self-hosted SearXNG = offline-friendly; tiered sourcing; caching).
- **Untestable here:** `trafilatura`/`wikipediaapi`/`diskcache` aren't installed on the dev
  host, so `research_server` can't be imported in CI — its pure logic was trapped behind
  network deps.
- **Dormant feature:** the mastery-based deeper queries (`mastery>=3/>=4`) never fired —
  the hydrator didn't pass `mastery`, so it defaulted to 1.
- `course_title` is threaded through the whole call chain but **never used** in queries.
- Citations: the service returns `sources` (URLs + tiers) but they aren't threaded into the
  generated content as inline citations (grounding gap — overlaps Tasks #8/#9).

### 3. Features weighed
- Extract pure helpers into a dep-free module → makes the ranking/query/scoring logic
  unit-testable now. Done.
- Activate mastery-aware query depth (pass it from the hydrator). Done — small, safe.
- Inline citations / provenance into the markdown — valuable but couples to the grounding
  work in Tasks #8/#9; left there.
- Async-loop reuse / batch endpoint — micro-opt, untestable here; skipped.

### 4. Refactored (this commit), tests green
- New `services/research/ranking.py` (no heavy deps): `domain_tier`, `build_search_queries`
  (mastery-aware), `compute_confidence`, `dedup_by_url`. `research_server` imports them;
  behavior unchanged. **+7 unit tests** (tiers, edu/gov, mastery query growth, confidence
  caps, stable dedup).
- Hydrator now passes `mastery` → activates the deeper-search queries (B12.5).
- 38 builder/research tests pass.

**Deferred:** inline citations (B12.8 → Tasks #8/#9); `course_title` unused (B12.9, harmless).

---

## B8 — Safety (`services/core/safety.py`, prompts)

**1-2. Understanding / eval.** `check_safety_detailed()` (keyword + TF-IDF, fitted at import)
gates user input in the FSM. The grading prompt was hardened earlier (B8.2); the **examiner
grade prompt** (used by the spaced-repetition path) still interpolated `user_answer` raw —
same injection exposure. The TF-IDF step is largely redundant (keyword substring already
triggers) but changing the safety classifier is behavior-risky with no offline way to
validate — left as noted.
**3-4. Refactor.** Applied the same `sanitize_untrusted` + fenced-spotlight pattern to
`get_examiner_grade_prompt`. Tests green.

## B9 — Infra / Ops (`docker-compose.yml`, Dockerfiles)

**1-2.** Earlier passes fixed the model-name mismatch, `.env`/secret wiring, and the STT
deployment. Remaining: `core-logic` declared `TTS_URL`/`RESEARCH_URL` but had **no
`depends_on`** for them, so it could call unready services at startup; `restart:
unless-stopped` everywhere (no crash-loop cap); `searxng:latest` unpinned; torch shares a
layer with requirements (cache churn).
**3-4.** Added `depends_on: tts (healthy), research (healthy)` to core-logic (correct startup
ordering). Kept `unless-stopped` (a valid production default for a single-box deploy — the
crash-loop concern is minor and `on-failure:N` risks leaving services down). Image pinning /
torch-layer split are build-time only and can't be validated in CI — noted for an infra PR.
Compose validates.

## B10 — Testing

**1-2.** `tts_server` and `research_server` had **zero** tests. `research_server` is
import-blocked off-container (heavy deps) — addressed in B12 by extracting `ranking.py`
(now tested). `tts_server` was import-blocked too: it did `os.makedirs("/app/data/...")`
**at import**, crashing off-container.
**3-4.** Made `tts_server` CACHE_DIR env-overridable with a temp-dir fallback (importable +
testable + more robust). Added **5 `tts_server` route tests** (health, voices, missing-text
400, WAV synthesis via a faked pipeline, unknown-voice fallback). `research_server` live
network/extraction still needs in-container/integration tests (noted).

## B11 — Dead Code & Stale Docs

Mostly resolved across earlier passes: deleted `rag/prompts.py`, `mock_safety.py`,
`scripts/initialize_db.py`, `scripts/validate_rag.py`, `services/rag/migrate.py`; corrected
CLAUDE.md; `night_audit.py` spun off as its own task (has reusable FSRS logic). Remaining
items are low-value and intentional: `service_manager.py` is a deliberate no-op compat stub;
the `play_sound`/`stop_audio` no-ops are harmless. Left as-is (noted) rather than churn.

**Section pass complete: B1-B12 all reviewed.** Remaining work is the runtime/browser-
validated tasks (#1, #6-#9) that can't be exercised in this Py-3.9 / no-Ollama / no-browser
environment.

---

## FE — Frontend sweep (page by page + design system)

### Foundation (committed 28118d6)
The design *language* was already good (Alpine theme, dark/light, status/Bloom tokens). The
problems were (a) missing scales and (b) inconsistent application. Added spacing /
typography / motion / focus-ring tokens + `--shadow-lg` + semantic color aliases; global
`:focus-visible` ring + reduced-motion guard. Expanded the build tree with a full FE
decomposition (design system / components / pages / cross-cutting).

### Color tokenization sweep (committed b5b03ce)
4 parallel agents (disjoint templates) + status.html SVG icons migrated ~25 hardcoded /
off-palette color literals (generic Tailwind green/amber/blue) onto theme tokens. Finding:
templates were MORE token-consistent than the raw inline-style count implied (many inline
styles already used `var()`); the real offenders were a handful of off-palette colors in
`course_structure.html` (node states) and stray `#fff`. Dark mode now consistent.

### Learn chatbox overhaul — the headline fix
**Root cause found:** `learn.html` links `static/css/learn-chat.css?v=2`, but **that file
never existed** (not in git history). So the entire chat shell, message bubbles, topbar,
composer, badges, and thinking indicator rendered essentially unstyled — *the* reason the
chatbox looked unprofessional.
**Fix:** authored `learn-chat.css` (372 lines, 100% design-token-based, dark-mode automatic)
covering every class the markup + `session.js` produce: full-height flex shell, sticky
topbar (back / breadcrumb+title / progress·mode·bloom badges), centered reading column,
distinct Helga (white card) vs user (tinted, right-aligned) bubbles with avatars, markdown
typography (p/ul/code/pre), grade badges (g1-g4 → danger…success), hover-reveal TTS/copy
actions, animated 3-dot thinking, hero, and a modern rounded composer (focus-ring,
send/mic/pause) + disclaimer. Neutralized the conflicting legacy `.chat-msg` padding/max-
width in style.css (load order verified: style.css → learn-chat.css). Bumped cache-buster
`?v=2 → ?v=3`. Validated: braces balanced, all 37 referenced tokens defined.

**Voice controls polish (TTS/STT/pause).** Made the chat's voice affordances professional +
token-based: mic recording is now a filled-danger button with an expanding pulse ring (was a
hardcoded color + scale jitter); transcribing spins the icon; the per-message TTS button has
proper play → loading (dim+spinner) → active (accent-filled stop icon) states and stays
visible while audio plays; pause/resume icon swaps via `data-paused` (JS-confirmed). No
`stt-preview`/`pause-overlay`/rails exist in the current markup (that legacy CSS is dead).

### FE3 page-by-page polish (done)
Completed the per-page consistency pass across all templates (home by hand as the exemplar;
courses/review/quiz/schedule/settings/course_view/structure/wizard via 4 parallel agents on
disjoint files). Each applied: off-scale font weights → `--font-weight-*`; matching
font-sizes → `--font-size-*`; spacing → `--space-*`; ad-hoc transitions → `--transition-*`;
card radii/shadows → tokens; and — the real consistency win — **status banners/badges now use
the `--bg-{success,warning,danger,info}` tint + `--color-*` border/text pattern** everywhere
(home stats error, settings toast/load-error, schedule review-status, course_structure
node-state badges). Stale `var(--token, #hex)` fallbacks stripped; undefined-token refs fixed.
Verified: all Jinja delimiters balanced, all CSS braces balanced, no undefined `var()` remain
(learn.html's `--path-*` vars are locally defined). Restored 2 settings cards the agent had
tightened from 12px → 8px back to the 12px card convention.

**Frontend sweep complete** (design system + all pages + the chatbox/voice overhaul). Remaining
frontend work is the larger feature Tasks #6/#7 (structured status events, SSE token streaming,
per-session scoping, learner dashboard, onboarding, global search, PWA) — runtime/browser-built.

---

## Task #6 (in progress) — B6.4 structured status events
Replaced the most fragile frontend pattern (free-text status-string parsing). `send_status_update`
gained an `event` param (web-ui already forwards the whole payload); `send_pipeline_stage(stage,
pct, **fields)` emits `{type:PIPELINE_STAGE, stage, pct, …}` + a human message (pct clamped).
`courses.js` prefers `data.event`, falls back to legacy parsing (additive, zero-risk). +4 backend
tests. Remaining (browser-validated): migrate creation call sites to `send_pipeline_stage`, then
delete the legacy parser; SSE token streaming; per-session FSM scoping.

## Task #11 (in progress) — course-creation parameter fidelity (static analysis done)
Audited `compute_course_params` + the SCOPE/MASTERY/STARTING_FROM profiles across the full 5×5×5
space:
- **BUG FIXED:** 15/125 combos produced `bloom_floor > bloom_ceiling` (e.g. mastery=Awareness
  ceiling 2 + starting=Advanced floor 4 → degenerate Bloom ramp). A starting level is a hard floor,
  so we now raise `bloom_ceiling = max(ceiling, floor)` — the course never ends below where it
  starts. +7 invariant/monotonicity tests (scope↑→modules↑, mastery↑→concepts+ceiling↑,
  start↑→floor↑, floor≤ceiling everywhere).
- **FLAG — hydration-time risk:** total concepts reaches ~110 (estimate) and ~132 actual at
  scope=5/mastery=5 (substructure bucketing rounds *up* at high mastery: targets 10/module, yields
  2×3×2=12). At ThreadPool≤3 with a research+LLM call per concept this is many minutes. Recommend a
  soft concept cap and/or a build-time estimate surfaced in the wizard (product decision).
- **FLAG — concepts_per_module drift:** the unit/lesson bucketing approximates the target (±~20%),
  not exact. Acceptable but documented.
- **FLAG — slider semantics:** starting_from 1 vs 2 produce identical *shape* (only content register
  differs); `skip_factor` reduces module *count*, conflating "breadth" with "starting level" (an
  advanced learner gets fewer modules, not just a higher floor) — a design question for the owner.
Remaining (runtime/eval): prompt-quality review for skeleton/audit/hydration + move generation to
schema-constrained output; generate sample courses across the param space and score for coherence /
coverage / non-redundancy / Bloom-appropriateness / Learn-readiness on the real Ollama.
