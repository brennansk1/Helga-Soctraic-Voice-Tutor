# Design Spec 01 — Data Model (canonical)

> Implementation-ready schema for the K-12 platform. **This is the single source of truth for
> table/column names** — every other design spec references these names verbatim. SQLite first
> (matches `services/common/storage.py` `_ThreadLocalDB`, WAL), Postgres-portable (B23.4).
> Migrations extend the existing integer `schema_version` system at `storage.py:172-206`.

## 0. Conventions
- **UIDs:** existing `*_` + 8-hex pattern for content (`course_`, `mod_`, `con_`…). New account entities use
  prefixed UUID4-hex: `par_`, `stu_`, `sub_`, `enr_`, `cns_`, `exm_`, `att_`, `bdg_`, `qst_`, `ntf_`.
- **Timestamps:** TEXT ISO-8601 UTC (`datetime('now')` default), matching existing tables.
- **JSON columns:** TEXT holding JSON (SQLite has no native JSON type; `json_extract` available).
- **The isolation key is `students.id` (`stu_…`)**, referenced everywhere as `student_id TEXT`.
- All new SQL goes through `StorageManager` sub-stores; column names interpolated into SQL **must** be added
  to the relevant `_VALID_COLUMNS` whitelist (existing pattern, `storage.py:830,910`).
- Catalog (global, read-only to students) tables get **no** `student_id`: `courses`, `concept_fts`,
  `concept_vec`, `standards`, `concept_standards`.

## 1. Migration sequence (extends current v3)

Each block follows the existing `if current_version < N:` + `ALTER TABLE` (try/except) + `UPDATE schema_version`
idiom. Ship them as separate versions so each is independently revertible/testable.

| Ver | Adds | Spec §§ |
|---|---|---|
| v4 | Tenancy core + `student_id` on per-user tables + legacy backfill + `user_progress` PK rebuild + `fsm_sessions` | §2, §3 |
| v5 | Standards layer + catalog columns on `courses` | §4 |
| v6 | Assessment/exam tables | §5 |
| v7 | Per-student gamification tables (migrate out of librarian global K-V) | §6 |
| v8 | Accommodations, notifications, compliance audit log | §7 |
| v9 | Catalog version pinning + hydration provenance + billing idempotency (cross-spec additive) | §7b |

### Backfill rule (v4) — never lose the existing single user's data
1. Insert a synthetic parent `par_legacy0` and student `stu_legacy0` (grade_band `'9-12'`, display "Legacy Learner").
2. `UPDATE <each per-user table> SET student_id = 'stu_legacy0' WHERE student_id IS NULL`.
3. Move librarian global `user_profile`/`gamification`/`achievements` rows under `stu_legacy0`.
The app continues to run; `current_student_id()` returns `stu_legacy0` until real auth lands.

## 2. Tenancy core (v4)

