# Helga — Build Tree: K-12 Socratic Curriculum Platform

> **TWO LAYERS.** This document now tracks both (1) the **Baseline** — the reverse-engineered
> single-user appliance (Phases 0–3, B1–B14, FE1–FE4 below; generated 2026-06-29 from a 4-agent
> sweep + 6-stream research, source HEAD `b1aa72e`) — and (2) the **Target Design** — the
> transformation into a multi-tenant, grade-adaptive, Utah-standards-aligned tutoring product for
> Utah K-12 homeschool/school students funded by the Utah Fits All Scholarship. The Target Design
> adds branches **B15–B27** and **FE5–FE8**, the **Tier F–J** roadmap, and the **expansion manifest**
> (see `docs/BUILD_MANIFEST.md`). Curriculum source-of-truth: `docs/UTAH_K12_CURRICULUM_REFERENCE.md`.
> Everything labeled "(as-is)" / "Baseline" describes the appliance we are building *from*.

---

## TARGET ARCHITECTURE (K-12 platform) — what we are building toward

The product *is* the grade-level-adapted Socratic bot that **teaches the official Utah Core
curriculum** (plus custom electives), sold to families and funded by the homeschool grant.
Four transformations the baseline cannot do, plus supporting workstreams:

| Field | Baseline (as-is) | Target |
|---|---|---|
| **Product** | personal offline tutor | Utah K-12 multi-tenant tutoring (grant-funded, sellable) |
| **Tenancy** | single global FSM, no accounts | Parent (billing owner) → one+ Student profiles, fully isolated |
| **Runtime target** | Mac Mini M4 Pro, offline | Linux GPU server, multi-tenant, self-hosted Qwen/Ollama (GPU fair-queue) |
| **Curriculum** | LLM-generated on demand | curated, standards-tagged, **versioned, human-reviewed** catalog |
| **Teaching** | persona "styles" only | grade-band (K-2/3-5/6-8/9-12) vocabulary, Bloom, scaffolding adaptation |
| **Audience UI** | one learner, 8 tabs | kid-first student app + role-gated **parent dashboard** + admin CMS |
| **Assessment** | quiz + Socratic grade | formal exams, per-standard checkpoints, **interest-themed** items |
| **Commerce/legal** | none | Stripe billing + seats; COPPA/FERPA/Utah data law; consent gating |

```
Target topology (single GPU server first; scale-out deferred):
Internet ─TLS→ Caddy → gunicorn(gevent) → web-ui  (Flask-Login, room-scoped Socket.IO)
                                            │ student_id on every event
                                            ▼
                                core-logic  FSM REGISTRY (per-student instances) ─┐
                                            │                                     │ GPU fair-queue
                                            ▼                                     ▼  (semaphore + round-robin)
                              rag/catalog (read-only standards catalog)      Ollama/Qwen (host GPU)
                                            │
                       helga.db (student_id-scoped; SQLite→Postgres when contended)
                       data/catalog/ (curated courses) + data/courses/ (electives)
                       Stripe webhook → subscriptions ; object storage (deferred)
```

Detailed staged design lives in the approved engineering plan; the feature-tree branches
**B15–B27** (below, after the baseline tree) decompose it at this document's usual depth, and
`docs/BUILD_MANIFEST.md` is the actionable checklist with acceptance criteria + releases.

> **Implementation-ready design specs are complete** in `docs/design/` (index:
> `docs/design/00_INDEX.md`) — exact schemas, API contracts, state machines, and parameter tables for
> every target branch. Schema source-of-truth: `docs/design/01_DATA_MODEL.md`. Spec map: B15→03 ·
> B16/B26→04 · B17→02 · B18→05 · B19→06 · B20→09 · B21→08 · B22→07 · B23/B27→10 · I/FE5-8/B24→11.

---

## PHASE 0 — CALIBRATION

| Field | Value |
|---|---|
| **Project type** | REFACTOR / UPGRADE (existing system reverse-engineered into the tree) |
| **Execution mode** | HYBRID (branch/cluster at a time, pause at integration checkpoints) |
| **Runtime target** | Mac Mini M4 Pro, 24GB unified memory, fully offline |
| **LLM** | Ollama + **Qwen3-14B** (`qwen3:14b`), OpenAI-compat chat API at `host.docker.internal:11434` |
| **STT** | **Removed** — system is text-only (microphone code deleted) |
| **TTS** | **Kokoro-82M** (`kokoro==0.3.4`), on-demand WAV, port 5005 |
| **Embeddings** | `all-MiniLM-L6-v2` loaded but **never invoked** (see B2.1) |
| **Storage** | SQLite (`helga.db`, WAL) + JSON course trees + Markdown concept files |
| **Search aug** | SearXNG (8080) + research service (5006), used at *build time only* |
| **Web** | Flask + Flask-SocketIO (gevent), vanilla JS, Jinja templates, port 5050 |
| **Conventions** | Python 3.11; `os.path.join`; thread-local SQLite; whitelist SQL identifiers; LLM via `llm_utils.py`; `logging.getLogger(__name__)`; pytest |

### Calibration finding: documentation is materially stale
`CLAUDE.md` describes a **different system than the one that exists** (Jetson, Qwen2.5-1.5B GGUF, Piper, Faster-Whisper, KuzuDB, ZIM, `inference-llm`/`audio-engine`/`input-node` services). Reality is Mac Mini + Qwen3-14B/Ollama + Kokoro + SQLite + SearXNG/research. **16 concrete contradictions** catalogued in B4.STALE. This must be fixed first — every future contributor (human or AI) is being misled.

---

## PHASE 1 — THE TRUNK (as-is)

**Entry/orchestration:** `main.py` (health-checks Docker+Ollama, builds, `compose up`, polls 5 services) and `deploy.sh` (pulls model, `compose up`). Six containers on one bridge network:

```
Browser (5050)
   │  HTTP POST /api/event  (+ Socket.IO for status/state push)
   ▼
web-ui  :5000→5050   Flask+SocketIO proxy, state_poller(2s), health_poller(5s)
   │  HTTP /event
   ▼
core-logic :5003     MnemosyneFSM (9 states) + SkeletonBuilder/Auditor/Hydrator
   │            │ HTTP                 │ HTTP (build-time only)
   ▼            ▼                      ▼
rag-engine   tts :5005            research :5006 ── searxng :8080
 :5002        Kokoro              SearXNG content augmentation
 SQLite+JSON facade, "search" (substring), flashcards, quiz
   │
   ▼
helga.db (WAL) + data/courses/{uid}/structure.json + content/{con}.md
```

