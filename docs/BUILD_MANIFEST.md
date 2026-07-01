# Helga — Build Manifest: K-12 Socratic Curriculum Platform

> Master checklist for the transformation of Helga from a single-user appliance into a
> multi-tenant, grade-adaptive, Utah-standards-aligned tutoring platform. Companion to
> `docs/HELGA_BUILD_TREE.md` (feature tree + roadmap) and the approved engineering plan.
> Curriculum source-of-truth: `docs/UTAH_K12_CURRICULUM_REFERENCE.md`.

**Status legend:** `todo` · `in_progress` · `done` · `blocked`
**Priority:** P0 = foundation-blocking (nothing ships without it) · P1 = MVP feature · P2 = monetization/quality · P3 = scale/polish
**Release:** R0 Foundation · R1 Multi-student MVP · R2 Curriculum + Parents · R3 Engagement + Billing · R4 Scale-out

> **Detailed design specs are complete** under `docs/design/` (index: `docs/design/00_INDEX.md`). Every
> B-code branch below has an implementation-ready spec — exact schemas, API contracts, state machines,
> algorithms, parameter tables. The schema source-of-truth is `docs/design/01_DATA_MODEL.md`. Spec map:
> B15→03 · B16/B26→04 · B17→02 · B18→05 · B19→06 · B20→09 · B21→08 · B22→07 · B23/B27→10 · I/FE5-8/B24→11.

---

## Phase 0 — Documentation & foundation (this session + immediate next)

| # | Item | Target | Status |
|---|------|--------|--------|
| 0.1 | Save Utah K-12 curriculum research report | `docs/UTAH_K12_CURRICULUM_REFERENCE.md` | done |
| 0.2 | Rewrite build tree for new target design | `docs/HELGA_BUILD_TREE.md` | done |
| 0.3 | Write this build manifest | `docs/BUILD_MANIFEST.md` | done |
| 0.4 | Work on branch `claude/edu-platform-scaling-design-syuzr3` | git | done |
| 0.5 | Write detailed design specs (all branches, implementation-ready) | `docs/design/00–11` | done |
| 0.6 | Save curriculum + gamification research reports | `docs/UTAH_K12_CURRICULUM_REFERENCE.md`, `docs/GAMIFICATION_RESEARCH.md` | done |
| 0.7 | Fix P0 engine bugs (FSRS sign, grading JSON-mode, mastery gate low-Bloom, `update_mastery` course_uid, `.env`/secret) | `fsrs_engine.py`, `fsm_logic.py`, `storage.py`, `docker-compose.yml` | done — landed pre-session via Tier A (branch `hopeful-jackson`, in main since `a8bba62`); verified 2026-07-01: FSRS-5 direct impl, `GRADE_JSON_SCHEMA` constrained grading, `course_uid` preserved, `${OLLAMA_MODEL}`/`FLASK_SECRET_KEY` wired; suite green |

---

## B15 — Accounts, Identity & Multi-Tenancy (Workstream A) · P0 · R0–R1