```sql
CREATE TABLE IF NOT EXISTS parents (
    id            TEXT PRIMARY KEY,              -- par_<hex>
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,                 -- argon2id
    display_name  TEXT,
    status        TEXT DEFAULT 'active',         -- active | suspended | pending_verify | deleted
    email_verified_at TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS students (
    id            TEXT PRIMARY KEY,              -- stu_<hex>   (THE isolation key)
    parent_id     TEXT NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
    display_name  TEXT NOT NULL,
    pin_hash      TEXT,                          -- argon2 of 4-digit PIN (young-kid login); null = parent-launch only
    grade_band    TEXT NOT NULL DEFAULT '6-8',   -- 'K-2' | '3-5' | '6-8' | '9-12'
    grade_numeric INTEGER,                       -- 0(K)..12, optional finer tuning
    avatar_url    TEXT,
    interests     TEXT DEFAULT '[]',             -- JSON array of strings (max 20)
    settings      TEXT DEFAULT '{}',             -- JSON: tts_default, font, reduced_motion, gamification_enabled…
    status        TEXT DEFAULT 'active',         -- active | archived | deleted
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_students_parent ON students(parent_id);

CREATE TABLE IF NOT EXISTS enrollments (
    id                  TEXT PRIMARY KEY,        -- enr_<hex>
    student_id          TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_uid          TEXT NOT NULL,           -- references catalog OR user courses (no FK: cross-store)
    course_kind         TEXT NOT NULL DEFAULT 'catalog',   -- catalog | elective
    current_concept_uid TEXT,
    status              TEXT NOT NULL DEFAULT 'active',     -- active | completed | paused | pending_approval | denied
    approved_by         TEXT,                    -- parent_id when course_kind=elective
    approved_at         TEXT,
    enrolled_at         TEXT DEFAULT (datetime('now')),
    UNIQUE(student_id, course_uid)
);
CREATE INDEX IF NOT EXISTS idx_enroll_student ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enroll_status  ON enrollments(student_id, status);

CREATE TABLE IF NOT EXISTS consent_records (
    id            TEXT PRIMARY KEY,              -- cns_<hex>
    parent_id     TEXT NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
    student_id    TEXT REFERENCES students(id) ON DELETE CASCADE,  -- null for account-level (TOS)
    consent_type  TEXT NOT NULL,                 -- coppa_data | tos | privacy | health_strand6 | marketing
    granted       INTEGER NOT NULL,              -- 0/1
    policy_version TEXT NOT NULL,                -- version of the doc consented to
    method        TEXT,                          -- checkbox | signed | card_verify
    ip_address    TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_consent_parent ON consent_records(parent_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    parent_id            TEXT PRIMARY KEY REFERENCES parents(id) ON DELETE CASCADE,
    provider             TEXT DEFAULT 'stripe',
    provider_customer_id TEXT,
    provider_sub_id      TEXT,
    plan                 TEXT,                    -- e.g. family_annual
    seats                INTEGER DEFAULT 1,       -- max active students
    status               TEXT DEFAULT 'inactive', -- active | trialing | past_due | canceled | inactive
    current_period_end   TEXT,
    updated_at           TEXT DEFAULT (datetime('now'))
);

-- Per-student FSM session state (replaces the global data/user_state.json sink)
CREATE TABLE IF NOT EXISTS fsm_sessions (
    student_id  TEXT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    blob        TEXT NOT NULL,                   -- JSON: state, active_course_uid, current_lesson_node,
                                                 --       conversation_history, transcript(capped), bloom, streaks…
    updated_at  TEXT DEFAULT (datetime('now'))
);
```

### 2.1 `student_id` on existing per-user tables (v4)
`ALTER TABLE … ADD COLUMN student_id TEXT` on: `user_progress`, `activity_log`, `scheduled_reviews`,
`flashcards`. Then add indexes and backfill (§1).
```sql
CREATE INDEX IF NOT EXISTS idx_progress_student   ON user_progress(student_id, course_uid);
CREATE INDEX IF NOT EXISTS idx_activity_student    ON activity_log(student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_flashcards_student  ON flashcards(student_id, next_review_date);
CREATE INDEX IF NOT EXISTS idx_schedule_student    ON scheduled_reviews(student_id, scheduled_date);
```

### 2.2 `user_progress` composite-PK rebuild (v4)
PK is currently `concept_uid` alone (`storage.py:79`). One student per concept is wrong for multi-tenant.
SQLite can't alter a PK in place → table rebuild inside the migration transaction:
```sql
CREATE TABLE user_progress_new ( … same columns …, student_id TEXT NOT NULL,
    PRIMARY KEY (student_id, concept_uid) );
INSERT INTO user_progress_new SELECT *, 'stu_legacy0' FROM user_progress;  -- column order pinned in code
DROP TABLE user_progress;  ALTER TABLE user_progress_new RENAME TO user_progress;
-- recreate idx_progress_* indexes
```
Upserts switch from PK-on-concept to `ON CONFLICT(student_id, concept_uid)`.