**Core data model:** courses (SQLite `courses` mirror + `structure.json` tree: Course→Module→Unit→Lesson→Concept, `*_` hex UIDs); `user_progress` (SM-2 + Bloom fields); `flashcards` (FSRS fields); `activity_log`; `scheduled_reviews` (legacy); `user_settings`; `schema_version`.

**Event trunk:** `transition()` (`fsm_logic.py:555`) dispatches global commands → transcript edits → global handlers (`SET_CONTEXT`, `NAVIGATE_TO_TOPIC`, `PAUSE/RESUME`) → state-specific (`TEXT_INPUT`, `SKIP_CONCEPT`, `DELETE_COURSE`). State is a **single global FSM instance** — there is no per-session/per-user scoping.

---

## PHASE 2 — FEATURE TREE (as-is status + discovered gaps)

Status: ✅ implemented · ✔️ verified-by-tests · 🧩 stub/dead · ⬜ gap (not-started) · 🚫 broken

```
T   Helga Trunk ...................................................... ✅
├─ B1  Course Creation Pipeline ..................................... ✅
│  ├─ B1.1  SkeletonBuilder (modules→units→lessons→concepts) ........ ✅
│  │   ├─ B1.1.1  3-slider param system (scope/mastery/start) ....... ✅
│  │   ├─ B1.1.2  Module gen w/ 3-retry ............................. ✅
│  │   ├─ B1.1.3  difflib dedup — matcher reuse + upper-bound gating ✅ PERF
│  │   └─ B1.1.4  Bloom target — single progressive_bloom() helper . ✅
│  ├─ B1.2  SyllabusAuditor (dedup + LLM quality pass) ............. ✅
│  ├─ B1.3  ContentHydrator (research+LLM per concept) ............. ✅
│  │   ├─ B1.3.1  ThreadPool(3) + per-concept 15s research HTTP .... 🧩 PERF
│  │   └─ B1.3.2  speculative gc.collect() removed ................. ✅
│  ├─ B1.4  Custom Course Wizard ................................... ✅ (3 overlapping endpoints)
│  └─ B1.5  EPUB ingestion ......................................... 🚫 returns 202, no real impl
├─ B2  Knowledge / RAG Layer ....................................... 🔨
│  ├─ B2.1  Semantic retrieval — lazy model + RRF + guarded hybrid . 🔨 (?mode=hybrid, FTS5 fallback; needs sqlite-vec)
│  ├─ B2.2  Keyword search — SQLite FTS5 (title+content, bm25) ..... ✅
│  ├─ B2.3  Chunking / passages .................................... ⬜ (Task #8)
│  ├─ B2.4  Reranking .............................................. ⬜ (Task #8)
│  ├─ B2.5  Citations / grounding .................................. ⬜ (prompts tell model to "fill gaps")
│  └─ B2.6  Knowledge graph (prereq edges) ......................... ⬜ (KuzuDB removed)
├─ B3  Tutoring Engine (Socratic) .................................. ✅
│  ├─ B3.1  9-state FSM dispatch ................................... ✅
│  ├─ B3.2  Socratic question types (6) ............................ ✅
│  ├─ B3.3  Grading — constrained JSON + grade-2 fallback + parser . ✅
│  ├─ B3.4  Bloom progression ...................................... ✅
│  ├─ B3.5  Mastery gate ........................................... ✅ (verified reachable — not a bug)
│  ├─ B3.6  Hint laddering / scaffolding ........................... ⬜ (Task #9)
│  ├─ B3.7  Misconception persistence .............................. ⬜ (Task #9)
│  ├─ B3.8  Answer-key verification layer .......................... ⬜ (Task #9)
│  └─ B3.9  Prereq enforcement at tutoring time .................... ⬜ (Task #9)
├─ B4  Spaced Repetition ........................................... 🚫
│  ├─ B4.1  FSRSEngine ............................................. 🚫 py-fsrs imported, NEVER used; hand-rolled formula sign-inverted
│  ├─ B4.2  SM-2 legacy ............................................ 🧩 deprecated, half-wired
│  ├─ B4.3  SR↔Socratic integration ............................... ⬜ (cards created, never quizzed in dialogue)
│  └─ B4.4  Memory Palace .......................................... 🧩 UI+routes dead (/palace→/)
├─ B5  Persistence / Data Integrity ................................ ✅
│  ├─ B5.1  Thread-local SQLite + WAL .............................. ✅ (CourseStore bypasses it)
│  ├─ B5.2  Column-whitelist upsert ................................ ✅
│  ├─ B5.3  JSON↔SQLite consistency ............................... ⬜ cross-store txn (deferred, noted)
│  ├─ B5.4  Migrations — deleted dead/inconsistent migrate.py ...... ✅
│  ├─ B5.5  update_mastery preserves course_uid ................... ✅
│  ├─ B5.6  N+1 queries — single progress query .................... ✅
│  ├─ B5.7  Backup — hardened (sqlite .backup + courses tar) ....... ✅
│  └─ B5.8  gamification SQL parameterized (table is lazy-created) . ✅
├─ B6  Web UI / Frontend ........................................... ✅
│  ├─ B6.1  Chat render + streaming tokens ......................... ✅ (monkey-patched reconcile)
│  ├─ B6.2  Path/journey view ...................................... ✅
│  ├─ B6.3  Per-session state scoping .............................. 🚫 global broadcast → no multi-tab/user
│  ├─ B6.4  Structured status events ............................... 🔨 contract+helper+consumer (call-site migration remains)
│  ├─ B6.5  Voice selection wiring — key unified; dead emit removed  ✅
│  ├─ B6.6  Markdown renderer ...................................... 🧩 hand-rolled, lookbehind breaks old Safari
│  ├─ B6.7  Dashboard / analytics .................................. ⬜
│  ├─ B6.8  Transcript export / history ............................ ⬜
│  ├─ B6.9  Global search / onboarding / dark-mode polish .......... ⬜
│  └─ B6.10 Dead code (ZIM/sudo modal, EDIT_MESSAGE, palace, SM-2) .. 🧩
├─ B7  Voice (TTS) ................................................. ✅
│  ├─ B7.1  Kokoro batch synth + cache ............................. ✅
│  ├─ B7.2  Streaming / first-audio latency ........................ ⬜ (batch only)
│  └─ B7.3  STT / barge-in / turn-taking ........................... ⬜ (STT removed)
├─ B8  Safety .......................................................🧩
│  ├─ B8.1  Keyword + TF-IDF filter ................................ 🧩 TF-IDF redundant; context override weak
│  └─ B8.2  Prompt-injection defense — grading + examiner fenced ... ✅
├─ B9  Infra / Ops ................................................. ✅
│  ├─ B9.1  docker-compose (6 svc, healthchecks) ................... ✅
│  ├─ B9.2  OLLAMA_MODEL mismatch (qwen2.5 vs qwen3) ............... 🚫 BUG
│  ├─ B9.3  .env not interpolated (random Flask secret) ............ 🚫
│  ├─ B9.4  Internal ports exposed to host ......................... 🧩 security
│  ├─ B9.5  Ollama SPOF (no circuit breaker) ....................... ⬜
│  ├─ B9.6  Image build layering (torch re-download) ............... 🧩 PERF
│  ├─ B9.7  Metrics / tracing / structured logs .................... ⬜
│  └─ B9.8  Auth ................................................... ⬜
├─ B10 Testing .....................................................✅
│  ├─ B10.1 Unit (storage/llm_utils/fsrs strong; 412 tests) ........ ✔️
│  ├─ B10.2 Real E2E (event path) .................................. 🚫 mocked / no server fixture
│  ├─ B10.3 tts_server tests ✅ + research ranking.py tested ....... 🔨 (research live path: integration)
│  └─ B10.4 CI runs Docker/compose/e2e ............................. ⬜
├─ B11 Dead Code & Stale Docs ......................................🧩
   ├─ B11.1 night_audit.py + scripts/*.py import kuzu (crash) ...... 🧩
   ├─ B11.2 service_manager.py no-op stub .......................... 🧩
   ├─ B11.3 mock_safety.py type-mismatched (dangerous) ............. 🧩
   ├─ B11.4 rag/prompts.py imports non-existent get_architect_prompt 🚫 ImportError
   ├─ B11.5 audio no-ops still called throughout FSM ............... 🧩
   └─ B11.6 CLAUDE.md — 16 contradictions vs reality ............... 🚫
└─ B12 Online Search / Content Augmentation (course-creation) ...... ✅
   ├─ B12.1 SearXNG self-hosted search (port 8080) ................. ✅
   ├─ B12.2 research_server pipeline (wiki + searxng + trafilatura)  ✅
   ├─ B12.3 Domain quality tiers + blocked-site filtering .......... ✅
   ├─ B12.4 Pure ranking/query/confidence helpers (ranking.py) ..... ✅ extracted + tested
   ├─ B12.5 Mastery-aware query depth ............................. ✅ wired from hydrator (was dormant)
   ├─ B12.6 diskcache (24h search / 7d pages) ..................... ✅
   ├─ B12.7 Hydrator integration (per-concept, ThreadPool≤3) ...... ✅
   ├─ B12.8 Citations/provenance into generated content .......... ⬜ (sources returned, not cited → Task #8/#9)
   └─ B12.9 Unused course_title param threaded through ............ 🧩 (noted; harmless)
```