| ID | Item | Target files | Acceptance | Pri | Rel | Status |
|---|------|------|------|---|---|---|
| B15.1 | Tenancy tables (parents, students, enrollments, consent_records, subscriptions) | `services/common/storage.py` (v3→v4 migration) | v4 migration creates tables; idempotent | P0 | R0 | done — v4 migration + `fsm_sessions`; new AccountStore/EnrollmentStore/ConsentStore/FsmSessionStore; `pytest tests/core/test_multitenancy_storage.py → 23 passed` |
| B15.2 | `student_id` on all per-user tables + legacy backfill; composite PK rebuild on `user_progress` | `storage.py` | existing rows backfilled to `legacy-default`; no data loss; composite indexes added | P0 | R0 | done — backfill to `stu_legacy0`, PK `(student_id, concept_uid)`, v3-fixture migration test proves zero row loss |
| B15.3 | StorageManager sub-store `student_id` scoping + `_VALID_COLUMNS` whitelist update | `storage.py` | every per-user query filters `student_id`; unit-tested | P0 | R0 | done — Progress/Flashcard/Activity/Schedule stores scoped; **note:** `student_id` is a trailing kwarg defaulting to `stu_legacy0` (not leading positional) — same isolation guarantee, zero R0 call-site breakage; removing the default is the R0→R1 cutover (spec 03 §1.2); full suite 603 passed |
| B15.4 | Flask-Login auth: parent email/pw (argon2), student profile/PIN, role gating | `services/web-ui/app.py` | parent & student login; role gates routes | P0 | R1 | todo |
| B15.5 | Socket.IO room scoping (fix B6.3 broadcast) | `app.py:172,245,582`; `fsm_logic.py` send_status_update | two-session test shows no cross-student leakage | P0 | R1 | todo |
| B15.6 | Per-student FSM registry (kill singleton) | new `services/core/fsm_registry.py`; `fsm_logic.py:3498` | N concurrent students hold isolated FSM state | P0 | R1 | done — LRU registry (cap/TTL/flush-on-evict), single sweeper replaces per-FSM threads, per-student RLocks, all routes resolve `registry.get(sid)`; `pytest tests/core/test_fsm_registry.py → 9 passed` |
| B15.7 | Per-student FSM persistence (`fsm_sessions` row replaces `user_state.json`) | `fsm_logic.py:237,1415,1462`; `storage.py` | restart restores each student's position | P0 | R1 | done — save/load/delete re-pointed to `fsm_sessions` row upsert (atomic in WAL); one-time legacy `user_state.json` import; restart-restore + eviction-lossless tests pass; suite 615 passed |
| B15.8 | Isolation test suite | `tests/` | student A cannot read/write student B (progress/flashcards/FSM) | P0 | R1 | todo |

## B16 — Curriculum Catalog & Standards (Workstream B) · P1/P3 · R2/R4

| ID | Item | Target | Acceptance | Pri | Rel | Status |
|---|------|------|------|---|---|---|
| B16.1 | `standards` + `concept_standards` tables (Utah strand codes) | `storage.py` | codes from reference doc loadable; join works | P1 | R2 | todo |
| B16.2 | Read-only catalog store (`data/catalog/`, `catalog`+`version` flags) | `storage.py`, `librarian.py` | catalog separate from user courses; read-only to students | P1 | R2 | todo |
| B16.3 | Standards-driven batch build pipeline (reuse Skeleton/Auditor/Hydrator) | `services/core/course_builder.py`, CMS runner | builds a course from a standards spec offline | P1 | R2 | todo |
| B16.4 | Phase-1 subjects published (K-8 Math, K-12 ELA, GFL, US Gov) | catalog | each published concept tags ≥1 Utah code; human-reviewed | P1 | R2 | todo |
| B16.5 | Phase-2 subjects (SEEd K-8 + 4 HS sciences, Social Studies, CS theory) | catalog | published + reviewed | P3 | R4 | todo |
| B16.6 | Phase-3 subjects (World Lang, Health, Library/Digital Literacy) | catalog | published + reviewed | P3 | R4 | todo |
| B16.7 | ★ baseline/enrichment toggle | catalog + UI | parent can choose "core only" vs "core+enrichment" | P3 | R4 | todo |

## B17 — Grade-Level (K-12) Adaptation & Kid-First Tutoring (Workstreams C+D) · P1 · R1