## 3. FSM session blob shape (stored in `fsm_sessions.blob`)
Serialized by the existing `_save_current_course_progress`/`_load_course_progress` (`fsm_logic.py:1415,1462`),
re-pointed from JSON file to this row. Keys (subset of the ~40 FSM attrs that must persist):
`{ state, active_course_uid, current_teaching_style, grade_band, current_lesson_node, current_context,
conversation_history[≤20], transcript[≤50], concept_correct_streak, concept_question_count,
passed_question_types[], current_bloom_level, bloom_correct_streak, course_bloom_floor, course_bloom_ceiling,
concept_bloom_target, socratic_type_index, socratic_retry_count, schema:1 }`. Caps enforce PERF-5.

## 4. Standards & catalog layer (v5)

```sql
CREATE TABLE IF NOT EXISTS standards (
    code         TEXT PRIMARY KEY,               -- Utah strand/standard code, e.g. '6.RP', 'BIO.1', 'SII.A.REI'
    subject      TEXT NOT NULL,                  -- math | ela | science | social_studies | world_lang | health | cs | financial_lit | library_media
    grade_band   TEXT,                           -- 'K-2'… or course code (e.g. 'GFL','USG') for HS courses
    grade_numeric INTEGER,
    strand       TEXT NOT NULL,                  -- e.g. 'Ratios & Proportional Relationships'
    text         TEXT NOT NULL,                  -- the standard statement
    is_enrichment INTEGER DEFAULT 0,             -- 1 = ★ supplementary
    source       TEXT DEFAULT 'USBE',
    adopted_year INTEGER
);
CREATE INDEX IF NOT EXISTS idx_standards_subject ON standards(subject, grade_band);

CREATE TABLE IF NOT EXISTS concept_standards (
    concept_uid   TEXT NOT NULL,
    standard_code TEXT NOT NULL REFERENCES standards(code),
    coverage      TEXT DEFAULT 'full',           -- full | partial | enrichment
    PRIMARY KEY (concept_uid, standard_code)
);
CREATE INDEX IF NOT EXISTS idx_cs_standard ON concept_standards(standard_code);
```

### 4.1 Catalog columns on existing `courses` (v5)
`ALTER TABLE courses ADD COLUMN …`:
`subject TEXT`, `grade_band TEXT`, `grade_numeric INTEGER`, `is_catalog INTEGER DEFAULT 0`,
`catalog_status TEXT DEFAULT 'draft'` (`draft|reviewed|published|retired`), `version INTEGER DEFAULT 1`,
`visibility TEXT DEFAULT 'private'` (`private|catalog`), `reviewed_by TEXT`, `published_at TEXT`,
`enrichment_included INTEGER DEFAULT 0`.
Catalog course files live under `data/catalog/courses/{uid}/` (read-only); user electives stay in `data/courses/`.
Students only ever see `is_catalog=1 AND catalog_status='published'` courses plus their own approved electives.

## 5. Assessment / exams (v6)