---

## FRONTEND BUILD TREE (FE) — expanded decomposition

Stack: Jinja templates + vanilla JS + `style.css` (~3.5k lines, token-based "Alpine" theme,
dark/light) + `courses.css`. The design *language* is good; the problem is **inconsistent
application** (hardcoded/off-palette colors, heavy inline styles, no spacing/type scale).
Status: ✅ done · 🔨 in progress · ⬜ not started · 🧩 cleanup.

```
FE  Frontend ....................................................... 🔨
├─ FE1  Design System ............................................. 🔨
│  ├─ FE1.1  Color tokens (palette + dark/light parity) ........... ✅ + semantic aliases added
│  ├─ FE1.2  Typography scale ..................................... ✅ added (was missing)
│  ├─ FE1.3  Spacing scale ........................................ ✅ added (was missing)
│  ├─ FE1.4  Radius / shadow / elevation .......................... ✅ (+ --shadow-lg)
│  ├─ FE1.5  Motion / transitions ................................. ✅ tokens + global transitions
│  ├─ FE1.6  Focus / a11y tokens .................................. ✅ global :focus-visible ring
│  └─ FE1.7  Utility/helper classes ............................... ⬜
├─ FE2  Components ................................................. 🔨
│  ├─ FE2.1  Buttons (primary/secondary/ghost/icon) ............... 🧩 consolidate variants
│  ├─ FE2.2  Cards (course card, stat card) ....................... ✅ exist; tokenize
│  ├─ FE2.3  Forms / inputs / selects ............................. 🧩 inline-styled in wizard
│  ├─ FE2.4  Modals / dialogs ..................................... 🧩 multiple impls
│  ├─ FE2.5  Nav / header / sidebar / hamburger ................... ✅
│  ├─ FE2.6  Chat (messages, streaming, composer, mic) ............ ✅ authored learn-chat.css (was MISSING/404)
│  ├─ FE2.7  Badges / pills / progress bars ....................... ✅
│  ├─ FE2.8  Toasts / banners / empty states ...................... 🧩 inconsistent
│  └─ FE2.9  Loading / skeletons / spinners ....................... 🧩 partial
├─ FE3  Pages (per-template polish) ............................... 🔨
│  ├─ FE3.1  home .................................................. ✅ tokenized (status banner, weights)
│  ├─ FE3.2  courses + creation wizards ........................... ✅ tokenized
│  ├─ FE3.3  learn (path + session) ............................... ✅ chatbox restyled (learn-chat.css)
│  ├─ FE3.4  review ............................................... ✅ tokenized (spacing/radius/status)
│  ├─ FE3.5  schedule ............................................. ✅ tokenized (status badges → tint bg)
│  ├─ FE3.6  quiz ................................................. ✅ tokenized
│  ├─ FE3.7  settings ............................................. ✅ tokenized (toast/dialog; cards 12px)
│  ├─ FE3.8  status ............................................... ✅ (clean)
│  └─ FE3.9  course_view / course_structure ...................... ✅ tokenized (node-state badges)
├─ FE4  Cross-cutting ............................................. 🔨
│  ├─ FE4.1  Dark-mode parity (no hardcoded colors) ............... 🔨 (migration in progress)
│  ├─ FE4.2  Responsive / mobile .................................. 🧩
│  ├─ FE4.3  Accessibility (focus, aria, contrast, keyboard) ...... 🔨 focus ring done
│  ├─ FE4.4  Loading / empty / error states ....................... 🧩
│  ├─ FE4.5  Consistency — tokens only, no inline styles .......... 🔨 (primary sweep)
│  ├─ FE4.6  Performance (CSS size / no layout thrash) ............ ⬜
│  └─ FE4.7  Icon / asset system .................................. 🧩 (emoji + inline SVG mixed)
```