| ID | Item | Target | Acceptance | Pri | Rel | Status |
|---|------|------|------|---|---|---|
| B17.1 | `grade_band` on students + catalog courses | `storage.py` | value flows into FSM + prompts | P1 | R1 | done — `students.grade_band` (v4) resolved in `MnemosyneFSM.__init__`, persisted in session blob, flows into prompts + Bloom bounds |
| B17.2 | Grade-aware prompts (vocab/length/register/ideas-per-turn) | `services/common/prompts.py:289-329` | distinct output K-2 vs 9-12 (snapshot test) | P1 | R1 | done — `GRADE_BAND_PROFILES` (spec 02 §3 verbatim); persona/register/word-caps/markdown-emoji gating in tutor prompt; K-2/9-12 grading calibration; `pytest tests/core/test_grade_bands.py → 18 passed` |
| B17.3 | Grade-bounded Bloom/mastery defaults | `fsm_logic.py`, `course_builder.py` | reuses `progressive_bloom`/`_check_mastery_gate` with grade bounds | P1 | R1 | done — band clamps course Bloom floor/ceiling; mastery gate thresholds (streak/questions/types) band-parameterized; low-ceiling completion regression test (B3.5) |
| B17.4 | Grade-banded hint ladder + micro-lectures | `prompts.py` (`get_hint_prompt`, `get_micro_lecture_prompt`) | more scaffolding for younger; faster fade older | P1 | R2 | todo |
| B17.5 | Manipulatives / visual answer modes (early math) | learn UI + FSM | K-2 can answer without typing abstractions; grades feed mastery | P2 | R3 | todo |
| B17.6 | Voice-first early-literacy / World-Lang loop | STT/TTS (B7.3) | read-aloud + pronunciation works | P2 | R3 | todo |
| B17.7 | Affect/frustration handling for young learners | `fsm_logic.py` (`_detect_ignorance`) | repeated-miss → encouragement+scaffold, not pressing | P2 | R3 | todo |

## B18 — Assessment, Exams & Interest-Themed Engagement (Workstream G) · P1 · R2

| ID | Item | Target | Acceptance | Pri | Rel | Status |
|---|------|------|------|---|---|---|
| B18.1 | Formal exam/assessment generator | new exam module | produces multi-item exams (system had none) | P1 | R2 | todo |
| B18.2 | Per-standard mastery checkpoints gate progression | exam module + FSM | unit/module checkpoints block advance until passed | P1 | R2 | todo |
| B18.3 | Interests-into-exams themer (standard fixed, theme varies) | exam module; `students.interests` | soccer-themed ratio item still tests the ratio standard | P1 | R2 | todo |
| B18.4 | Item validity check (still tests target standard) | exam module | automated check: difficulty/standard unchanged | P1 | R2 | todo |
| B18.5 | GFL (74% cut) + Basic Civics (35/50) practice modes | exam module | scored modes match Utah thresholds | P2 | R2 | todo |

## B19 — Parent / Guardian Dashboard (Workstream E) · P1 · R2

| ID | Item | Target | Acceptance | Pri | Rel | Status |
|---|------|------|------|---|---|---|
| B19.1 | Children overview | web-ui parent pages | grade/courses/mastery/streak/time per child | P1 | R2 | todo |
| B19.2 | Per-child progress + standards coverage | `concept_standards`, `activity_log` | shows Utah codes mastered/in-progress + timeline | P1 | R2 | todo |
| B19.3 | Elective approval workflow (`pending_approval`) | parent + custom wizard | child elective needs parent approve before start | P1 | R2 | todo |
| B19.4 | Add/manage students (seat-capped) | parent + subscriptions | cannot exceed `subscriptions.seats` | P1 | R2 | todo |
| B19.5 | Exportable progress/standards PDF report | parent | downloadable record for homeschool/grant | P1 | R2 | todo |
| B19.6 | Account/consent/data export+delete | parent + consent | ties B21; export/delete works | P1 | R2 | todo |

## B20 — Billing & Subscriptions (Stripe) (Workstream E5) · P2 · R3

| ID | Item | Acceptance | Pri | Rel | Status |
|---|------|------|---|---|---|
| B20.1 | Stripe Checkout + customer portal | test-mode checkout completes | P2 | R3 | todo |
| B20.2 | Webhook → `subscriptions` mirror | webhook updates local status | P2 | R3 | todo |
| B20.3 | Seat enforcement (seats vs student count) | add-student blocked over seats | P2 | R3 | todo |
| B20.4 | Grant/invoice-friendly receipts | Utah Fits All expense docs generated | P2 | R3 | todo |

## B21 — Compliance, Privacy & Safety (Workstream F) · P1 · R2