```sql
CREATE TABLE IF NOT EXISTS exams (
    id           TEXT PRIMARY KEY,               -- exm_<hex>
    course_uid   TEXT,                            -- null for cross-course (e.g. Civics prep)
    scope_uid    TEXT,                            -- module/unit uid this checkpoint gates (null=summative)
    kind         TEXT NOT NULL,                  -- diagnostic | checkpoint | unit | summative | standardized_prep
    standard_codes TEXT DEFAULT '[]',            -- JSON: standards this exam asserts
    blueprint    TEXT NOT NULL,                  -- JSON: item slots {standard_code, bloom, type, count}
    pass_threshold REAL DEFAULT 0.8,             -- 0.74 GFL, 0.70 Civics(35/50), etc.
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exam_attempts (
    id           TEXT PRIMARY KEY,               -- att_<hex>
    student_id   TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    exam_id      TEXT NOT NULL REFERENCES exams(id),
    course_uid   TEXT,
    status       TEXT DEFAULT 'in_progress',     -- in_progress | submitted | graded | abandoned
    score        REAL,                           -- 0..1
    passed       INTEGER,                        -- 0/1 vs pass_threshold
    theme        TEXT,                           -- interest theme applied (audit)
    accommodations TEXT DEFAULT '{}',            -- JSON snapshot honored this attempt
    started_at   TEXT DEFAULT (datetime('now')),
    submitted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempt_student ON exam_attempts(student_id, exam_id);

CREATE TABLE IF NOT EXISTS exam_item_responses (
    id            TEXT PRIMARY KEY,
    attempt_id    TEXT NOT NULL REFERENCES exam_attempts(id) ON DELETE CASCADE,
    standard_code TEXT,
    bloom_level   INTEGER,
    item_type     TEXT,                          -- mcq | free | numeric | ordering
    prompt        TEXT,                          -- rendered (themed) item text
    correct       TEXT,                          -- answer key
    response      TEXT,                          -- student answer
    grade         INTEGER,                       -- 1-4 (reuses Socratic grader) or 0/1 for objective
    is_correct    INTEGER,
    theme_validated INTEGER DEFAULT 0            -- B18.4 validity guard passed
);
```
Items are generated per-attempt (not stored as a fixed bank in v1) so interest-theming and accommodations apply
at attempt time; `exam_item_responses` is the durable record. A future item bank is an additive table.

## 6. Per-student gamification (v7) — migrate out of librarian global K-V
The baseline keeps `gamification`/`achievements`/`user_profile` as **global** K-V in `services/rag/librarian.py:1648`.
Replace with student-scoped tables in `helga.db`:
```sql
CREATE TABLE IF NOT EXISTS student_gamification (
    student_id  TEXT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    total_xp    INTEGER DEFAULT 0,
    level       INTEGER DEFAULT 1,
    streak_days INTEGER DEFAULT 0,
    streak_last_date TEXT,
    daily_xp    INTEGER DEFAULT 0,
    daily_date  TEXT,
    cosmetics   TEXT DEFAULT '{}'                -- JSON unlocked/equipped
);
CREATE TABLE IF NOT EXISTS xp_ledger (         -- audit + anti-cheat + analytics
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    reason      TEXT,                            -- answer|complete_concept|complete_module|review|exam_pass|quest
    ref_uid     TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_xp_student ON xp_ledger(student_id, created_at);
CREATE TABLE IF NOT EXISTS badges (            -- catalog of badge definitions (global)
    id TEXT PRIMARY KEY, name TEXT, description TEXT, icon TEXT,
    criteria TEXT, xp_reward INTEGER DEFAULT 0, scope TEXT );  -- scope: standard|strand|streak|special
CREATE TABLE IF NOT EXISTS student_badges (
    student_id TEXT NOT NULL, badge_id TEXT NOT NULL, unlocked_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (student_id, badge_id) );
CREATE TABLE IF NOT EXISTS quests (            -- daily/weekly challenge definitions (global)
    id TEXT PRIMARY KEY, title TEXT, kind TEXT, target INTEGER, xp_reward INTEGER, cadence TEXT );
CREATE TABLE IF NOT EXISTS student_quests (
    student_id TEXT NOT NULL, quest_id TEXT NOT NULL, progress INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active', period_key TEXT,    -- e.g. '2026-06-30' for daily reset
    PRIMARY KEY (student_id, quest_id, period_key) );
```