---

## PHASE 3 — BUILD MANIFEST (priority rows)

P0 = correctness bug shipping wrong behavior; P1 = high-value capability/security; P2 = perf/quality; P3 = polish/cleanup.

| ID | Path | Feature / Defect | Acceptance criteria | Pri | Status | Evidence |
|---|---|---|---|---|---|---|
| B4.1 | SR/FSRS | py-fsrs imported but never used; `new_d = w4-(rating-3)*w5` sign-inverted | Route scheduling through py-fsrs `Scheduler.review_card()`; intervals match FSRS-6 spec; test vs known vectors | P0 | 🚫 | `fsrs_engine.py:59,88,116` |
| B3.3 | Tutor | Grading has no JSON-mode; on parse fail defaults to **grade 3** (wrong answers pass) | Grade via constrained JSON output; parse-fail → retry/abstain, never silent pass | P0 | 🚫 | `fsm_logic.py:1936,1978` |
| B3.5 | Tutor | Mastery gate requires ≥3 distinct passed types; impossible at bloom_ceiling≤2 | `min(3, types_available_for_ceiling)`; low-mastery course can complete | P0 | 🚫 | `fsm_logic.py:929` |
| B5.5 | Data | `update_mastery` passes `course_uid=""` → INSERT OR REPLACE orphans progress | Pass real course_uid; progress stays linked; completion % correct | P0 | 🚫 | `librarian.py:1511` |
| B1.1.4 | Build | Bloom target computed 3× with inconsistent ceiling defaults (2 vs 5) | Single `compute_bloom(module_idx)` helper; one value per module | P0 | 🚫 | `course_builder.py:877,1206,1456` |
| B9.2 | Infra | compose `OLLAMA_MODEL=qwen2.5:14b` ≠ pulled `qwen3:14b` | `${OLLAMA_MODEL:-qwen3:14b}`; service requests the model that exists | P0 | 🚫 | `docker-compose.yml:51,88` |
| B11.4 | Dead | `rag/prompts.py` imports non-existent `get_architect_prompt` | Remove shim or define symbol; import never raises | P0 | 🚫 | `rag/prompts.py:17` |
| B2.1 | RAG | Embedding model loaded, never called; "RAG" is substring match | sqlite-vec + FTS5 hybrid + RRF; `/search` returns semantic hits; model invoked | P1 | 🚫 | `librarian.py:59,103` |
| B2.5 | RAG | No grounding/citations; prompts tell model to "fill gaps" | Inline citations to source passages; abstention when context insufficient | P1 | ⬜ | `prompts.py:306,478` |
| B8.2 | Safety | User/source text injected into prompts unescaped | Delimit + "treat as data" preamble; injection test suite passes | P1 | 🚫 | `prompts.py:368,290` |
| B6.3 | UI | Global FSM broadcast — all tabs/users share one session | Per-SID/session room scoping; two tabs independent | P1 | 🚫 | `app.py:163`; `fsm_logic.py` singleton |
| B9.3 | Infra | `.env` never interpolated; Flask secret random per restart | `${FLASK_SECRET_KEY}` wired; sessions survive restart | P1 | 🚫 | `docker-compose.yml`; `.env.example` |
| B5.7 | Data | No backup of helga.db / courses | `make backup` (sqlite `.backup` + courses tar); documented restore | P1 | ⬜ | (absent) |
| B6.4 | UI | Fragile free-text status-string parsing across 3 files | Structured `{type,stage,pct}` envelope; reword-safe progress | P2 | 🚫 | `session.js:690`; `courses.js:390` |
| B5.6 | Data | N+1 queries in structure/courses/stats/quiz | One `get_course_progress` dict per request; denormalized counts | P2 | 🧩 | `librarian.py:234,139,198,732` |
| B1.1.3 | Build | O(n²) difflib dedup | Hash/MinHash token-set bucketing; sub-quadratic | P2 | 🧩 | `course_builder.py:557,2341` |
| B4.3 | SR | FSRS cards created but never quizzed in Socratic loop | Due cards surface as retrieval questions; interleaving | P2 | ⬜ | `fsm_logic.py:2183` |
| B7.2 | Voice | TTS batch-only (high time-to-first-audio) | Sentence-level streaming; first audio < 300ms | P2 | ⬜ | `tts_server.py:62` |
| B10.2 | Test | No real E2E of event path | Live-server fixture; Browser→FSM→Socket.IO round-trip asserted | P2 | 🚫 | `test_full_e2e.py:27` |
| B11.* | Dead | Remove kuzu scripts, no-op stubs, dead UI, fix CLAUDE.md | Repo has no import-crashing files; docs match reality | P3 | 🧩 | many |

(Full node set in Phase 2; this table is the actionable priority slice.)

---

## RESEARCH-DRIVEN UPGRADE ROADMAP (fused with audit)

Each item ties a research recommendation to the audit node it fixes.