| ID | Item | Acceptance | Pri | Rel | Status |
|---|------|------|---|---|---|
| B21.1 | COPPA verifiable parental consent + data minimization | consent captured before child use (`consent_records`) | P1 | R2 | todo |
| B21.2 | FERPA / Utah Student Data Protection Act handling | access/export/delete + retention policy | P1 | R2 | todo |
| B21.3 | Self-hosted inference (no minor data to 3rd-party LLM) | inference stays on our GPU; documented | P1 | R2 | todo |
| B21.4 | Health Strand 6 consent gating (abstinence framing) | gated by `consent_records`; Utah-Code framing | P1 | R2 | todo |
| B21.5 | Minor-safe tutoring guardrails (profanity/safety) | safety filter on free-text for minors (extends B8) | P1 | R2 | todo |
| B21.6 | ToS/Privacy Policy + Utah Fits All eligibility posture | published; provider-eligibility documented | P2 | R2 | todo |

## B22 — Gamification 2.0 (Workstream H) · P2 · R3

| ID | Item | Acceptance | Pri | Rel | Status |
|---|------|------|---|---|---|
| B22.1 | Wire existing XP everywhere, per-student | XP fires on review+session per student | P2 | R3 | todo |
| B22.2 | Skill-tree map (strands=branches, standards=nodes) | catalog renders as visual tree (FE8) | P2 | R3 | todo |
| B22.3 | Badges per standard/strand; quests/daily challenges | unlocks beyond streaks | P2 | R3 | todo |
| B22.4 | Interest-themed cosmetic rewards (avatars) | unlockable cosmetics | P2 | R3 | todo |
| B22.5 | Grade-appropriate framing + on/off toggle | playful K-5, subtle teens; toggle respected | P2 | R3 | todo |
| B22.6 | Safe (within-family/anonymized, no open leaderboards) | no cross-family identity exposure | P2 | R3 | todo |

## B23 — Production Scaling & Deployment (Workstream J) · P1/P3 · R1/R4

| ID | Item | Target | Acceptance | Pri | Rel | Status |
|---|------|------|------|---|---|---|
| B23.1 | GPU semaphore + per-student fair queue | `services/core/llm_client.py` (`chat()`/`get_llm_client()`) | M students, no 60s timeouts, bounded p95 | P1 | R1 | done — `gpu_gate.py` admission gate wraps all LLM entry points; RR fairness across student_ids; busy backpressure + overload shedding; `pytest tests/core/test_gpu_gate.py → 12 passed` |
| B23.2 | Interactive vs background priority classes | `llm_client.py` | background build never starves live tutoring | P1 | R1 | done — bg ≤ 1 slot, never granted while interactive waits (tested); llm_utils build pipelines default BACKGROUND, FSM turns INTERACTIVE |
| B23.3 | Ollama tuning (`KEEP_ALIVE=-1`, `MAX_LOADED_MODELS=1`) | host env | model stays warm across students | P1 | R1 | done — documented in `.env.example` + compose (`OLLAMA_NUM_PARALLEL` shared by gate cap); host launchctl instructions |
| B23.4 | SQLite→Postgres (psycopg pool behind sub-stores) | `storage.py` | ETL preserves `student_id`; same interface | P3 | R4 | todo |
| B23.5 | Multi-worker (Redis sessions + Socket.IO MQ + stateless FSM) | `app.py`, `fsm_registry.py` | >1 worker serves any student | P3 | R4 | todo |
| B23.6 | Caddy/TLS + gunicorn-gevent topology | infra/compose | HTTPS; WS upgrade; gevent worker | P3 | R4 | todo |
| B23.7 | Backups/restore drill + secrets management | infra | nightly backup + tested restore; secrets persisted | P3 | R4 | todo |

## B24 — Notifications & Communications (Workstream L) · P2 · R3

| ID | Item | Acceptance | Pri | Rel | Status |
|---|------|------|---|---|---|
| B24.1 | Transactional email + queue | verification/reset/receipt emails send | P2 | R3 | todo |
| B24.2 | Weekly parent progress digest | per-child mastery/time/struggles email | P2 | R3 | todo |
| B24.3 | In-app notifications | elective requests / due / streak | P2 | R3 | todo |
| B24.4 | Struggle/inactivity alerts | parent alerted on struggle/inactivity | P2 | R3 | todo |

## B25 — Accessibility & Differentiation (Workstream K) · P2 · R3 (B25.1 in_progress per baseline FE4.3)

