# Helga K-12 Platform — Design Spec Index

> **Status: design complete, ready for implementation.** These specs take the target design in
> `docs/HELGA_BUILD_TREE.md` (branches B15–B27, FE5–FE8) and `docs/BUILD_MANIFEST.md` from
> feature-level decomposition to implementation-ready detail: exact schemas, API contracts, state
> machines, algorithms, and parameter tables. An engineer (or coding agent) can pick up any branch
> and build it from its spec.

## Reading order
Read **01** and **02** first — they are the shared foundation every other spec references.

| # | Spec | Branches | Owns |
|---|------|----------|------|
| 00 | `00_INDEX.md` | — | This index + cross-spec map + glossary |
| 01 | `01_DATA_MODEL.md` | all | **Canonical schema** — every table/column name; migrations v4–v9; FSM session blob; Postgres portability |
| 02 | `02_GRADE_ADAPTATION.md` | B17.1-3 | Per-band (K-2/3-5/6-8/9-12) parameter table; `GRADE_BAND_PROFILES`; `prompts.py` insertion points; grade-bounded Bloom/mastery (fixes B3.5) |
| 03 | `03_MULTITENANCY_AUTH_FSM.md` | B15.4-8 | Flask-Login parent/student+PIN roles; Socket.IO room scoping; per-student FSM registry |
| 04 | `04_CATALOG_AND_STANDARDS.md` | B16, B26 | Standards ingestion; catalog course schema; offline authoring pipeline; review CMS; versioning; coverage audit; provenance |
| 05 | `05_ASSESSMENT_ENGINE.md` | B18, G | Exam taxonomy; per-attempt item generation; interest theming + validity guard; progression gating; scoring |
| 06 | `06_PARENT_DASHBOARD.md` | B19, FE6 | Parent shell; children overview; standards coverage; elective-approval state machine; reports; data rights |
| 07 | `07_GAMIFICATION.md` | B22, FE8 | SDT/flow philosophy + guardrails; server-authoritative XP; badges; skill tree; age fade-out; measurement kill-thresholds |
| 08 | `08_COMPLIANCE_PRIVACY_SAFETY.md` | B21 | COPPA consent gate; FERPA/Utah data rights; Health Strand 6 gating; minor-safe moderation; data-handling matrix |
| 09 | `09_BILLING.md` | B20 | Seat-metered Stripe plan; Checkout/portal; webhook handlers; seat enforcement; grant invoices; entitlement gate |
| 10 | `10_DEPLOYMENT_SCALING.md` | B23, B27 | GPU fair-queue; capacity model; Postgres migration; multi-worker; topology; backups; observability |
| 11 | `11_IA_ONBOARDING_NOTIFICATIONS.md` | I, FE5-8, B24 | Tab restructure (8→4 kid tabs); role routing; onboarding flows; notifications |

## Research basis (verbatim source reports)
- `docs/UTAH_K12_CURRICULUM_REFERENCE.md` — Utah Core Standards map (curriculum source-of-truth for spec 04).
- `docs/GAMIFICATION_RESEARCH.md` — gamification evidence base (philosophy + guardrails for spec 07).

## Cross-spec dependency map
- **Everything depends on 01** (table names) and on **03/B15** (the `student_id` isolation key + auth) landing first — that's why B15 is the R0/R1 foundation.
- **02 ↔ 04/05/07/11:** grade band drives content hydration (04), exam calibration (05), gamification skinning (07), and IA presentation (11).
- **04 → 05/06/07:** the standards layer (`standards`/`concept_standards`) powers exam blueprints (05), parent standards-coverage (06), and the skill tree (07).
- **08 ↔ 06/09/05:** consent/data-rights wire into the parent dashboard (06); PCI-minimized billing (09) shares the data posture; Health Strand 6 gating touches enrollment + exams (05).
- **10 underlies all live LLM work:** the GPU fair-queue wraps **both** LLM paths — `llm_client.py` (live tutoring) and `llm_utils.py`'s `llm_generate` (course/catalog building).
- **09 ↔ 06:** subscription `seats` gate enforced at add-student in the parent dashboard.

## Glossary (canonical terms — use verbatim)
- **parent** (`par_…`) — billing owner / guardian account. **student** (`stu_…`) — a learner; `students.id` is the system-wide isolation key.
- **grade_band** — one of `K-2 | 3-5 | 6-8 | 9-12`. **catalog course** — curated, standards-tagged, `is_catalog=1`, `catalog_status='published'`. **elective** — user-generated course behind parent approval.
- **enrollment** — a student↔course link with progress + approval status. **standard_code** — official Utah strand/standard code (e.g. `6.RP`).
- **FSM registry** — per-student `MnemosyneFSM` instance pool replacing the global singleton. **student room** — `student:{id}` Socket.IO room.

## How specs map to releases (from BUILD_MANIFEST)
- **R0/R1 (foundation):** 01, 02, 03, 10 §1 (GPU queue). → isolated, grade-appropriate, GPU-fair multi-student tutoring.
- **R2 (curriculum + parents):** 04, 05, 06, 08. → kids learn published Utah-standards courses; parents manage them.
- **R3 (engagement + billing):** 07, 09, 11. → retention + paying families + kid-first UX.
- **R4 (scale-out):** 04 (remaining subjects), 10 §3-7 (Postgres/multi-worker/topology/observability).

## What is intentionally deferred (not gaps)
- Stateless hydrate-per-turn FSM ("Option A") → with multi-worker (B23.5).
- Postgres → when SQLite write contention / multi-worker bites (10 §3).
- Item bank persistence → exams generate per-attempt in v1 (05 §10).
- Co-guardian / multi-parent → single `parent_id` now (01 §10).