### Tier A — Correctness & truth-in-system (do first; mostly small)
1. **Fix FSRS** (B4.1) — use the already-imported py-fsrs `Scheduler` instead of the buggy hand-rolled formula. *Research: py-fsrs v6 / FSRS-6, 21 params, default until logs large.*
2. **Constrained JSON grading** (B3.3) — Ollama `format=<json schema>` (v0.5+) crushes the JSON-failure rate to ~0 and removes the silent grade-3 fallback. *Research Top-12 #1.*
3. **Adaptive mastery gate** (B3.5), **single Bloom helper** (B1.1.4), **fix `update_mastery`** (B5.5), **compose model name** (B9.2), **delete import-crashing dead code** (B11.4). Pure bug fixes.
4. **Fix CLAUDE.md** (B11.6) to match reality (this doc supplies the corrections).

### Tier B — Make RAG real (highest capability leverage)
5. **sqlite-vec ≥0.1.6 + FTS5 BM25 + RRF (k≈10–20)** hybrid search in the existing `helga.db` (B2.1/B2.2). *Research §2, Top-12 #3.*
6. **Swap MiniLM → BGE-M3** (dense+sparse from one model) or `nomic-embed-text-v1.5` for speed (B2.1). *Top-12 #4.*
7. **Add a reranker** — `bge-reranker-v2-m3` (or FlashRank MiniLM on CPU) over top-20 → top-5 (B2.4). *Biggest single retrieval-quality lift, Top-12 #2. Benchmark latency on M4 first.*
8. **Header-aware + recursive 512/15% chunking** of concept Markdown; prepend `Course>Module>Lesson>Concept` context line (B2.3). *Research §RAG chunking.*
9. **Inline citations + sufficient-context abstention gate** (B2.5/B8.2) — grounding-before-Socratic; deterministic score-floor abstention. *Top-12 #6, #12.*

### Tier C — Pedagogy depth
10. **LearnLM 5-principle + hard "never give the answer" system prompt + hint ladder** (B3.6). *Research §3, Top-12 #5.*
11. **pyBKT per-concept mastery tracking** to gate progression at ~90% mastery (B3.5/B3.9). *Top-12 #9.*
12. **Answer-key/verification layer** so the tutor stops over-validating wrong answers (B3.8). *Top-12 #6.*
13. **Wire FSRS due-cards into the Socratic loop** + interleaving (B4.3). *Top-12 #8.*