| ID | Item | Acceptance | Pri | Rel | Status |
|---|------|------|---|---|---|
| B25.1 | WCAG 2.1 AA pass (keyboard/aria-live/contrast) | audit passes | P2 | R3 | in_progress |
| B25.2 | Reading/dyslexia supports (read-aloud, fonts) | TTS everywhere + font options | P2 | R3 | todo |
| B25.3 | ELL simplified-language + glossing | toggle works | P2 | R3 | todo |
| B25.4 | IEP/504 accommodation flags honored by FSM/exams | extended-time/no-timer/etc. applied | P2 | R3 | todo |
| B25.5 | Alt-text/captions → TTS/text-only | media accessible (extends B13.9) | P2 | R3 | todo |

## B26 — Content Authoring & Review CMS (Workstream M) · P2 · R2–R3

| ID | Item | Acceptance | Pri | Rel | Status |
|---|------|------|---|---|---|
| B26.1 | Offline authoring job runner | batch builds catalog from standards map | P2 | R2 | todo |
| B26.2 | Admin review console (draft→reviewed→published) | only published visible to students | P2 | R2 | todo |
| B26.3 | Catalog versioning + changelog | students pinned to a version | P2 | R3 | todo |
| B26.4 | Standards-coverage audit report | shows published vs gaps per Utah code | P2 | R3 | todo |
| B26.5 | Hydration provenance log | sources recorded (supports F1) | P2 | R3 | todo |

## B27 — Observability, Analytics & Unit Economics (Workstream N) · P3 · R4

| ID | Item | Acceptance | Pri | Rel | Status |
|---|------|------|---|---|---|
| B27.1 | Structured JSON logging + correlation | logs carry `student_id`/request id | P3 | R4 | todo |
| B27.2 | Prometheus metrics (GPU/latency/sessions) | metrics scrapeable | P3 | R4 | todo |
| B27.3 | xAPI learning-analytics event log | events feed dashboard/analytics | P3 | R4 | todo |
| B27.4 | Cost / tokens-per-student tracking | per-student token/GPU-sec reported | P3 | R4 | todo |
| B27.5 | Ollama circuit-breaker + alerting (closes B9.5) | graceful degradation + alert | P3 | R4 | todo |

---

## Frontend (FE5–FE8)

| ID | Item | Acceptance | Rel | Status |
|---|------|------|---|---|
| FE5.1 | Today (next lesson + due + daily quest) — merges Home + Schedule-due | single "what to do now" surface | R3 | todo |
| FE5.2 | Learn (skill-tree + Socratic session) | keeps `learn.html`, adds tree | R3 | todo |
| FE5.3 | Practice (merge Quiz + Review) | exams + flashcards in one tab | R3 | todo |
| FE5.4 | My Stuff (interests, gamification, avatar) | lightweight kid settings | R3 | todo |
| FE5.5 | Remove Status/heavy Settings from student view | student nav simplified | R3 | todo |
| FE6 | Parent dashboard surface | role-gated parent UI (B19) | R2 | todo |
| FE7 | Auth & onboarding flows | parent signup, student PIN, consent capture | R1 | todo |
| FE8 | Skill-tree map view (gamified catalog) | renders catalog as tree (B22.2) | R3 | todo |

---

## Release gates (summary)

- **R0 Foundation:** B15.1–B15.3 + Phase 0 docs + P0 bug fixes. App still runs single-user on `legacy-default`.
- **R1 Multi-student MVP:** B15.4–B15.8, B17.1–B17.3, B23.1–B23.3, FE7. N isolated, grade-appropriate students on 1 GPU.
- **R2 Curriculum + Parents:** B16.1–B16.4, B26.1–B26.2, B17.4 + B18, B19, B21, FE6. Kids learn published Utah-standards courses; parents manage them.
- **R3 Engagement + Billing:** B22, B20, FE5/FE8, B24, B25, B17.5–B17.7. Paying families, polished kid UX.
- **R4 Scale-out:** B16.5–B16.6, B23.4–B23.7, B27. Many concurrent families across all subjects.