## 7. Accommodations, notifications, compliance audit (v8)
```sql
CREATE TABLE IF NOT EXISTS accommodations (     -- IEP/504 flags (B25.4)
    student_id   TEXT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    extended_time INTEGER DEFAULT 0,            -- exams: no timer / 1.5x etc.
    no_timer      INTEGER DEFAULT 0,
    reduced_distraction INTEGER DEFAULT 0,      -- minimal UI / no gamification flourish
    larger_targets INTEGER DEFAULT 0,
    extra_scaffolding INTEGER DEFAULT 0,        -- FSM: deeper hint ladder, more micro-lectures
    simplified_language INTEGER DEFAULT 0,      -- ELL/reading (B25.3)
    read_aloud_default INTEGER DEFAULT 0,
    notes        TEXT,
    set_by       TEXT,                          -- parent_id
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,               -- ntf_<hex>
    recipient_id TEXT NOT NULL,                 -- parent_id or student_id
    recipient_role TEXT NOT NULL,               -- parent | student
    kind        TEXT NOT NULL,                  -- elective_request | due_review | struggle_alert | digest | system
    title       TEXT, body TEXT, ref_uid TEXT,
    read_at     TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notif_recipient ON notifications(recipient_id, read_at);
CREATE TABLE IF NOT EXISTS audit_log (          -- FERPA/Utah data-access audit (distinct from activity_log)
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    TEXT, actor_role TEXT,
    action      TEXT NOT NULL,                  -- view_progress | export_data | delete_data | consent_change | login
    subject_student_id TEXT,
    detail      TEXT,                            -- JSON
    ip_address  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
```

## 7b. Catalog versioning, provenance & billing idempotency (v9 — cross-spec additive)
These columns/tables are referenced by specs 04 (catalog) and 09 (billing) and are split into v9 so v5–v8
stay self-contained. All additive.
```sql
-- Spec 04 §5: pin an enrollment to the catalog version the student started on
ALTER TABLE enrollments ADD COLUMN course_version INTEGER DEFAULT 1;

-- Spec 04 §7: per-concept hydration provenance (legal/licensing posture, ties spec 08 F1)
CREATE TABLE IF NOT EXISTS hydration_provenance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_uid  TEXT NOT NULL,
    concept_uid TEXT NOT NULL,
    sources     TEXT,                            -- JSON: urls/titles/confidence from research_server
    model       TEXT, generated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_provenance_course ON hydration_provenance(course_uid);

-- Spec 09 §3/§8: Stripe webhook idempotency ledger (process each event once)
CREATE TABLE IF NOT EXISTS billing_events (
    provider_event_id TEXT PRIMARY KEY,          -- Stripe evt_…
    type        TEXT, parent_id TEXT,
    processed_at TEXT DEFAULT (datetime('now')),
    payload_hash TEXT
);
```
Catalog course content versions themselves are stored as immutable file snapshots under
`data/catalog/courses/{uid}/versions/v{n}.json` (spec 04 §5), not in SQLite.

## 8. Storage layer changes (sub-store API)
Every `StorageManager` per-user sub-store method gains a **leading `student_id`** parameter and an
`AND student_id = ?` clause (`ProgressStore`, `FlashcardStore`, `ActivityStore`, `ScheduleStore`,
new `GamificationStore`, `ExamStore`, `AccountStore`, `EnrollmentStore`, `ConsentStore`, `NotificationStore`,
`AccommodationStore`, `StandardsStore`). Catalog stores (`CourseStore` read path, `SearchStore`,
`StandardsStore` reads) stay global. Add `'student_id'` to each `_VALID_COLUMNS` whitelist.

## 9. Postgres portability notes (B23.4)
`INSERT OR REPLACE` → `ON CONFLICT … DO UPDATE`; `AUTOINCREMENT` → `GENERATED … AS IDENTITY`;
`datetime('now')` → `now()`; JSON TEXT → `jsonb`; FTS5 → `tsvector`/GIN (or keep catalog FTS in a read-only
SQLite sidecar since catalog is global); `concept_vec` → `pgvector`. ETL preserves `student_id`. The sub-store
abstraction means only `_ThreadLocalDB` and dialect-specific SQL change.

## 10. Open data questions (decide during build)
- Interests: JSON column now vs normalized `student_interests` table (needed only if we query/recommend by interest at scale).
- Item bank: generate-per-attempt (v1) vs persisted reviewable bank (later) — affects exam reproducibility.
- Multi-parent / co-guardian: single `parent_id` now; a `guardians(parent_id, student_id, role)` join if co-parents needed.