### Tier D — Platform / UX / Ops
14. **Per-session room scoping** for FSM state (B6.3) — prerequisite for multi-tab/user.
15. **Structured status-event envelope** (B6.4) — removes the most fragile frontend code.
16. **SSE for token streaming**, keep Socket.IO only for control channel (B6.1). *Research §6, Top-12 #11.*
17. **TTS streaming** + (optional) WhisperKit STT re-introduction for a voice loop < 800ms (B7.2/B7.3). *Research §5, Top-12 #7.*
18. **Backups** (B5.7), **`.env` wiring + secret** (B9.3), **drop host port exposure** (B9.4), **Ollama health/circuit-breaker** (B9.5), **torch build layer split** (B9.6).
19. **Real E2E + CI Docker build** (B10.2/B10.4); tests for research/tts servers (B10.3).
20. **MLX runtime migration** (research Top-12 #10) — defer; biggest effort, evaluate after Tier A–C.

---

## B13 — Multimodal model & visuals in Socratic teaching (Tasks #1 + visuals)

Default model is now **Qwen3.5-9B** (`qwen3.5:9b`, native text+vision; `qwen3.5:9b-mlx`
on Apple Silicon). Smaller/faster than Qwen3-14B, frees RAM, and — the reason for the
swap — multimodal, so visuals can become objects of Socratic inquiry.

```
B13  Multimodal & Visuals .......................................... 🔨
├─ B13.1  Model swap → Qwen3.5-9B (multimodal) ................... ✅ config unified (compose/.env/
│                                                                    deploy/main/llm_client/llm_utils)
├─ B13.2  Vision-capable LLM client (images param) .............. ✅ chat(images=…) + tests
├─ B13.3  Chat renders images + LaTeX math ..................... ✅ images + KaTeX (offline, raw-TeX fallback); ⬜ Mermaid
├─ B13.4  Hydration: diagram-as-code step (Mermaid + Socratic Q)  ⬜ (constrained output, B1)
├─ B13.5  Online image extraction + license filter + cache ...... ⬜ (research_server, B12/#10)
├─ B13.6  VLM relevance/caption/alt-text pass ................... ⬜ (now native via qwen3.5:9b)
├─ B13.7  Student image input in Learn (attach→FSM→grader) ...... ✅ wired end-to-end (live-validate vision)
├─ B13.8  Local diffusion for illustrations (pre-gen, cached) ... ⬜ (optional; not for technical diagrams)
└─ B13.9  Accessibility: alt text feeds TTS/text-only path ...... ⬜
```
Principle: visuals are objects of inquiry, not decoration. Prefer verifiable diagram-as-code
for technical content; reserve diffusion for non-factual illustration; pre-generate at
course-creation time (never in the live dialogue turn). Runtime-validated on the M4/Ollama.

---

## B14 — MCP-style tutor tool + data layer (Task #14)

Ollama-native tool-calling (MCP-aligned shape, no separate server). Failsafes/guardrails
scale to the model's capability TIER — for our default **qwen3.5:9b = tier 2**.

```
B14  Tutor tools & data ............................................ 🔨
├─ B14.1  Model capability tiers + per-tier policy ............... ✅ model_tier(), TIER_POLICY
├─ B14.2  ToolRegistry — tier+safety gating, ollama tool schema . ✅
├─ B14.3  Executor failsafes (validate/timeout/cap/no-raise) .... ✅ tested
├─ B14.4  LLMClient.chat_with_tools agentic loop ................ ✅ bounded rounds, tested
├─ B14.5  25 tools (math/stats/science/viz/cs/lang + data) ...... ✅ guarded lazy deps
├─ B14.6  Sandboxed run_python — tier-3/safety-3, OFF default ... ✅ (env opt-in; needs real sandbox in prod)
├─ B14.7  Data pulls (search/content/mastery/wikipedia) ......... ✅ injected callbacks
└─ B14.8  Wire chat_with_tools into the FSM Socratic loop ....... ✅ flag-gated (live-validate on M4)
```
qwen3.5:9b (tier 2) gets 24/25 tools (all but the code-exec tool): ≤3 tool calls/turn, ≤3
rounds, 4000-char outputs, 8s/tool. Pedagogy: tool results feed the tutor's hint/verification,
never handed to the student as the answer. 40 unit tests (tiers/gating/failsafes/tool logic/loop/FSM wiring).

B14.8 wiring: the FSM owns a lazily-built registry bound to its storage (search/content/mastery
callbacks); before grading, `_verify_answer_objectively()` optionally runs an objective tool-check
(math/units/stats/facts) and injects a `[TOOL CHECK: …]` note into the grading context. Gated by
`HELGA_ENABLE_TUTOR_TOOLS` (OFF by default), never raises, never blocks grading — default runtime
behavior is unchanged until validated on the M4. Code-exec stays disabled in the live tutor.

---

## IMPLEMENTATION PROGRESS (branch `claude/hopeful-jackson-bc8db4`)

All landed with tests green (381 passing; 1 pre-existing live-Ollama test deselected).

**Tier A — correctness/security (DONE):**
- B4.1 ✅ `fsrs_engine.py` rewritten to correct FSRS-5 equations; broken v6 import removed.
- B3.3 ✅ grading failure → grade 2 (no false-pass) **+** B3.3↑ constrained JSON via Ollama `format` schema (`GRADE_JSON_SCHEMA`).
- B5.5 ✅ `update_progress` preserves `course_uid`.
- B8.2 ✅ grading prompt-injection hardening (`sanitize_untrusted` + fenced spotlighting).
- B9.2 ✅ compose `${OLLAMA_MODEL}`; B9.3 ✅ Flask secret from env.
- B5.6 ✅ `/api/course_structure` N+1 removed.
- B6.5 ✅ voice-key unified (dropdown drives playback).
- B11.1/B11.3/B11.4 ✅ dead/crashing files deleted; B11.6 ✅ CLAUDE.md corrected.
- Verified-not-bugs (dropped): B3.5 mastery gate, B1.1.4 Bloom dup.

**Tier B — capability (STARTED):**
- B2.2 ✅ `/search` now SQLite **FTS5** (title + content, bm25) replacing substring; reindex hook on course build. (`SearchStore` in storage.py)
- B2.1 ⬜ semantic/dense retrieval (embeddings + reranker) — next.
- Model-swap tooling ✅ `tools/grading_eval.py` (+20 cases) for objective model A/B; README "Model evaluation & swapping" section.

**Still NOT_STARTED (need design/review):** B2.1 dense RAG + reranker, B6.3 per-session FSM scoping, B6.4 structured status events, SSE streaming, Tier C pedagogy (LearnLM prompt, pyBKT, answer-key verifier, FSRS↔Socratic).

### New roadmap items — Tier E: Voice & UX overhaul (tasks #5–#7)

Tracked in the session task list; queued for design/build.

- **B7.3 / Task #5 — Offline STT + voice input in Learn** 🔨 IN PROGRESS
  Re-introduce offline speech-to-text (removed in the Mac migration; system is currently text-only).
  **Engine decision (2026-06):** primary = **`nvidia/nemotron-3.5-asr-streaming-0.6b`** (cache-aware FastConformer-RNNT — true streaming, sub-100ms time-to-final, 40 language-locales) via the **MLX/CoreML Apple-Silicon port**, run as a **native host service** (MLX/ANE doesn't run in Linux containers) reached via `host.docker.internal` — **mirroring how Ollama is deployed**, not a new container. Pluggable backend keeps **faster-whisper** (CTranslate2, CPU, containerizable) as a drop-in fallback.
  Contract: `POST /api/stt` (audio → `{"text": …}`) + `/health`. Web-ui adds `STT_URL` (default `host.docker.internal:5001`) proxy. Frontend: mic capture (MediaRecorder) + push-to-talk in `learn.html`/`session.js` → web-ui proxy → `/api/stt` → feed transcript into the existing `TEXT_INPUT` event path. Add **Silero VAD + endpointing**, interim transcript, and **barge-in** (cancel in-flight TTS). Target <800ms voice loop. Update README, CLAUDE.md, deploy docs.
  Caveats: MLX/CoreML ports are **community** (FluidInference / 199-biotechnologies) — verify maturity + NVIDIA model license; **benchmark WER+latency on the real M4** before finalizing. Plumbing (proxy + frontend + service structure) is built/tested here; model inference can't run in CI.

- **B6.A / Task #6 — Learn-section UX overhaul** ⬜
  Structured status-event envelope (B6.4) replacing free-text parsing; clean chat/streaming reconciliation (retire the monkey-patch, back with a small state store); integrate the new STT voice loop + TTS streaming; live progress/mastery without 2s-poll races; accessibility (modal focus traps, sane `aria-live`, keyboard path nav); responsive path-view SVG; remove dead UI (EDIT_MESSAGE, ZIM/sudo modal, Memory Palace rail, dead socket emits). Benefits from SSE streaming + per-session scoping (B6.3).

- **B6.B / Task #7 — App-wide UX / design system** ⬜
  Learner dashboard/analytics (mastery over time, Bloom progression, FSRS retention forecast, streaks; pairs with pyBKT + an xAPI event log); shared design system/components so the three creation wizards stop diverging; consolidate the multiple socket connections + `escapeHtml` impls; finish dark mode; first-run onboarding; **global search UI** (now that FTS5 exists); transcript export / session history; mobile pass; offline/connection indicator; optional lightweight reactive layer (Alpine.js / web components) on high-churn surfaces only; consider PWA (manifest + service worker).

### Honest caveats from research
- Several research sources carried forward-dated (2026) datelines — re-verify specific model leaderboard claims against live model cards before adopting.
- **Memory Palace** has a weak evidence base (small effect, high bias) — keep as opt-in for concrete material, not a core retention engine.
- Vector-store *choice* matters far less than chunking/embeddings/reranking/prompting — prioritize Tier B items 5–9 over any store migration.
- Reranker latencies in sources are GPU/unspecified — **benchmark on the actual M4 Pro** before committing.

---

# TARGET DESIGN — K-12 PLATFORM EXPANSION (B15–B27, FE5–FE8)

Same status legend: ✅ done · ✔️ tested · 🧩 stub/cleanup · ⬜ not-started · 🚫 broken · 🔨 in-progress.
These branches extend the baseline tree above. Actionable checklist with acceptance criteria,
target files, priorities and releases: `docs/BUILD_MANIFEST.md`. Curriculum map:
`docs/UTAH_K12_CURRICULUM_REFERENCE.md`.

## PHASE 4 — PLATFORM FEATURE TREE (target)

```
├─ B15 Accounts, Identity & Multi-Tenancy .......................... ✔️  (Workstream A — COMPLETE)
│  ├─ B15.1  Tenancy schema (parents/students/enrollments/consent/subs) ✔️  storage.py v4 + 4 new stores
│  ├─ B15.2  student_id on all per-user tables + backfill legacy ... ✔️  PK rebuild; zero-loss migration test
│  ├─ B15.3  StorageManager sub-store student_id scoping ........... ✔️  kwarg default stu_legacy0 (R1 cutover)
│  ├─ B15.4  Auth (parent pw argon2 / student PIN, roles) .......... ✔️  auth.py + routes + pages
│  ├─ B15.5  Socket.IO room scoping (fixes B6.3 broadcast) ......... ✔️  rooms + fail-closed drops
│  ├─ B15.6  Per-student FSM registry (kills global singleton) ..... ✔️  fsm_registry.py LRU+sweeper+locks
│  ├─ B15.7  Per-student FSM persistence (fsm_sessions row) ........ ✔️  row upsert; legacy JSON import
│  └─ B15.8  Isolation test suite (A can't read/write B) ........... ✔️  all 9 spec-03 §9 rows
├─ B16 Curriculum Catalog & Standards .............................. 🔨  (Workstream B)
│  ├─ B16.1  standards + concept_standards tables (Utah codes) ..... ✔️  v5 + StandardsStore + loader
│  ├─ B16.2  Read-only catalog store (data/catalog/, catalog flag) . ✔️  published-only student view
│  ├─ B16.3  Standards-driven batch build pipeline ................. ⬜  reuses Skeleton/Auditor/Hydrator
│  ├─ B16.4  Phase-1 subjects published (K-8 Math, K-12 ELA, GFL, USG) ⬜
│  ├─ B16.5  Phase-2 subjects (SEEd, Social Studies, CS) ........... ⬜
│  ├─ B16.6  Phase-3 subjects (World Lang, Health, Lib/Digital) .... ⬜
│  └─ B16.7  ★ baseline/enrichment toggle ......................... ⬜
├─ B17 Grade-Level (K-12) Adaptation & Kid-First Tutoring .......... 🔨  (Workstreams C+D)
│  ├─ B17.1  grade_band on students + catalog courses .............. ✔️  FSM resolves from students row
│  ├─ B17.2  Grade-aware prompts (vocab/length/register) ........... ✔️  GRADE_BAND_PROFILES; 18 tests
│  ├─ B17.3  Grade-bounded Bloom/mastery defaults .................. ✔️  band clamps + banded gate
│  ├─ B17.4  Grade-banded hint ladder + micro-lectures ............. ⬜  get_hint_prompt/get_micro_lecture_prompt
│  ├─ B17.5  Manipulatives/visual answer modes (early math) ........ ⬜
│  ├─ B17.6  Voice-first early-literacy/WL loop .................... ⬜  reuses STT/TTS (B7.3)
│  └─ B17.7  Affect/frustration handling for young learners ........ ⬜  extends _detect_ignorance
├─ B18 Assessment, Exams & Interest-Themed Engagement .............. ⬜  (Workstream G)
│  ├─ B18.1  Formal exam/assessment generator ..................... ⬜  (baseline has quiz+grading, no exams)
│  ├─ B18.2  Per-standard mastery checkpoints (gate progression) ... ⬜
│  ├─ B18.3  Interests-into-exams themer (standard fixed, theme varies) ⬜  uses students.interests
│  ├─ B18.4  Item validity check (still tests target standard) ..... ⬜
│  └─ B18.5  GFL (74% cut) + Basic Civics (35/50) practice modes ... ⬜
├─ B19 Parent / Guardian Dashboard ................................. ⬜  (Workstream E)
│  ├─ B19.1  Children overview ..................................... ⬜
│  ├─ B19.2  Per-child progress + standards coverage .............. ⬜  from concept_standards/activity_log
│  ├─ B19.3  Elective approval workflow (pending_approval) ......... ⬜  gates custom wizard
│  ├─ B19.4  Add/manage students (seat-capped) ..................... ⬜
│  ├─ B19.5  Exportable progress/standards PDF report ............. ⬜  homeschool records
│  └─ B19.6  Account/consent/data export+delete ................... ⬜  ties B21
├─ B20 Billing & Subscriptions (Stripe) ........................... ⬜  (Workstream E5)
│  ├─ B20.1  Stripe Checkout + customer portal .................... ⬜
│  ├─ B20.2  Webhook → subscriptions mirror ....................... ⬜
│  ├─ B20.3  Seat enforcement (seats vs student count) ............ ⬜
│  └─ B20.4  Grant/invoice-friendly receipts ...................... ⬜  Utah Fits All expense docs
├─ B21 Compliance, Privacy & Safety ............................... ⬜  (Workstream F)
│  ├─ B21.1  COPPA verifiable parental consent + data minimization  ⬜  consent_records
│  ├─ B21.2  FERPA / Utah Student Data Protection Act handling ..... ⬜  access/export/delete, retention
│  ├─ B21.3  Self-hosted inference (no minor data to 3rd-party LLM)  ⬜  compliance advantage (locks AI-engine choice)
│  ├─ B21.4  Health Strand 6 consent gating (abstinence framing) ... ⬜  Utah Code
│  ├─ B21.5  Minor-safe tutoring guardrails (profanity/safety) ..... ⬜  extends B8
│  └─ B21.6  ToS/Privacy Policy + Utah Fits All eligibility posture  ⬜
├─ B22 Gamification 2.0 ............................................ ⬜  (Workstream H; baseline XP/streak skeletal)
│  ├─ B22.1  Wire existing XP everywhere, per-student .............. ⬜  librarian.py gamification
│  ├─ B22.2  Skill-tree map (strands=branches, standards=nodes) .... ⬜  FE8
│  ├─ B22.3  Badges per standard/strand; quests/daily challenges ... ⬜
│  ├─ B22.4  Interest-themed cosmetic rewards (avatars) ............ ⬜
│  ├─ B22.5  Grade-appropriate framing + on/off toggle ............ ⬜
│  └─ B22.6  Safe (within-family/anonymized, no open leaderboards) . ⬜  COPPA
├─ B23 Production Scaling & Deployment ............................. 🔨  (Workstream J)
│  ├─ B23.1  GPU semaphore + per-student fair queue ............... ✔️  gpu_gate.py; RR fairness; 12 tests
│  ├─ B23.2  Interactive vs background priority classes ........... ✔️  bg≤1 slot, never starves live
│  ├─ B23.3  Ollama tuning (KEEP_ALIVE=-1, MAX_LOADED_MODELS=1) .... ✔️  .env.example + compose
│  ├─ B23.4  SQLite→Postgres (psycopg pool behind sub-stores) ..... ⬜  deferred trigger: write contention
│  ├─ B23.5  Multi-worker (Redis sessions + Socket.IO MQ + stateless FSM) ⬜  deferred
│  ├─ B23.6  Caddy/TLS + gunicorn-gevent topology ................. ⬜
│  └─ B23.7  Backups/restore drill + secrets management ........... ⬜  extends B5.7/B9.3
├─ B24 Notifications & Communications .............................. ⬜  (Workstream L)
│  ├─ B24.1  Transactional email + queue .......................... ⬜
│  ├─ B24.2  Weekly parent progress digest ....................... ⬜
│  ├─ B24.3  In-app notifications ................................ ⬜
│  └─ B24.4  Struggle/inactivity alerts .......................... ⬜
├─ B25 Accessibility & Differentiation ............................. 🔨  (Workstream K; extends FE4.3/B13.9)
│  ├─ B25.1  WCAG 2.1 AA pass (keyboard/aria-live/contrast) ....... 🔨  (focus ring already done)
│  ├─ B25.2  Reading/dyslexia supports (read-aloud, fonts) ........ ⬜
│  ├─ B25.3  ELL simplified-language + glossing .................. ⬜
│  ├─ B25.4  IEP/504 accommodation flags honored by FSM/exams ..... ⬜
│  └─ B25.5  Alt-text/captions → TTS/text-only ................... ⬜
├─ B26 Content Authoring & Review CMS .............................. ⬜  (Workstream M; legal+quality gate)
│  ├─ B26.1  Offline authoring job runner ......................... ⬜
│  ├─ B26.2  Admin review console (draft→reviewed→published) ...... ⬜
│  ├─ B26.3  Catalog versioning + changelog ...................... ⬜
│  ├─ B26.4  Standards-coverage audit report ..................... ⬜
│  └─ B26.5  Hydration provenance log ............................ ⬜
└─ B27 Observability, Analytics & Unit Economics ................... ⬜  (Workstream N; expands B9.7)
   ├─ B27.1  Structured JSON logging + correlation ............... ⬜
   ├─ B27.2  Prometheus metrics (GPU/latency/sessions) ........... ⬜
   ├─ B27.3  xAPI learning-analytics event log .................. ⬜
   ├─ B27.4  Cost / tokens-per-student tracking ................. ⬜
   └─ B27.5  Ollama circuit-breaker + alerting (closes B9.5) ..... ⬜
```

## FRONTEND BUILD TREE (target) — FE5–FE8

```
├─ FE5  Student app shell (kid-first tab restructure) ............. ⬜  (replaces 8-tab single-user nav)
│  ├─ FE5.1  Today (next lesson + due + daily quest) ............. ⬜  merges Home + Schedule-due
│  ├─ FE5.2  Learn (skill-tree + Socratic session) .............. ⬜  keeps learn.html, adds tree
│  ├─ FE5.3  Practice (merge Quiz + Review) ..................... ⬜
│  ├─ FE5.4  My Stuff (interests, gamification, avatar) ......... ⬜
│  └─ FE5.5  Remove Status/heavy Settings from student view ..... ⬜  (Status → admin/ops only)
├─ FE6  Parent dashboard surface .................................. ⬜  (B19, role-gated)
├─ FE7  Auth & onboarding flows ................................... ✔️  signup/login/picker/PIN grid
└─ FE8  Skill-tree map view (gamified catalog) ................... ⬜  (B22.2)
```

## RESEARCH/PRODUCT ROADMAP — Tiers F–J (continues baseline Tiers A–E)

Mapped to releases R0 Foundation · R1 Multi-student MVP · R2 Curriculum+Parents · R3 Engagement+Billing · R4 Scale-out.

### Tier F — Multi-tenant foundation (R0–R1)
B15 (schema → auth → FSM registry), B17.1-3 grade adaptation, B23.1-3 GPU fair queue, and the
baseline **P0 engine bug fixes** (B4.1 FSRS, B3.3 grading JSON-mode, B5.5 update_mastery, B9.2/B9.3).
*Outcome: isolated, grade-appropriate, GPU-fair multi-student tutoring on one server.*

### Tier G — Curriculum & pedagogy (R2)
B16 catalog + standards, B26 authoring/review CMS, B17.4-7 + B18 kid tooling + exams + interests,
B25 accessibility. *Outcome: kids learn published Utah-standards courses.*

### Tier H — Parents, commerce & compliance (R2–R3)
B19 parent dashboard, B20 Stripe billing, B21 compliance, B24 notifications.
*Outcome: paying families, legal posture, parent control.*

### Tier I — Engagement & UX (R3)
B22 gamification, FE5–FE8 tab restructure + skill tree, B17.5/D2-D4 multimodal/voice tools.
*Outcome: retention.*

### Tier J — Scale-out (R4)
B16.5-6 remaining subjects, B23.4-7 Postgres / multi-worker / Caddy-gunicorn topology / backups,
B27 observability + unit economics. *Outcome: many concurrent families across all subjects.*

### Sequencing note
R0/R1 (Tier F) is load-bearing — **B15 multi-tenancy is the prerequisite for every other target
branch** (parent dashboard, billing, per-student catalog progress, scaling all assume it). Build the
storage abstraction (B15.1-3) and registry boundary (B15.6) deliberately so later Postgres (B23.4)
and multi-worker (B23.5) are connection-string/locality swaps, not rewrites.
