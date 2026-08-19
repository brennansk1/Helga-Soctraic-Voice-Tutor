"""
Unified Storage Manager for Helga.

Replaces KuzuDB with three storage mechanisms:
- SQLite: user progress, activity log, scheduled reviews, settings
- JSON files: course structure (data/courses/{uid}/structure.json)
- Markdown files: concept content (data/courses/{uid}/content/{concept_uid}.md)
"""

import os
import re
import json
import sqlite3
import logging
import uuid
import shutil
import threading
from datetime import datetime, date, timedelta, timezone


def utc_today() -> date:
    """Today's date in UTC.

    Use this — NOT date.today() — whenever comparing against a column written
    by SQLite's CURRENT_TIMESTAMP / datetime('now'), both of which are UTC.
    Mixing a UTC-stored day with a local Python day silently breaks for the
    window between UTC midnight and local midnight (6 hours a day in MDT, more
    elsewhere). That bug made get_streak() return 0 for active users every
    evening; see its comment for the observed reproduction.
    """
    return datetime.now(timezone.utc).date()
from typing import Callable, List, Dict, Optional, Any

from services.common.concept_doc import index_text as concept_index_text

logger = logging.getLogger(__name__)

# B15 multi-tenancy: the isolation key for all per-user data. Until real auth
# lands (B15.4), every per-user store call defaults to the legacy student so
# the app keeps running single-user (spec 01 §1 backfill, spec 03 §1.2).
DEFAULT_STUDENT_ID = "stu_legacy0"
LEGACY_PARENT_ID = "par_legacy0"


def _sid(student_id: Optional[str]) -> str:
    """Resolve the effective student_id (R0 fallback = legacy student)."""
    return student_id or DEFAULT_STUDENT_ID


class _ThreadLocalDB:
    """Thread-local SQLite connection manager. One connection per thread, reused
    across calls. WAL mode enables concurrent reads from different threads."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()

    def get(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None


class StorageManager:
    """Main facade — creates SQLite DB, ensures directories, provides sub-stores."""

    def __init__(self, data_dir: str = "/app/data"):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "helga.db")
        self.courses_dir = os.path.join(data_dir, "courses")
        os.makedirs(self.courses_dir, exist_ok=True)

        self._init_db()

        # Sub-stores
        self.courses = CourseStore(self.courses_dir, self.data_dir)
        self.progress = ProgressStore(self.db_path)
        self.activity = ActivityStore(self.db_path)
        self.schedule = ScheduleStore(self.db_path)
        self.settings = SettingsStore(self.db_path)
        self.flashcards = FlashcardStore(self.db_path)
        self.search = SearchStore(self.db_path, self.courses)
        # Keep the full-text index honest on every write.
        #
        # The index was populated in exactly two places: lazily when it was
        # found EMPTY, and by a full rebuild at the end of a course build. So a
        # concept the tutor or the asset collector rewrote mid-session stayed
        # searchable only by its stale text until the next course was built —
        # and after a course was deleted its concepts kept answering searches
        # for the same reason. One row upsert on save costs nothing and closes
        # both.
        self.courses.on_content_saved = self.search.index_concept
        # B15 tenancy stores
        self.accounts = AccountStore(self.db_path)
        self.enrollments = EnrollmentStore(self.db_path)
        self.consent = ConsentStore(self.db_path)
        self.fsm = FsmSessionStore(self.db_path)
        # B16: standards layer + read-only catalog course files
        # (data/catalog/courses/, physically separate from user electives)
        self.standards = StandardsStore(self.db_path)
        self.exams = ExamStore(self.db_path)
        self.notifications = NotificationStore(self.db_path)
        self.accommodations = AccommodationStore(self.db_path)
        self.audit = AuditStore(self.db_path)
        self.subscriptions = SubscriptionStore(self.db_path)
        self.gamification = GamificationStore(self.db_path)
        self.catalog_dir = os.path.join(data_dir, "catalog", "courses")
        os.makedirs(self.catalog_dir, exist_ok=True)
        self.catalog_courses = CourseStore(self.catalog_dir, self.data_dir)

    def _init_db(self):
        """Create SQLite tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_progress (
                    concept_uid TEXT PRIMARY KEY,
                    course_uid TEXT NOT NULL,
                    status TEXT DEFAULT 'locked',
                    grade INTEGER DEFAULT 0,
                    easiness_factor REAL DEFAULT 2.5,
                    interval_days INTEGER DEFAULT 0,
                    repetitions INTEGER DEFAULT 0,
                    next_review_date TEXT,
                    last_review_date TEXT,
                    times_reviewed INTEGER DEFAULT 0,
                    times_correct INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_uid TEXT NOT NULL,
                    concept_uid TEXT,
                    unit_uid TEXT,
                    activity_type TEXT NOT NULL,
                    duration_seconds INTEGER DEFAULT 0,
                    grade INTEGER,
                    details TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS scheduled_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_uid TEXT NOT NULL,
                    unit_uid TEXT NOT NULL,
                    unit_title TEXT NOT NULL,
                    scheduled_date TEXT NOT NULL,
                    review_number INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'pending',
                    completed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS flashcards (
                    uid TEXT PRIMARY KEY,
                    course_uid TEXT NOT NULL,
                    concept_uid TEXT NOT NULL,
                    front TEXT NOT NULL,
                    back TEXT NOT NULL,
                    status TEXT DEFAULT 'new',
                    next_review_date TEXT,
                    easiness_factor REAL DEFAULT 2.5,
                    interval_days INTEGER DEFAULT 0,
                    repetitions INTEGER DEFAULT 0,
                    stability REAL,
                    difficulty REAL,
                    last_review_date TEXT,
                    lapses INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'manual',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                -- 6. Schema Versioning
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
                
                -- 7. Courses metadata table for fast listing
                CREATE TABLE IF NOT EXISTS courses (
                    uid TEXT PRIMARY KEY,
                    title TEXT,
                    overview TEXT,
                    status TEXT,
                    teaching_style TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                -- BUG-9: Performance indexes
                CREATE INDEX IF NOT EXISTS idx_progress_course ON user_progress(course_uid);
                CREATE INDEX IF NOT EXISTS idx_progress_review ON user_progress(next_review_date);
                CREATE INDEX IF NOT EXISTS idx_progress_status ON user_progress(status);
                CREATE INDEX IF NOT EXISTS idx_activity_course ON activity_log(course_uid);
                CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);
                CREATE INDEX IF NOT EXISTS idx_schedule_course ON scheduled_reviews(course_uid);
                CREATE INDEX IF NOT EXISTS idx_schedule_date ON scheduled_reviews(scheduled_date);
                CREATE INDEX IF NOT EXISTS idx_schedule_status ON scheduled_reviews(status);
                CREATE INDEX IF NOT EXISTS idx_flashcards_course ON flashcards(course_uid);
                CREATE INDEX IF NOT EXISTS idx_flashcards_concept ON flashcards(concept_uid);
                CREATE INDEX IF NOT EXISTS idx_flashcards_review ON flashcards(next_review_date);
                CREATE INDEX IF NOT EXISTS idx_courses_status ON courses(status);
            """)
            
            # Set initial version if empty
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_version")
            row = cursor.fetchone()
            current_version = row[0] if row else 0
            if current_version == 0:
                cursor.execute("INSERT INTO schema_version (version) VALUES (1)")
                current_version = 1

            # Schema migration v1 → v2: Add FSRS columns to flashcards
            if current_version < 2:
                for col, col_type, default in [
                    ("stability", "REAL", None),
                    ("difficulty", "REAL", None),
                    ("last_review_date", "TEXT", None),
                    ("lapses", "INTEGER", "0"),
                    ("source", "TEXT", "'manual'"),
                ]:
                    try:
                        default_clause = f" DEFAULT {default}" if default else ""
                        cursor.execute(f"ALTER TABLE flashcards ADD COLUMN {col} {col_type}{default_clause}")
                    except sqlite3.OperationalError:
                        pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 2")
                logger.info("Schema migrated to v2: FSRS columns added to flashcards")

            # Schema migration v2 → v3: Add bloom_level to user_progress
            if current_version < 3:
                try:
                    cursor.execute("ALTER TABLE user_progress ADD COLUMN bloom_level INTEGER DEFAULT 1")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 3")
                logger.info("Schema migrated to v3: bloom_level added to user_progress")

            # Schema migration v3 → v4: multi-tenancy core (design spec 01 §2).
            # Tenancy tables, student_id on per-user tables, legacy backfill,
            # user_progress composite-PK rebuild, fsm_sessions.
            if current_version < 4:
                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS parents (
                        id            TEXT PRIMARY KEY,
                        email         TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        display_name  TEXT,
                        status        TEXT DEFAULT 'active',
                        email_verified_at TEXT,
                        created_at    TEXT DEFAULT (datetime('now')),
                        updated_at    TEXT DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS students (
                        id            TEXT PRIMARY KEY,
                        parent_id     TEXT NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
                        display_name  TEXT NOT NULL,
                        pin_hash      TEXT,
                        grade_band    TEXT NOT NULL DEFAULT '6-8',
                        grade_numeric INTEGER,
                        avatar_url    TEXT,
                        interests     TEXT DEFAULT '[]',
                        settings      TEXT DEFAULT '{}',
                        status        TEXT DEFAULT 'active',
                        created_at    TEXT DEFAULT (datetime('now')),
                        updated_at    TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_students_parent ON students(parent_id);

                    CREATE TABLE IF NOT EXISTS enrollments (
                        id                  TEXT PRIMARY KEY,
                        student_id          TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                        course_uid          TEXT NOT NULL,
                        course_kind         TEXT NOT NULL DEFAULT 'catalog',
                        current_concept_uid TEXT,
                        status              TEXT NOT NULL DEFAULT 'active',
                        approved_by         TEXT,
                        approved_at         TEXT,
                        enrolled_at         TEXT DEFAULT (datetime('now')),
                        UNIQUE(student_id, course_uid)
                    );
                    CREATE INDEX IF NOT EXISTS idx_enroll_student ON enrollments(student_id);
                    CREATE INDEX IF NOT EXISTS idx_enroll_status  ON enrollments(student_id, status);

                    CREATE TABLE IF NOT EXISTS consent_records (
                        id            TEXT PRIMARY KEY,
                        parent_id     TEXT NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
                        student_id    TEXT REFERENCES students(id) ON DELETE CASCADE,
                        consent_type  TEXT NOT NULL,
                        granted       INTEGER NOT NULL,
                        policy_version TEXT NOT NULL,
                        method        TEXT,
                        ip_address    TEXT,
                        created_at    TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_consent_parent ON consent_records(parent_id);

                    CREATE TABLE IF NOT EXISTS subscriptions (
                        parent_id            TEXT PRIMARY KEY REFERENCES parents(id) ON DELETE CASCADE,
                        provider             TEXT DEFAULT 'stripe',
                        provider_customer_id TEXT,
                        provider_sub_id      TEXT,
                        plan                 TEXT,
                        seats                INTEGER DEFAULT 1,
                        status               TEXT DEFAULT 'inactive',
                        current_period_end   TEXT,
                        updated_at           TEXT DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS fsm_sessions (
                        student_id  TEXT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
                        blob        TEXT NOT NULL,
                        updated_at  TEXT DEFAULT (datetime('now'))
                    );
                """)

                # Synthetic legacy tenant — the app keeps running single-user
                # under this pair until real auth lands (spec 01 §1).
                cursor.execute(
                    "INSERT OR IGNORE INTO parents (id, email, password_hash, display_name, status) "
                    "VALUES (?, 'legacy@localhost', '', 'Legacy Parent', 'active')",
                    (LEGACY_PARENT_ID,))
                cursor.execute(
                    "INSERT OR IGNORE INTO students (id, parent_id, display_name, grade_band) "
                    "VALUES (?, ?, 'Legacy Learner', '9-12')",
                    (DEFAULT_STUDENT_ID, LEGACY_PARENT_ID))

                # student_id on per-user tables + backfill
                for table in ("activity_log", "scheduled_reviews", "flashcards"):
                    try:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN student_id TEXT")
                    except sqlite3.OperationalError:
                        pass  # Column already exists
                    cursor.execute(
                        f"UPDATE {table} SET student_id = ? WHERE student_id IS NULL",
                        (DEFAULT_STUDENT_ID,))

                # user_progress composite-PK rebuild: PK was concept_uid alone,
                # which allows exactly one student per concept. SQLite can't
                # alter a PK in place → rebuild with pinned column order.
                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS user_progress_new (
                        student_id TEXT NOT NULL,
                        concept_uid TEXT NOT NULL,
                        course_uid TEXT NOT NULL,
                        status TEXT DEFAULT 'locked',
                        grade INTEGER DEFAULT 0,
                        easiness_factor REAL DEFAULT 2.5,
                        interval_days INTEGER DEFAULT 0,
                        repetitions INTEGER DEFAULT 0,
                        next_review_date TEXT,
                        last_review_date TEXT,
                        times_reviewed INTEGER DEFAULT 0,
                        times_correct INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        bloom_level INTEGER DEFAULT 1,
                        PRIMARY KEY (student_id, concept_uid)
                    );
                """)
                cursor.execute(f"""
                    INSERT OR IGNORE INTO user_progress_new (
                        student_id, concept_uid, course_uid, status, grade,
                        easiness_factor, interval_days, repetitions,
                        next_review_date, last_review_date, times_reviewed,
                        times_correct, created_at, updated_at, bloom_level)
                    SELECT '{DEFAULT_STUDENT_ID}', concept_uid, course_uid, status, grade,
                        easiness_factor, interval_days, repetitions,
                        next_review_date, last_review_date, times_reviewed,
                        times_correct, created_at, updated_at, bloom_level
                    FROM user_progress
                """)
                cursor.executescript("""
                    DROP TABLE user_progress;
                    ALTER TABLE user_progress_new RENAME TO user_progress;
                    CREATE INDEX IF NOT EXISTS idx_progress_course ON user_progress(course_uid);
                    CREATE INDEX IF NOT EXISTS idx_progress_review ON user_progress(next_review_date);
                    CREATE INDEX IF NOT EXISTS idx_progress_status ON user_progress(status);
                    CREATE INDEX IF NOT EXISTS idx_progress_student   ON user_progress(student_id, course_uid);
                    CREATE INDEX IF NOT EXISTS idx_activity_student   ON activity_log(student_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_flashcards_student ON flashcards(student_id, next_review_date);
                    CREATE INDEX IF NOT EXISTS idx_schedule_student   ON scheduled_reviews(student_id, scheduled_date);
                """)
                cursor.execute("UPDATE schema_version SET version = 4")
                logger.info("Schema migrated to v4: multi-tenancy core (tenancy tables, "
                            "student_id scoping, legacy backfill, user_progress PK rebuild)")

            # Schema migration v4 → v5: standards layer + catalog columns
            # (design spec 01 §4, spec 04). Catalog tables carry NO student_id —
            # they are global and read-only to students.
            if current_version < 5:
                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS standards (
                        code         TEXT PRIMARY KEY,
                        subject      TEXT NOT NULL,
                        grade_band   TEXT,
                        grade_numeric INTEGER,
                        strand       TEXT NOT NULL,
                        text         TEXT NOT NULL,
                        is_enrichment INTEGER DEFAULT 0,
                        source       TEXT DEFAULT 'USBE',
                        adopted_year INTEGER
                    );
                    CREATE INDEX IF NOT EXISTS idx_standards_subject ON standards(subject, grade_band);

                    CREATE TABLE IF NOT EXISTS concept_standards (
                        concept_uid   TEXT NOT NULL,
                        standard_code TEXT NOT NULL REFERENCES standards(code),
                        coverage      TEXT DEFAULT 'full',
                        PRIMARY KEY (concept_uid, standard_code)
                    );
                    CREATE INDEX IF NOT EXISTS idx_cs_standard ON concept_standards(standard_code);
                """)
                for col, decl in (
                        ("subject", "TEXT"), ("grade_band", "TEXT"),
                        ("grade_numeric", "INTEGER"),
                        ("is_catalog", "INTEGER DEFAULT 0"),
                        ("catalog_status", "TEXT DEFAULT 'draft'"),
                        ("version", "INTEGER DEFAULT 1"),
                        ("visibility", "TEXT DEFAULT 'private'"),
                        ("reviewed_by", "TEXT"), ("published_at", "TEXT"),
                        ("enrichment_included", "INTEGER DEFAULT 0")):
                    try:
                        cursor.execute(f"ALTER TABLE courses ADD COLUMN {col} {decl}")
                    except sqlite3.OperationalError:
                        pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 5")
                logger.info("Schema migrated to v5: standards + concept_standards + catalog columns")

            # Schema migration v5 → v6: assessment/exam tables (spec 01 §5)
            if current_version < 6:
                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS exams (
                        id           TEXT PRIMARY KEY,
                        course_uid   TEXT,
                        scope_uid    TEXT,
                        kind         TEXT NOT NULL,
                        standard_codes TEXT DEFAULT '[]',
                        blueprint    TEXT NOT NULL,
                        pass_threshold REAL DEFAULT 0.8,
                        created_at   TEXT DEFAULT (datetime('now'))
                    );

                    CREATE TABLE IF NOT EXISTS exam_attempts (
                        id           TEXT PRIMARY KEY,
                        student_id   TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                        exam_id      TEXT NOT NULL REFERENCES exams(id),
                        course_uid   TEXT,
                        status       TEXT DEFAULT 'in_progress',
                        score        REAL,
                        passed       INTEGER,
                        theme        TEXT,
                        accommodations TEXT DEFAULT '{}',
                        started_at   TEXT DEFAULT (datetime('now')),
                        submitted_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_attempt_student ON exam_attempts(student_id, exam_id);

                    CREATE TABLE IF NOT EXISTS exam_item_responses (
                        id            TEXT PRIMARY KEY,
                        attempt_id    TEXT NOT NULL REFERENCES exam_attempts(id) ON DELETE CASCADE,
                        standard_code TEXT,
                        bloom_level   INTEGER,
                        item_type     TEXT,
                        prompt        TEXT,
                        correct       TEXT,
                        response      TEXT,
                        grade         INTEGER,
                        is_correct    INTEGER,
                        theme_validated INTEGER DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS idx_response_attempt ON exam_item_responses(attempt_id);
                """)
                cursor.execute("UPDATE schema_version SET version = 6")
                logger.info("Schema migrated to v6: exam tables")

            # v6 → v7: per-student gamification (spec 01 §6) — replaces the
            # librarian's global K-V when B22 lands; schema ships now so the
            # migration chain stays linear.
            if current_version < 7:
                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS student_gamification (
                        student_id  TEXT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
                        total_xp    INTEGER DEFAULT 0,
                        level       INTEGER DEFAULT 1,
                        streak_days INTEGER DEFAULT 0,
                        streak_last_date TEXT,
                        daily_xp    INTEGER DEFAULT 0,
                        daily_date  TEXT,
                        cosmetics   TEXT DEFAULT '{}'
                    );
                    CREATE TABLE IF NOT EXISTS xp_ledger (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id  TEXT NOT NULL,
                        amount      INTEGER NOT NULL,
                        reason      TEXT,
                        ref_uid     TEXT,
                        created_at  TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_xp_student ON xp_ledger(student_id, created_at);
                    CREATE TABLE IF NOT EXISTS badges (
                        id TEXT PRIMARY KEY, name TEXT, description TEXT, icon TEXT,
                        criteria TEXT, xp_reward INTEGER DEFAULT 0, scope TEXT );
                    CREATE TABLE IF NOT EXISTS student_badges (
                        student_id TEXT NOT NULL, badge_id TEXT NOT NULL,
                        unlocked_at TEXT DEFAULT (datetime('now')),
                        PRIMARY KEY (student_id, badge_id) );
                    CREATE TABLE IF NOT EXISTS quests (
                        id TEXT PRIMARY KEY, title TEXT, kind TEXT, target INTEGER,
                        xp_reward INTEGER, cadence TEXT );
                    CREATE TABLE IF NOT EXISTS student_quests (
                        student_id TEXT NOT NULL, quest_id TEXT NOT NULL,
                        progress INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
                        period_key TEXT,
                        PRIMARY KEY (student_id, quest_id, period_key) );
                """)
                cursor.execute("UPDATE schema_version SET version = 7")
                logger.info("Schema migrated to v7: per-student gamification tables")

            # v7 → v8: accommodations, notifications, compliance audit (spec 01 §7)
            if current_version < 8:
                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS accommodations (
                        student_id   TEXT PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
                        extended_time INTEGER DEFAULT 0,
                        no_timer      INTEGER DEFAULT 0,
                        reduced_distraction INTEGER DEFAULT 0,
                        larger_targets INTEGER DEFAULT 0,
                        extra_scaffolding INTEGER DEFAULT 0,
                        simplified_language INTEGER DEFAULT 0,
                        read_aloud_default INTEGER DEFAULT 0,
                        notes        TEXT,
                        set_by       TEXT,
                        updated_at   TEXT DEFAULT (datetime('now'))
                    );
                    CREATE TABLE IF NOT EXISTS notifications (
                        id          TEXT PRIMARY KEY,
                        recipient_id TEXT NOT NULL,
                        recipient_role TEXT NOT NULL,
                        kind        TEXT NOT NULL,
                        title       TEXT, body TEXT, ref_uid TEXT,
                        read_at     TEXT,
                        created_at  TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_notif_recipient ON notifications(recipient_id, read_at);
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        actor_id    TEXT, actor_role TEXT,
                        action      TEXT NOT NULL,
                        subject_student_id TEXT,
                        detail      TEXT,
                        ip_address  TEXT,
                        created_at  TEXT DEFAULT (datetime('now'))
                    );
                """)
                cursor.execute("UPDATE schema_version SET version = 8")
                logger.info("Schema migrated to v8: accommodations/notifications/audit_log")

            # v8 → v9: catalog version pinning, hydration provenance, billing
            # idempotency (spec 01 §7b — cross-spec additive)
            if current_version < 9:
                try:
                    cursor.execute("ALTER TABLE enrollments ADD COLUMN course_version INTEGER DEFAULT 1")
                except sqlite3.OperationalError:
                    pass
                cursor.executescript("""
                    CREATE TABLE IF NOT EXISTS hydration_provenance (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_uid  TEXT NOT NULL,
                        concept_uid TEXT NOT NULL,
                        sources     TEXT,
                        model       TEXT, generated_at TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_provenance_course ON hydration_provenance(course_uid);
                    CREATE TABLE IF NOT EXISTS billing_events (
                        provider_event_id TEXT PRIMARY KEY,
                        type        TEXT, parent_id TEXT,
                        processed_at TEXT DEFAULT (datetime('now')),
                        payload_hash TEXT
                    );
                """)
                cursor.execute("UPDATE schema_version SET version = 9")
                logger.info("Schema migrated to v9: version pinning + provenance + billing events")

            # v9 → v10: FSRS memory state on user_progress.
            #
            # Concept-level review scheduling used a fixed grade→interval table
            # ({4: [7, 30]} and so on) while the FSRS engine — already used for
            # flashcards, with 48 passing tests — sat unused. So the schedule
            # ignored review history entirely: answering a concept correctly for
            # the fifth time scheduled it exactly as far out as the first time.
            # That is the difference between spaced repetition and a reminder.
            #
            # These are the columns FSRS needs to carry memory between reviews.
            # Additive and nullable: an existing row with NULL stability is
            # simply a card that has not been reviewed under FSRS yet, which is
            # the engine's own first-review path.
            if current_version < 10:
                for col, decl in (("stability", "REAL"),
                                  ("difficulty", "REAL"),
                                  ("lapses", "INTEGER DEFAULT 0")):
                    try:
                        cursor.execute(
                            f"ALTER TABLE user_progress ADD COLUMN {col} {decl}")
                    except sqlite3.OperationalError:
                        pass  # already present
                cursor.execute("UPDATE schema_version SET version = 10")
                logger.info("Schema migrated to v10: FSRS memory state on user_progress")

            if current_version < 11:
                # The taught-concepts ledger: what a course has already said, so
                # a later concept can cite it instead of re-teaching it.
                # Created here rather than lazily so a fresh install has it
                # before the first hydration and the indexes exist before any
                # rows do. See services/core/taught_ledger.py for the design.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS taught_concepts (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        title        TEXT NOT NULL,
                        ordinal      INTEGER NOT NULL,
                        module       TEXT,
                        lesson       TEXT,
                        embedding    BLOB,
                        embedder     TEXT,
                        body_hash    TEXT,
                        shingles     TEXT,
                        PRIMARY KEY (course_uid, concept_uid)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS taught_claims (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        ordinal      INTEGER NOT NULL,
                        claim        TEXT NOT NULL,
                        keywords     TEXT NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_taught_course "
                               "ON taught_concepts(course_uid, ordinal)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_course "
                               "ON taught_claims(course_uid, ordinal)")
                cursor.execute("UPDATE schema_version SET version = 11")
                logger.info("Schema migrated to v11: taught-concepts ledger")

            if current_version < 12:
                # RETAINED SOURCE PASSAGES — the durable home.
                #
                # What reaches the tutor today is generated Markdown, a lossy
                # re-expression of whatever research returned. The research
                # CACHE holds the originals but it is a speed layer with a
                # 24h/7d TTL, so it must never be the only copy: a claim cannot
                # be verified against a passage that has expired.
                #
                # `retrieved_at` and `degraded` preserve absent-vs-zero through
                # this layer too. A retained row with no text is a source we
                # fetched and got nothing from; a missing row is a source we
                # never fetched. Those must not look alike.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sources (
                        source_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT,
                        title        TEXT,
                        url          TEXT,
                        passage      TEXT,
                        source_type  TEXT,
                        domain_tier  TEXT,
                        grounding    REAL,
                        degraded     INTEGER DEFAULT 0,
                        retrieved_at TEXT
                    )
                """)
                # Which claims rest on which sources. This is what makes
                # "claims grounded ONLY in supplementary material" a measurable
                # share rather than an assertion — the policy recorded on the
                # course counts claims, and this is where the count comes from.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS claim_sources (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        claim        TEXT NOT NULL,
                        source_id    INTEGER,
                        supplementary INTEGER DEFAULT 0
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_course "
                               "ON sources(course_uid, concept_uid)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_claim_sources "
                               "ON claim_sources(course_uid, concept_uid)")
                cursor.execute("UPDATE schema_version SET version = 12")
                logger.info("Schema migrated to v12: retained sources + claim links")

            if current_version < 13:
                # SESSION NOTES, append-only, WITH ITS COMPACTION BOUNDARY
                # DESIGNED IN FROM THE START.
                #
                # Content is ~32 MB for a bachelor's and negligible; notes are
                # the one component that grows without bound — ~50 turns a
                # session, over four years. Retrofitting compaction onto years
                # of rows is the painful path, so `compacted` exists before
                # there is anything to compact: raw turns are kept verbatim for
                # a retention window, then collapsed to FSRS state plus a
                # summary and the raw text dropped.
                #
                # Kept out of the concepts table on purpose — append churn from
                # notes would otherwise bloat content pages and the WAL.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS session_notes (
                        note_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_uid   TEXT,
                        concept_uid  TEXT,
                        student_id   TEXT,
                        role         TEXT,
                        text         TEXT,
                        grade        INTEGER,
                        created_at   TEXT NOT NULL,
                        compacted    INTEGER DEFAULT 0
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_concept "
                               "ON session_notes(concept_uid, created_at)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_compaction "
                               "ON session_notes(compacted, created_at)")
                cursor.execute("UPDATE schema_version SET version = 13")
                logger.info("Schema migrated to v13: append-only session notes")

            if current_version < 14:
                # The teaching object: a concept parsed into addressable
                # structure — claims, worked steps, belief/correction pairs,
                # question seeds per Bloom band, the grade-3 threshold.
                #
                # NOT a migration of where content lives. The Markdown stays
                # canonical; this is a parsed view stored beside it, because the
                # only consumer is a model and prose cannot express "the seed
                # question for Bloom 3-4" without a regex at session time.
                #
                # `completeness` is stored with it so hollowness is a query
                # rather than a re-parse: a concept can pass the section
                # template and fill almost none of these fields, which is what
                # "structurally complete, substantively empty" means.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS teaching_objects (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        obj          TEXT NOT NULL,
                        completeness REAL,
                        PRIMARY KEY (course_uid, concept_uid)
                    )
                """)
                cursor.execute("UPDATE schema_version SET version = 14")
                logger.info("Schema migrated to v14: teaching objects")

            if current_version < 15:
                # CONCEPT BODIES IN THE DATABASE, WITH THE .md AS A MIRROR.
                #
                # Concepts are 1-6 KB with a ~15 KB ceiling under the depth
                # contract, and SQLite's own benchmark puts small blobs ~35%
                # faster to read and write in-database than as individual files,
                # at ~20% less disk, with the filesystem only winning above
                # ~100 KB. So the performance argument points here.
                #
                # But speed is not the reason. Three things this buys that
                # one-file-per-concept structurally cannot:
                #
                #   * ABSENT-VS-ZERO BECOMES STRUCTURAL. A row with an empty
                #     body is a concept we hydrated and got nothing from; a
                #     missing row is one never attempted. On disk both are "no
                #     file", and this project has been bitten by that confusion
                #     repeatedly.
                #   * Structure (JSON), state (progress) and content become
                #     transactionally consistent instead of three stores that
                #     can disagree after a crash.
                #   * The ledger, sources, claims and teaching objects become
                #     co-resident with the content they index, so retrieval is a
                #     JOIN rather than a filesystem walk plus a separate index.
                #
                # The .md files are still written, and reads fall back to them,
                # so this is additive rather than a migration with a cutover.
                # `content_hash` is what makes drift between the two detectable
                # instead of silent.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS concepts (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        title        TEXT,
                        content      TEXT,
                        content_hash TEXT,
                        path         TEXT,
                        words        INTEGER,
                        updated_at   TEXT,
                        PRIMARY KEY (course_uid, concept_uid)
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_concepts_course "
                               "ON concepts(course_uid)")
                # FTS5 over content, so search stops being a substring scan.
                try:
                    cursor.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS concepts_fts
                        USING fts5(concept_uid UNINDEXED, course_uid UNINDEXED,
                                   title, content)
                    """)
                except sqlite3.OperationalError as e:
                    # FTS5 is compiled in on every build we target, but a search
                    # index that cannot be created must not stop a migration.
                    logger.warning(f"FTS5 unavailable, search stays substring: {e}")
                cursor.execute("UPDATE schema_version SET version = 15")
                logger.info("Schema migrated to v15: concept bodies in SQLite + FTS5")

            conn.commit()
        finally:
            conn.close()

    def mastery_overview(self, course_uid: str = None, student_id: str = None) -> dict:
        """Answer "what do I actually know?" — the A5.2 Progress surface.

        Lives on the facade because it is the one query that needs BOTH halves:
        the memory state in SQLite and the concept titles in the course JSON. A
        progress page listing `con_4f2a91bc` instead of "Confounding variables"
        is not a progress page.

        Everything here is derived from stored state; nothing is estimated. A
        concept the learner has never answered is reported as `unseen` rather
        than given a zero score, because "not yet studied" and "studied and
        forgotten" are different answers to "where are my gaps?" and collapsing
        them is how a dashboard starts lying.
        """
        try:
            from services.core.fsrs_engine import FSRSEngine
            engine = FSRSEngine()
        except Exception:
            engine = None

        rows = {}
        conn = self.progress._get_db()
        query = "SELECT * FROM user_progress WHERE student_id = ?"
        params = [_sid(student_id)]
        if course_uid:
            query += " AND course_uid = ?"
            params.append(course_uid)
        for r in conn.execute(query, params).fetchall():
            rows[r["concept_uid"]] = dict(r)

        course_uids = ([course_uid] if course_uid
                       else [c.get("uid") for c in (self.courses.list_courses() or [])])

        today = date.today()
        courses, concepts = [], []

        for cuid in [c for c in course_uids if c]:
            try:
                meta = self.courses.get_course(cuid) or {}
                flat = self.courses.get_flat_concepts(cuid) or []
            except Exception as e:
                logger.warning(f"mastery_overview: skipping {cuid}: {e}")
                continue

            seen = reviewed = correct = 0
            bloom_sum = bloom_n = 0

            for con in flat:
                p = rows.get(con.get("uid")) or {}
                tr = p.get("times_reviewed") or 0
                tc = p.get("times_correct") or 0
                stability = p.get("stability")

                retention = None
                if engine is not None and stability:
                    elapsed = 0
                    if p.get("last_review_date"):
                        try:
                            elapsed = max(0, (today - date.fromisoformat(
                                p["last_review_date"])).days)
                        except (ValueError, TypeError):
                            elapsed = 0
                    try:
                        retention = round(engine.get_retention(stability, elapsed), 3)
                    except Exception:
                        retention = None

                state = "unseen"
                if p.get("status") == "completed":
                    state = "known"
                elif tr:
                    state = "learning"

                if state != "unseen":
                    seen += 1
                    reviewed += tr
                    correct += tc
                    if p.get("bloom_level"):
                        bloom_sum += p["bloom_level"]
                        bloom_n += 1

                concepts.append({
                    "concept_uid": con.get("uid"),
                    "course_uid": cuid,
                    "title": con.get("title"),
                    "module": con.get("module_title"),
                    "state": state,
                    "bloom_level": p.get("bloom_level") or con.get("bloom_level"),
                    "times_reviewed": tr,
                    "times_correct": tc,
                    "accuracy": round(tc / tr, 3) if tr else None,
                    "lapses": p.get("lapses") or 0,
                    "stability_days": round(stability, 1) if stability else None,
                    "retention": retention,
                    "next_review_date": p.get("next_review_date"),
                })

            total = len(flat)
            if not total:
                # A course row with no concepts is a failed or half-built
                # generation, not something a learner has progress in. Listing
                # it would print a raw uid ("course_c6620699") next to
                # "0 / 0 known", which reads as a rendering bug.
                continue
            courses.append({
                "course_uid": cuid,
                "title": meta.get("title") or cuid,
                "total_concepts": total,
                "started": seen,
                "known": sum(1 for c in concepts
                             if c["course_uid"] == cuid and c["state"] == "known"),
                "coverage": round(seen / total, 3) if total else 0.0,
                "accuracy": round(correct / reviewed, 3) if reviewed else None,
                "avg_bloom": round(bloom_sum / bloom_n, 1) if bloom_n else None,
            })

        # Gaps, ranked by what most deserves attention: anything answered wrong
        # more than it was answered right, then anything decayed below 0.7
        # recall. Unseen concepts are excluded — that is a backlog, not a gap.
        gaps = [c for c in concepts if c["state"] != "unseen" and (
            (c["accuracy"] is not None and c["accuracy"] < 0.5)
            or c["lapses"] > 0
            or (c["retention"] is not None and c["retention"] < 0.7))]
        gaps.sort(key=lambda c: (c["accuracy"] if c["accuracy"] is not None else 1.0,
                                 -c["lapses"]))

        started = [c for c in concepts if c["state"] != "unseen"]
        total_reviewed = sum(c["times_reviewed"] for c in started)
        total_correct = sum(c["times_correct"] for c in started)

        return {
            "courses": courses,
            "concepts": concepts,
            "gaps": gaps[:20],
            "totals": {
                "courses": len(courses),
                "concepts": len(concepts),
                "started": len(started),
                "known": sum(1 for c in concepts if c["state"] == "known"),
                "accuracy": (round(total_correct / total_reviewed, 3)
                             if total_reviewed else None),
                "due_today": sum(
                    1 for c in concepts
                    if c["next_review_date"] and c["next_review_date"] <= today.isoformat()),
            },
        }

    def reset(self):
        """Delete all data — equivalent to clean_slate."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.courses_dir):
            shutil.rmtree(self.courses_dir)
        os.makedirs(self.courses_dir, exist_ok=True)
        self._init_db()


class CourseStore:
    """JSON course structure CRUD + Markdown content files."""

    def __init__(self, courses_dir: str, data_dir: str):
        self.courses_dir = courses_dir
        self.data_dir = data_dir
        self._cache = {}
        # Set by StorageManager to the search index's upserter. Left None here
        # so a CourseStore built standalone (tests, scripts) still works.
        self.on_content_saved = None

    def _get_db(self) -> sqlite3.Connection:
        """Thread-local connection to the same helga.db every other store uses.

        CourseStore was the one store with no database handle, because it began
        as pure JSON-and-Markdown. Concept bodies now live in `concepts` (v15)
        alongside the ledger and retained sources that key off them, so it needs
        one. Thread-local for the same reason the other stores are: hydration
        runs in a ThreadPoolExecutor and SQLite connections are not shareable
        across threads.
        """
        if not hasattr(self, "_tl"):
            import threading
            self._tl = threading.local()
        conn = getattr(self._tl, "conn", None)
        if conn is None:
            conn = sqlite3.connect(os.path.join(self.data_dir, "helga.db"),
                                   timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            self._tl.conn = conn
        return conn

    def create_course(self, course_dict: dict) -> str:
        """Write course structure.json and sync metadata to SQLite."""
        uid = course_dict.get("uid") or f"course_{uuid.uuid4().hex[:8]}"
        course_dict["uid"] = uid
        if "created_at" not in course_dict:
            course_dict["created_at"] = datetime.utcnow().isoformat()
        if "status" not in course_dict:
            course_dict["status"] = "skeleton"

        # AUTO-10: Write SQLite row first; only write JSON if SQLite succeeds
        cat = course_dict.get("catalog") or {}
        db_path = os.path.join(self.data_dir, "helga.db")
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO courses (uid, title, overview, status, teaching_style,
                    subject, grade_band, grade_numeric, is_catalog, catalog_status,
                    version, visibility, reviewed_by, published_at, enrichment_included)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uid,
                course_dict.get("title", ""),
                course_dict.get("overview", ""),
                course_dict.get("status", "unknown"),
                course_dict.get("teaching_style", ""),
                cat.get("subject"), cat.get("grade_band"), cat.get("grade_numeric"),
                1 if cat.get("is_catalog") else 0,
                cat.get("catalog_status", "draft") if cat else "draft",
                cat.get("version", 1) if cat else 1,
                cat.get("visibility", "private") if cat else "private",
                cat.get("reviewed_by"), cat.get("published_at"),
                1 if cat.get("enrichment_included") else 0,
            ))
            conn.commit()

        course_dir = os.path.join(self.courses_dir, uid)
        os.makedirs(course_dir, exist_ok=True)
        os.makedirs(os.path.join(course_dir, "content"), exist_ok=True)

        structure_path = os.path.join(course_dir, "structure.json")
        with open(structure_path, "w") as f:
            json.dump(course_dict, f, indent=2)

        logger.info(f"Created course structure: {structure_path}")
        return uid

    def get_course(self, uid: str) -> Optional[dict]:
        """Read course structure.json."""
        if uid in self._cache:
            # Return a copy to prevent accidental in-memory mutations affecting the cache
            import copy
            return copy.deepcopy(self._cache[uid])
            
        path = os.path.join(self.courses_dir, uid, "structure.json")
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            course = json.load(f)
            import copy
            self._cache[uid] = copy.deepcopy(course)
            return course

    def update_course(self, uid: str, course_dict: dict):
        """Overwrite course structure.json and update metadata in SQLite."""
        course_dict["uid"] = uid
        course_dict["updated_at"] = datetime.utcnow().isoformat()
        
        import copy
        self._cache[uid] = copy.deepcopy(course_dict)
        
        path = os.path.join(self.courses_dir, uid, "structure.json")
        with open(path, "w") as f:
            json.dump(course_dict, f, indent=2)
            
        # Update metadata table
        try:
            db_path = os.path.join(self.data_dir, "helga.db")
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cat = course_dict.get("catalog") or {}
                cursor.execute("""
                    UPDATE courses SET title=?, overview=?, status=?, teaching_style=?,
                        subject=?, grade_band=?, grade_numeric=?, is_catalog=?,
                        catalog_status=?, version=?, visibility=?, reviewed_by=?,
                        published_at=?, enrichment_included=?
                    WHERE uid=?
                """, (
                    course_dict.get("title", ""),
                    course_dict.get("overview", ""),
                    course_dict.get("status", "unknown"),
                    course_dict.get("teaching_style", ""),
                    cat.get("subject"), cat.get("grade_band"), cat.get("grade_numeric"),
                    1 if cat.get("is_catalog") else 0,
                    cat.get("catalog_status", "draft") if cat else "draft",
                    cat.get("version", 1) if cat else 1,
                    cat.get("visibility", "private") if cat else "private",
                    cat.get("reviewed_by"), cat.get("published_at"),
                    1 if cat.get("enrichment_included") else 0,
                    uid
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to update course metadata in SQLite: {e}")

    def list_catalog_courses(self, published_only: bool = True,
                             subject: str = None, grade_band: str = None) -> List[dict]:
        """B16.2: catalog listing. Students only ever see
        is_catalog=1 AND catalog_status='published' rows."""
        q = "SELECT * FROM courses WHERE is_catalog = 1"
        params = []
        if published_only:
            q += " AND catalog_status = 'published'"
        if subject:
            q += " AND subject = ?"
            params.append(subject)
        if grade_band:
            q += " AND grade_band = ?"
            params.append(grade_band)
        try:
            with sqlite3.connect(os.path.join(self.data_dir, "helga.db")) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(q + " ORDER BY subject, grade_numeric, title",
                                    params).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to list catalog courses: {e}")
            return []

    def list_courses(self) -> List[dict]:
        """List all courses (metadata only from SQLite)."""
        courses = []
        try:
            with sqlite3.connect(os.path.join(self.data_dir, "helga.db")) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM courses ORDER BY created_at DESC")
                for row in cursor.fetchall():
                    courses.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to list courses from SQLite: {e}")
            # Fallback to filesystem if SQLite fails or table missing
            if not os.path.exists(self.courses_dir):
                return courses
            for name in sorted(os.listdir(self.courses_dir)):
                course_dir = os.path.join(self.courses_dir, name)
                if os.path.isdir(course_dir):
                    c = self.get_course(name)
                    if c: courses.append(c)
        return courses

    def delete_course(self, uid: str) -> bool:
        """Delete course directory and ALL related SQLite rows.

        Cascades the delete to every table that references course_uid so no
        orphan rows leak into review stats, activity logs, flashcard queues,
        or progress history. Previously this only cleared the `courses` row,
        leaving stale flashcards/progress/activity entries behind that would
        pollute aggregate queries (e.g., review stats counting cards from a
        deleted course, or fresh rebuilds inheriting old progress).
        """
        if uid in self._cache:
            del self._cache[uid]

        course_dir = os.path.join(self.courses_dir, uid)
        deleted = False
        if os.path.exists(course_dir):
            shutil.rmtree(course_dir)
            logger.info(f"Deleted course directory: {uid}")
            deleted = True

        # Cascade delete across every table that references course_uid.
        try:
            db_path = os.path.join(self.data_dir, "helga.db")
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cascade_tables = [
                    ("courses", "uid"),
                    ("user_progress", "course_uid"),
                    ("flashcards", "course_uid"),
                    ("scheduled_reviews", "course_uid"),
                    ("activity_log", "course_uid"),
                    # These three were missing from a list whose whole purpose
                    # is that nothing is missing from it. concept_fts kept
                    # answering searches with a deleted course's concepts;
                    # hydration_provenance kept the licensing record of content
                    # that no longer exists; concept_vec kept its embeddings.
                    ("concept_fts", "course_uid"),
                    ("concept_vec", "course_uid"),
                    ("hydration_provenance", "course_uid"),
                ]
                total_rows = 0
                for table, col in cascade_tables:
                    try:
                        cursor.execute(
                            f"DELETE FROM {table} WHERE {col}=?", (uid,)
                        )
                        if cursor.rowcount > 0:
                            total_rows += cursor.rowcount
                            logger.info(
                                f"Deleted {cursor.rowcount} row(s) from {table} "
                                f"for course {uid}"
                            )
                    except sqlite3.OperationalError as e:
                        # Table or column may not exist in older schemas — skip.
                        logger.debug(f"Cascade skip for {table}: {e}")
                conn.commit()
                if total_rows > 0:
                    logger.info(
                        f"Course {uid} cascade delete removed {total_rows} total row(s)"
                    )
        except Exception as e:
            logger.error(f"Failed to cascade delete course {uid}: {e}")

        return deleted

    def get_flat_concepts(self, uid: str) -> List[dict]:
        """Flatten course hierarchy into ordered concept list for syllabus queue."""
        course = self.get_course(uid)
        if not course:
            return []
        concepts = []
        for module in course.get("modules", []):
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        concepts.append({
                            "uid": concept["uid"],
                            "title": concept["title"],
                            "module_title": module["title"],
                            "unit_title": unit["title"],
                            "lesson_title": lesson["title"],
                            "depth_level": concept.get("depth_level", 3),
                            "learning_objectives": concept.get("learning_objectives", []),
                            "bloom_level": concept.get("bloom_level"),
                            "complexity_role": concept.get("complexity_role", ""),
                            "module_bloom_target": module.get("bloom_target"),
                            # Written into structure.json by the builder and the
                            # hydrator, then dropped here — so the FSM had no way
                            # to know a concept was a generated stub or that its
                            # grounding pass came back nearly empty, and taught
                            # it with exactly the same confidence as a
                            # well-sourced one.
                            "ordinal": concept.get("ordinal"),
                            "llm_fallback": bool(concept.get("llm_fallback")),
                            "source_confidence": concept.get("source_confidence"),
                            "text": "",  # Will be loaded from .md on demand
                        })
        return concepts

    def get_concept_by_uid(self, course_uid: str, concept_uid: str) -> Optional[dict]:
        """Find a concept by UID within a course structure."""
        course = self.get_course(course_uid)
        if not course:
            return None
        for module in course.get("modules", []):
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        if concept["uid"] == concept_uid:
                            return {
                                **concept,
                                "module_title": module["title"],
                                "unit_title": unit["title"],
                                "lesson_title": lesson["title"],
                            }
        return None

    def find_concept_across_courses(self, concept_uid: str) -> Optional[dict]:
        """Search for a concept UID across all courses."""
        for course in self.list_courses():
            result = self.get_concept_by_uid(course["uid"], concept_uid)
            if result:
                result["course_uid"] = course["uid"]
                result["course_title"] = course["title"]
                return result
        return None

    def get_concept_content(self, course_uid: str, concept_uid: str) -> str:
        """Concept content: the database first, the .md file as fallback.

        DB-first because a row can be EMPTY, and a file cannot. "Hydrated and
        produced nothing" and "never hydrated" are different states that the
        filesystem renders identically as a missing file, and this project has
        repeatedly been bitten by exactly that confusion.

        The disk fallback keeps every course built before v15 readable, so this
        is additive rather than a cutover.
        """
        try:
            row = self._get_db().execute(
                "SELECT content FROM concepts WHERE course_uid=? AND concept_uid=?",
                (course_uid, concept_uid)).fetchone()
            if row is not None:
                return row[0] or ""
        except sqlite3.OperationalError:
            pass  # pre-v15 database
        except Exception as e:
            logger.debug(f"concept read from DB failed for {concept_uid}: {e}")

        for subdir in ["content", "topics"]:
            path = os.path.join(self.courses_dir, course_uid, subdir, f"{concept_uid}.md")
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read()
        return ""

    def add_session_note(self, course_uid, concept_uid, role, text,
                         student_id=None, grade=None):
        """Append one turn of a tutoring session. Never fails a session."""
        try:
            db = self._get_db()
            db.execute(
                "INSERT INTO session_notes (course_uid, concept_uid, student_id, "
                "role, text, grade, created_at) VALUES (?,?,?,?,?,?,?)",
                (course_uid, concept_uid, student_id, role, text, grade,
                 datetime.now().isoformat()))
            db.commit()
        except Exception as e:
            logger.debug(f"session note skipped: {e}")

    def compact_session_notes(self, older_than_days=90, keep_per_concept=6):
        """Collapse old raw turns, keeping a recent tail per concept.

        Notes are the one component that grows without bound: content is ~32 MB
        for a bachelor's and negligible, while ~50 turns a session over four
        years is not. The compaction BOUNDARY was designed in before there was
        anything to compact, because retrofitting it onto years of rows is the
        painful path.

        What is dropped is the raw TEXT, not the row: the grade, the timestamp
        and the concept survive, because those are what FSRS and the retention
        curves read. A compacted note is still evidence that a turn happened —
        deleting the row would silently shorten a learner's history.

        Returns a summary of what it did, so a caller can log it rather than
        guess.
        """
        try:
            db = self._get_db()
            cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
            # The newest `keep_per_concept` turns per concept stay verbatim even
            # when old: they are what a resumed session reads for continuity.
            keep = {r[0] for r in db.execute(
                "SELECT note_id FROM ("
                "  SELECT note_id, ROW_NUMBER() OVER ("
                "    PARTITION BY concept_uid ORDER BY created_at DESC) rn"
                "  FROM session_notes WHERE compacted = 0"
                ") WHERE rn <= ?", (keep_per_concept,))}
            candidates = [r[0] for r in db.execute(
                "SELECT note_id FROM session_notes "
                "WHERE compacted = 0 AND created_at < ?", (cutoff,))]
            targets = [n for n in candidates if n not in keep]
            for nid in targets:
                db.execute("UPDATE session_notes SET text = NULL, compacted = 1 "
                           "WHERE note_id = ?", (nid,))
            db.commit()
            return {"compacted": len(targets), "kept_recent": len(keep),
                    "cutoff_days": older_than_days}
        except Exception as e:
            logger.warning(f"session-note compaction failed: {e}")
            return {"compacted": 0, "error": str(e)[:120]}

    def concept_content_state(self, course_uid: str, concept_uid: str) -> str:
        """'absent' | 'empty' | 'present' — the distinction a file cannot make.

        `get_concept_content` returns "" for both a concept that was never
        attempted and one that hydrated to nothing. Callers that need to tell
        them apart — a resume path deciding what to build, a QA harness counting
        failures — must use this instead of truthiness on the content.
        """
        try:
            row = self._get_db().execute(
                "SELECT content FROM concepts WHERE course_uid=? AND concept_uid=?",
                (course_uid, concept_uid)).fetchone()
            if row is None:
                return "absent"
            return "present" if (row[0] or "").strip() else "empty"
        except Exception:
            # Without the table we genuinely cannot tell, and saying so beats
            # guessing.
            return "unknown"

    def save_concept_content(self, course_uid: str, concept_uid: str, markdown: str) -> str:
        """Write concept content to SQLite AND mirror it to the .md file.

        Both, deliberately. The database is what everything else joins against —
        the ledger, retained sources, claims and teaching objects are all keyed
        the same way — while the file keeps the content human-readable, greppable
        and exportable without a query. `content_hash` makes drift between the
        two detectable rather than silent.
        """
        content_dir = os.path.join(self.courses_dir, course_uid, "content")
        os.makedirs(content_dir, exist_ok=True)
        path = os.path.join(content_dir, f"{concept_uid}.md")
        with open(path, "w") as f:
            f.write(markdown)

        try:
            import hashlib
            h = hashlib.sha256((markdown or "").encode("utf-8")).hexdigest()[:16]
            title = ""
            try:
                title = (self.get_concept_by_uid(course_uid, concept_uid) or {}).get("title", "")
            except Exception:
                pass
            db = self._get_db()
            db.execute(
                "INSERT OR REPLACE INTO concepts (course_uid, concept_uid, title, "
                "content, content_hash, path, words, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (course_uid, concept_uid, title, markdown, h, path,
                 len((markdown or "").split()), datetime.now().isoformat()))
            try:
                db.execute("DELETE FROM concepts_fts WHERE concept_uid=?",
                           (concept_uid,))
                db.execute("INSERT INTO concepts_fts (concept_uid, course_uid, "
                           "title, content) VALUES (?,?,?,?)",
                           (concept_uid, course_uid, title, markdown))
            except sqlite3.OperationalError:
                pass  # FTS5 unavailable; substring search still works
            db.commit()
        except sqlite3.OperationalError:
            pass  # pre-v15 database
        except Exception as e:
            # The file is already written, so the content is not lost. A failed
            # index write must not cost the caller their content.
            logger.debug(f"concept DB write skipped for {concept_uid}: {e}")

        if self.on_content_saved:
            # Best-effort: a search index that cannot be updated must never
            # cost the caller their content write.
            try:
                concept = self.get_concept_by_uid(course_uid, concept_uid)
                self.on_content_saved(
                    course_uid, concept_uid,
                    (concept or {}).get("title", ""), markdown)
            except Exception as e:
                logger.debug(f"search index update skipped for {concept_uid}: {e}")
        return path

    def get_unit_concepts(self, course_uid: str, unit_uid: str) -> List[dict]:
        """Get all concepts in a specific unit."""
        course = self.get_course(course_uid)
        if not course:
            return []
        for module in course.get("modules", []):
            for unit in module.get("units", []):
                if unit["uid"] == unit_uid:
                    concepts = []
                    for lesson in unit.get("lessons", []):
                        concepts.extend(lesson.get("concepts", []))
                    return concepts
        return []

    def get_course_stats(self, uid: str) -> dict:
        """Count modules, units, lessons, concepts in a course."""
        course = self.get_course(uid)
        if not course:
            return {"modules": 0, "units": 0, "lessons": 0, "concepts": 0}
        m, u, l, c = 0, 0, 0, 0
        for mod in course.get("modules", []):
            m += 1
            for unit in mod.get("units", []):
                u += 1
                for lesson in unit.get("lessons", []):
                    l += 1
                    c += len(lesson.get("concepts", []))
        return {"modules": m, "units": u, "lessons": l, "concepts": c}


class SearchStore:
    """SQLite FTS5 full-text search over concept titles and content.

    Builds and queries a virtual table `concept_fts(concept_uid, course_uid,
    title, content)`. Ranking uses bm25(). The index is rebuilt lazily from the
    course JSON/Markdown sources whenever it is empty so callers don't have to
    manage it explicitly.
    """

    def __init__(self, db_path: str, course_store: "CourseStore"):
        self.db_path = db_path
        self.courses = course_store
        self._db = _ThreadLocalDB(db_path)
        self._available = None  # tri-state: None=unknown, True/False once probed
        self._ensure_table()

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def is_available(self) -> bool:
        """Whether FTS5 is compiled into the runtime SQLite build."""
        if self._available is None:
            self._ensure_table()
        return bool(self._available)

    def _ensure_table(self):
        """Create the FTS5 virtual table if FTS5 is available."""
        if self._available is False:
            return
        conn = self._get_db()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS concept_fts USING fts5("
                "concept_uid, course_uid, title, content)"
            )
            conn.commit()
            self._available = True
        except sqlite3.OperationalError as e:
            # FTS5 not compiled in — callers must fall back to substring search.
            logger.warning(f"FTS5 unavailable, full-text search disabled: {e}")
            self._available = False

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """Turn arbitrary user text into a safe FTS5 MATCH expression.

        Each whitespace-separated token is wrapped in double quotes (escaping
        embedded quotes) so punctuation can't be interpreted as FTS5 operators
        or cause syntax errors. A trailing ``*`` on each term enables prefix
        matching for a friendlier search-as-you-type feel.
        """
        tokens = [t for t in re.split(r"\s+", query.strip()) if t]
        safe_terms = []
        for tok in tokens:
            escaped = tok.replace('"', '""')
            safe_terms.append(f'"{escaped}"*')
        return " ".join(safe_terms)

    def _row_count(self) -> int:
        conn = self._get_db()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM concept_fts").fetchone()
            return row["c"] if row else 0
        except sqlite3.OperationalError:
            return 0

    def index_concept(self, course_uid: str, concept_uid: str,
                      title: str, content: str) -> bool:
        """Insert or replace one concept's row. Called on every content save.

        `concept_fts` is an FTS5 virtual table with no UNIQUE constraint, so
        `INSERT OR REPLACE` would append a duplicate rather than replace —
        the delete has to be explicit or a concept edited five times answers
        the same query five times.
        """
        if not self.is_available() or not concept_uid:
            return False
        try:
            conn = self._get_db()
            conn.execute("DELETE FROM concept_fts WHERE concept_uid = ?", (concept_uid,))
            conn.execute(
                "INSERT INTO concept_fts (concept_uid, course_uid, title, content) "
                "VALUES (?, ?, ?, ?)",
                (concept_uid, course_uid, title or "", content or ""),
            )
            conn.commit()
            return True
        except sqlite3.Error as e:
            logger.debug(f"index_concept failed for {concept_uid}: {e}")
            return False

    def drop_course(self, course_uid: str) -> int:
        """Remove a deleted course's concepts from the index."""
        if not self.is_available() or not course_uid:
            return 0
        try:
            conn = self._get_db()
            cur = conn.execute(
                "DELETE FROM concept_fts WHERE course_uid = ?", (course_uid,))
            conn.commit()
            return cur.rowcount or 0
        except sqlite3.Error as e:
            logger.debug(f"drop_course from index failed for {course_uid}: {e}")
            return 0

    def rebuild_search_index(self) -> int:
        """Walk every course and repopulate the FTS index. Returns row count."""
        if not self.is_available():
            return 0
        conn = self._get_db()
        conn.execute("DELETE FROM concept_fts")
        count = 0
        for course in self.courses.list_courses():
            course_uid = course.get("uid")
            if not course_uid:
                continue
            for concept in self.courses.get_flat_concepts(course_uid):
                concept_uid = concept.get("uid")
                if not concept_uid:
                    continue
                content = self.courses.get_concept_content(course_uid, concept_uid)
                conn.execute(
                    "INSERT INTO concept_fts (concept_uid, course_uid, title, content) "
                    "VALUES (?, ?, ?, ?)",
                    (concept_uid, course_uid, concept.get("title", ""), content or ""),
                )
                count += 1
        conn.commit()
        logger.info(f"Rebuilt concept FTS index with {count} concept(s)")
        return count

    def search(self, query: str, course_uid: str = None, limit: int = 10) -> List[dict]:
        """Full-text search concepts ranked by bm25.

        Returns a list of dicts: {concept_uid, course_uid, title, content}.
        Lazily rebuilds the index if it is currently empty.
        """
        if not self.is_available():
            return []
        if not query or not query.strip():
            return []

        # Lazy population: only walk the corpus when the index is empty.
        if self._row_count() == 0:
            self.rebuild_search_index()

        match_expr = self._sanitize_query(query)
        if not match_expr:
            return []

        conn = self._get_db()
        sql = (
            "SELECT concept_uid, course_uid, title, content "
            "FROM concept_fts WHERE concept_fts MATCH ?"
        )
        params: List[Any] = [match_expr]
        if course_uid:
            sql += " AND course_uid = ?"
            params.append(course_uid)
        sql += " ORDER BY bm25(concept_fts) LIMIT ?"
        params.append(int(limit))

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Optional dense / hybrid retrieval ────────────────────────────────────
    # All methods below are fully guarded: they no-op or return [] when the
    # optional dependency (sqlite-vec) is absent.  The FTS5 `search()` path
    # above is NEVER modified and remains the default.

    @staticmethod
    def is_dense_available() -> bool:
        """Return True only if sqlite-vec is importable.

        Deliberately avoids caching at class level so tests can monkey-patch
        the import without needing module reloads.
        """
        try:
            import sqlite_vec  # noqa: F401
            return True
        except ImportError:
            return False

    def build_dense_index(self, embed_fn: Callable) -> int:
        """Create (or replace) a sqlite-vec vec0 virtual table and populate it.

        Args:
            embed_fn: callable that accepts a list of strings and returns a
                      numpy array or list-of-lists of float32 vectors.

        Returns the number of concepts indexed, or 0 if sqlite-vec is absent.
        """
        if not self.is_dense_available():
            logger.info("build_dense_index: sqlite-vec absent, skipping.")
            return 0

        try:
            import sqlite_vec
            import struct

            conn = self._get_db()
            # Load the sqlite-vec extension into this connection.
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            # Probe embedding dimension with a trivial call.
            probe = embed_fn(["probe"])
            import numpy as np
            probe_arr = np.array(probe, dtype="float32")
            dim = int(probe_arr.shape[-1])

            # Drop and recreate the vec0 table so index is always fresh.
            conn.execute("DROP TABLE IF EXISTS concept_vec")
            conn.execute(
                f"CREATE VIRTUAL TABLE concept_vec USING vec0("
                f"concept_uid TEXT PRIMARY KEY, course_uid TEXT, embedding FLOAT[{dim}])"
            )

            count = 0
            for course in self.courses.list_courses():
                course_uid = course.get("uid")
                if not course_uid:
                    continue
                flat = self.courses.get_flat_concepts(course_uid)
                if not flat:
                    continue
                texts = []
                for c in flat:
                    body = self.courses.get_concept_content(course_uid, c["uid"]) or ""
                    # Was `title + body[:512]`. Measured on a real concept
                    # document, 512 characters reaches Metadata, Learning
                    # Objectives and half of Prerequisites — not one word of the
                    # explanation. Metadata also carries a
                    # `- **Path**: Course > Module > Unit > Lesson` line, so
                    # every concept in a course embedded to nearly the same
                    # vector and dense search ranked by position in the tree
                    # rather than by meaning. index_text() picks the sections
                    # that say what the concept IS.
                    texts.append(concept_index_text(body, c.get("title", "")))

                vecs = np.array(embed_fn(texts), dtype="float32")
                for concept, vec in zip(flat, vecs):
                    blob = struct.pack(f"{dim}f", *vec.tolist())
                    conn.execute(
                        "INSERT OR REPLACE INTO concept_vec(concept_uid, course_uid, embedding) "
                        "VALUES (?, ?, ?)",
                        (concept["uid"], course_uid, blob),
                    )
                    count += 1

            conn.commit()
            logger.info(f"Built dense index with {count} concept(s), dim={dim}")
            return count

        except Exception as e:
            logger.warning(f"build_dense_index failed (dense unavailable): {e}")
            return 0

    def dense_search(
        self,
        query_vec,
        course_uid: str = None,
        limit: int = 10,
    ) -> List[dict]:
        """Top-k cosine/L2 search via sqlite-vec.

        Returns [] if sqlite-vec is absent or the index doesn't exist.
        Each result dict has keys: concept_uid, course_uid, title, content.
        """
        if not self.is_dense_available():
            return []

        try:
            import sqlite_vec
            import struct
            import numpy as np

            conn = self._get_db()
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            # Check that the vec table exists.
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='concept_vec'"
            ).fetchone()
            if not row:
                return []

            vec = np.array(query_vec, dtype="float32").flatten()
            dim = len(vec)
            blob = struct.pack(f"{dim}f", *vec.tolist())

            if course_uid:
                sql = (
                    "SELECT cv.concept_uid, cv.course_uid, "
                    "cf.title, cf.content "
                    "FROM concept_vec cv "
                    "LEFT JOIN concept_fts cf ON cf.concept_uid = cv.concept_uid "
                    "WHERE cv.course_uid = ? "
                    "ORDER BY vec_distance_cosine(cv.embedding, ?) "
                    "LIMIT ?"
                )
                rows = conn.execute(sql, (course_uid, blob, int(limit))).fetchall()
            else:
                sql = (
                    "SELECT cv.concept_uid, cv.course_uid, "
                    "cf.title, cf.content "
                    "FROM concept_vec cv "
                    "LEFT JOIN concept_fts cf ON cf.concept_uid = cv.concept_uid "
                    "ORDER BY vec_distance_cosine(cv.embedding, ?) "
                    "LIMIT ?"
                )
                rows = conn.execute(sql, (blob, int(limit))).fetchall()

            return [dict(r) for r in rows]

        except Exception as e:
            logger.warning(f"dense_search failed, returning []: {e}")
            return []

    def hybrid_search(
        self,
        query: str,
        embed_fn: Callable = None,
        course_uid: str = None,
        limit: int = 10,
        k: int = 60,
    ) -> List[dict]:
        """Fuse FTS5 + dense results via Reciprocal Rank Fusion.

        Always falls back to pure FTS5 when:
        - dense deps (sqlite-vec) are absent, OR
        - embed_fn is not provided, OR
        - any exception occurs in the dense path.

        The FTS5 default is NEVER skipped.  Response shape is identical to
        ``search()``: list of {concept_uid, course_uid, title, content}.
        """
        # Step 1: always run FTS5 — this is the guaranteed base result.
        try:
            fts_results = self.search(query, course_uid=course_uid, limit=limit)
        except Exception as e:
            logger.warning(f"hybrid_search: FTS5 failed: {e}")
            fts_results = []

        # Step 2: attempt dense search only when deps + embed_fn are available.
        dense_results = []
        if embed_fn is not None and self.is_dense_available():
            try:
                query_vec = embed_fn([query])
                import numpy as np
                arr = np.array(query_vec, dtype="float32")
                # embed_fn([text]) → shape (1, dim) or (dim,); normalise to 1-D.
                vec = arr[0] if arr.ndim == 2 else arr.flatten()
                dense_results = self.dense_search(vec, course_uid=course_uid, limit=limit)
            except Exception as e:
                logger.warning(f"hybrid_search: dense path failed, using FTS5 only: {e}")
                dense_results = []

        # Step 3: if no dense results, return FTS5 order unchanged.
        if not dense_results:
            return fts_results

        # Step 4: RRF fusion.
        try:
            from services.common.retrieval import reciprocal_rank_fusion
            fused = reciprocal_rank_fusion(
                [fts_results, dense_results],
                k=k,
                key=lambda r: r.get("concept_uid", ""),
            )
            return [item for item, _score in fused[:limit]]
        except Exception as e:
            logger.warning(f"hybrid_search: RRF fusion failed, falling back to FTS5: {e}")
            return fts_results


class ProgressStore:
    """SQLite user progress per concept, scoped by student_id (B15.3).

    student_id defaults to the legacy student until real auth lands (B15.4);
    passing it explicitly is the multi-tenant path. Every query filters on it.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    # BUG-7: Whitelist of valid column names to prevent SQL injection
    _VALID_COLUMNS = {
        'status', 'grade', 'easiness_factor', 'interval_days', 'repetitions',
        'next_review_date', 'last_review_date', 'times_reviewed', 'times_correct',
        'updated_at', 'concept_uid', 'course_uid', 'bloom_level', 'student_id',
        # FSRS memory state (schema v10)
        'stability', 'difficulty', 'lapses',
    }

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def get_progress(self, concept_uid: str, student_id: str = None) -> Optional[dict]:
        conn = self._get_db()
        row = conn.execute(
            "SELECT * FROM user_progress WHERE concept_uid = ? AND student_id = ?",
            (concept_uid, _sid(student_id))).fetchone()
        return dict(row) if row else None

    def update_progress(self, concept_uid: str, course_uid: str, student_id: str = None, **kwargs):
        """Upsert progress for a concept (keyed on (student_id, concept_uid))."""
        # BUG-7: Validate column names against whitelist
        invalid_keys = set(kwargs.keys()) - self._VALID_COLUMNS
        if invalid_keys:
            logger.warning(f"Rejected invalid column names in update_progress: {invalid_keys}")
            kwargs = {k: v for k, v in kwargs.items() if k in self._VALID_COLUMNS}

        sid = _sid(student_id)
        conn = self._get_db()
        # B5.5: INSERT OR REPLACE rewrites the whole row, so an empty course_uid
        # would orphan existing progress from its course. If the caller didn't
        # supply one (e.g. review-only updates), preserve the existing link.
        if not course_uid:
            existing = conn.execute(
                "SELECT course_uid FROM user_progress WHERE concept_uid = ? AND student_id = ?",
                (concept_uid, sid),
            ).fetchone()
            if existing and existing["course_uid"]:
                course_uid = existing["course_uid"]
        kwargs["updated_at"] = datetime.utcnow().isoformat()
        kwargs["concept_uid"] = concept_uid
        kwargs["course_uid"] = course_uid
        kwargs["student_id"] = sid

        # A TRUE upsert: touch only the columns the caller supplied.
        #
        # This was INSERT OR REPLACE, which deletes the existing row and writes
        # a new one — so every column the caller did NOT pass silently reverted
        # to its default. update_progress(status="completed") erased grade,
        # bloom_level, times_reviewed and the review schedule; the very next
        # call, update_progress(grade=..., status="reviewed"), erased whatever
        # the first one had set. Both are real call sites in fsm_logic.
        #
        # The hazard was already known for one column — the course_uid guard
        # above exists precisely because a blank course_uid orphaned progress —
        # but only that symptom was patched, not the mechanism. FSRS memory
        # state on this table would have been wiped the same way, which is how
        # it was found.
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        updates = ", ".join(f"{c} = excluded.{c}" for c in kwargs
                            if c not in ("concept_uid", "student_id"))
        conn.execute(
            f"INSERT INTO user_progress ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(student_id, concept_uid) DO UPDATE SET {updates}",
            list(kwargs.values()))
        conn.commit()

    def mark_completed(self, concept_uid: str, course_uid: str, student_id: str = None):
        self.update_progress(concept_uid, course_uid, student_id=student_id, status="completed")

    def get_course_progress(self, course_uid: str, student_id: str = None) -> List[dict]:
        conn = self._get_db()
        rows = conn.execute(
            "SELECT * FROM user_progress WHERE course_uid = ? AND student_id = ?",
            (course_uid, _sid(student_id))).fetchall()
        return [dict(r) for r in rows]

    def get_due_reviews(self, target_date: str = None, student_id: str = None) -> List[dict]:
        """Concepts due for review on or before target_date.

        THIS USED TO ALWAYS RETURN NOTHING. It read only
        `user_progress.next_review_date`, and no code path in the system ever
        writes that column — reviews are scheduled into `scheduled_reviews` by
        ScheduleStore.schedule_concept_review(). The column stayed NULL, and
        `NULL <= '<date>'` is NULL in SQL, so every row was filtered out.
        Verified against a real DB: two scheduled reviews present, zero due
        reported, even with target_date set to 2099.

        The visible effect was that the FSM's spoken review mode answered "No
        cards due for review right now" no matter how much the learner had
        studied, and the parent dashboard reported a permanent zero. The web
        review page was unaffected because librarian's /api/due_concepts
        already unions both sources — this brings the storage layer in line
        with the behaviour that endpoint had to implement for itself.

        Both sources are returned, deduped on concept_uid, progress rows first
        (they carry grade/bloom history that a bare schedule row does not).
        """
        if not target_date:
            target_date = date.today().isoformat()
        conn = self._get_db()
        sid = _sid(student_id)

        out, seen = [], set()
        for r in conn.execute(
            "SELECT * FROM user_progress WHERE next_review_date IS NOT NULL "
            "AND next_review_date <= ? AND status != 'locked' AND student_id = ?",
            (target_date, sid)
        ).fetchall():
            d = dict(r)
            if d.get("concept_uid") and d["concept_uid"] not in seen:
                seen.add(d["concept_uid"])
                out.append(d)

        # scheduled_reviews stores the concept uid in `unit_uid` — the table
        # predates concept-level scheduling and was reused rather than renamed.
        for r in conn.execute(
            "SELECT * FROM scheduled_reviews WHERE scheduled_date <= ? "
            "AND COALESCE(status, 'pending') != 'completed' AND student_id = ? "
            # Earliest first, so a concept with several pending reviews is
            # surfaced at its most overdue date rather than an arbitrary one.
            "ORDER BY scheduled_date ASC, review_number ASC",
            (target_date, sid)
        ).fetchall():
            d = dict(r)
            uid = d.get("unit_uid")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            out.append({
                "concept_uid": uid,
                "course_uid": d.get("course_uid"),
                "title": d.get("unit_title", ""),
                "next_review_date": d.get("scheduled_date"),
                "status": d.get("status") or "pending",
                "source": "scheduled_review",
            })
        return out

    def get_completion_percentage(self, course_uid: str, total_concepts: int, student_id: str = None) -> float:
        """Calculate completion percentage for a course."""
        conn = self._get_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM user_progress WHERE course_uid = ? AND status = 'completed' AND student_id = ?",
            (course_uid, _sid(student_id))
        ).fetchone()
        completed = row["cnt"] if row else 0
        return (completed / total_concepts * 100) if total_concepts > 0 else 0


class FlashcardStore:
    """SQLite user flashcards tracking, scoped by student_id (B15.3)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    # BUG-7: Whitelist of valid column names for flashcard updates
    _VALID_COLUMNS = {
        'status', 'next_review_date', 'easiness_factor', 'interval_days',
        'repetitions', 'updated_at', 'front', 'back',
        'stability', 'difficulty', 'last_review_date', 'lapses', 'source',
        'student_id'
    }

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def add_card(self, course_uid: str, concept_uid: str, front: str, back: str,
                 student_id: str = None) -> str:
        conn = self._get_db()
        uid = f"card_{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO flashcards (uid, course_uid, concept_uid, front, back, student_id) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, course_uid, concept_uid, front, back, _sid(student_id))
        )
        conn.commit()
        return uid

    def get_due_cards(self, course_uid: str = None, target_date: str = None,
                      student_id: str = None) -> List[dict]:
        if not target_date:
            target_date = date.today().isoformat()
        conn = self._get_db()
        query = ("SELECT * FROM flashcards WHERE (next_review_date <= ? OR next_review_date IS NULL) "
                 "AND status != 'suspended' AND student_id = ?")
        params = [target_date, _sid(student_id)]
        if course_uid:
            query += " AND course_uid = ?"
            params.append(course_uid)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def update_card(self, uid: str, student_id: str = None, **kwargs):
        # BUG-7: Validate column names against whitelist
        invalid_keys = set(kwargs.keys()) - self._VALID_COLUMNS
        if invalid_keys:
            logger.warning(f"Rejected invalid column names in update_card: {invalid_keys}")
            kwargs = {k: v for k, v in kwargs.items() if k in self._VALID_COLUMNS}

        conn = self._get_db()
        kwargs["updated_at"] = datetime.utcnow().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [uid, _sid(student_id)]
        conn.execute(f"UPDATE flashcards SET {sets} WHERE uid = ? AND student_id = ?", vals)
        conn.commit()

    def grade_card_fsrs(self, uid: str, rating: int, fsrs_engine, student_id: str = None) -> dict:
        """Grade a card using FSRS algorithm and update all scheduling fields.

        Args:
            uid: Card UID
            rating: 1=Again, 2=Hard, 3=Good, 4=Easy
            fsrs_engine: FSRSEngine instance

        Returns:
            Dict with new scheduling info (interval, next_review_date, stability, etc.)
        """
        conn = self._get_db()
        row = conn.execute("SELECT * FROM flashcards WHERE uid = ? AND student_id = ?",
                           (uid, _sid(student_id))).fetchone()
        if not row:
            raise ValueError(f"Card {uid} not found")

        card = dict(row)
        rating = max(1, min(4, int(rating)))

        # Calculate days elapsed since last review
        days_elapsed = 0
        if card.get("last_review_date"):
            try:
                last = date.fromisoformat(card["last_review_date"])
                days_elapsed = max(0, (date.today() - last).days)
            except (ValueError, TypeError):
                days_elapsed = card.get("interval_days", 0) or 0

        # Run FSRS algorithm
        new_stability, new_difficulty = fsrs_engine.calculate_memory(
            stability=card.get("stability"),
            difficulty=card.get("difficulty"),
            rating=rating,
            days_elapsed=days_elapsed,
        )
        new_interval = fsrs_engine.next_interval(new_stability)

        # Update repetition count and lapses
        reps = (card.get("repetitions") or 0) + 1
        lapses = card.get("lapses") or 0
        if rating == 1:  # Again — lapse
            lapses += 1
            new_interval = 1  # Re-learn: show again tomorrow

        next_review = (date.today() + timedelta(days=new_interval)).isoformat()
        now = datetime.utcnow().isoformat()

        conn.execute("""
            UPDATE flashcards SET
                stability = ?, difficulty = ?, interval_days = ?,
                next_review_date = ?, last_review_date = ?,
                repetitions = ?, lapses = ?, status = 'review',
                updated_at = ?
            WHERE uid = ? AND student_id = ?
        """, (new_stability, new_difficulty, new_interval,
              next_review, date.today().isoformat(),
              reps, lapses, now, uid, _sid(student_id)))
        conn.commit()

        retention = fsrs_engine.get_retention(new_stability, 0)

        return {
            "uid": uid,
            "rating": rating,
            "interval_days": new_interval,
            "next_review_date": next_review,
            "stability": round(new_stability, 4),
            "difficulty": round(new_difficulty, 4),
            "repetitions": reps,
            "lapses": lapses,
            "retention": round(retention, 4),
        }

    def get_review_stats(self, course_uid: str = None, student_id: str = None) -> dict:
        """Get aggregated review statistics for the schedule view."""
        conn = self._get_db()
        today = date.today().isoformat()
        base_where = "WHERE status != 'suspended' AND student_id = ?"
        params = [_sid(student_id)]
        if course_uid:
            base_where += " AND course_uid = ?"
            params.append(course_uid)

        # Due today or overdue
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM flashcards {base_where} AND (next_review_date <= ? OR next_review_date IS NULL)",
            params + [today]
        ).fetchone()
        due_count = row["cnt"] if row else 0

        # Due in next 7 days
        week = (date.today() + timedelta(days=7)).isoformat()
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM flashcards {base_where} AND next_review_date > ? AND next_review_date <= ?",
            params + [today, week]
        ).fetchone()
        upcoming_count = row["cnt"] if row else 0

        # Average retention (for cards with stability)
        rows = conn.execute(
            f"SELECT stability, last_review_date FROM flashcards {base_where} AND stability IS NOT NULL",
            params
        ).fetchall()
        if rows:
            total_ret = 0
            for r in rows:
                elapsed = 0
                if r["last_review_date"]:
                    try:
                        elapsed = max(0, (date.today() - date.fromisoformat(r["last_review_date"])).days)
                    except (ValueError, TypeError):
                        pass
                s = r["stability"] or 1.0
                DECAY = -0.5
                FACTOR = 19.0 / 81.0
                ret = (1 + FACTOR * elapsed / s) ** DECAY if s > 0 else 0
                total_ret += ret
            avg_retention = round(total_ret / len(rows) * 100)
        else:
            avg_retention = 0

        # Cards by next_review_date for calendar view
        card_dates = conn.execute(
            f"SELECT next_review_date, COUNT(*) as cnt FROM flashcards {base_where} AND next_review_date IS NOT NULL GROUP BY next_review_date ORDER BY next_review_date",
            params
        ).fetchall()
        calendar = {r["next_review_date"]: r["cnt"] for r in card_dates}

        return {
            "due_today": due_count,
            "upcoming_7d": upcoming_count,
            "avg_retention": avg_retention,
            "calendar": calendar,
            "total_cards": due_count + upcoming_count,
        }

    def get_cards_for_concept(self, concept_uid: str, student_id: str = None) -> List[dict]:
        conn = self._get_db()
        rows = conn.execute("SELECT * FROM flashcards WHERE concept_uid = ? AND student_id = ?",
                            (concept_uid, _sid(student_id))).fetchall()
        return [dict(r) for r in rows]

    def get_cards_for_course(self, course_uid: str, student_id: str = None) -> List[dict]:
        conn = self._get_db()
        rows = conn.execute("SELECT * FROM flashcards WHERE course_uid = ? AND student_id = ?",
                            (course_uid, _sid(student_id))).fetchall()
        return [dict(r) for r in rows]


class ActivityStore:
    """SQLite activity logging."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def log_activity(self, course_uid: str, activity_type: str,
                     concept_uid: str = None, unit_uid: str = None,
                     duration_seconds: int = 0, grade: int = None,
                     details: dict = None, student_id: str = None):
        conn = self._get_db()
        conn.execute(
            "INSERT INTO activity_log (course_uid, concept_uid, unit_uid, activity_type, duration_seconds, grade, details, student_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (course_uid, concept_uid, unit_uid, activity_type, duration_seconds, grade,
             json.dumps(details) if details else None, _sid(student_id))
        )
        conn.commit()

    def get_activities(self, start_date: str = None, end_date: str = None,
                       course_uid: str = None, activity_type: str = None,
                       student_id: str = None) -> List[dict]:
        conn = self._get_db()
        query = "SELECT * FROM activity_log WHERE student_id = ?"
        params = [_sid(student_id)]
        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date + "T23:59:59")
        if course_uid:
            query += " AND course_uid = ?"
            params.append(course_uid)
        if activity_type:
            query += " AND activity_type = ?"
            params.append(activity_type)
        # created_at is CURRENT_TIMESTAMP, which has one-second granularity, so
        # two rows written in the same second tie and their order is whatever
        # SQLite happens to return. id breaks the tie by true insertion order —
        # callers that ask for "the most recent" need that to be answerable.
        query += " ORDER BY created_at DESC, id DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_daily_summary(self, target_date: str = None, student_id: str = None) -> dict:
        if not target_date:
            # Matches DATE(created_at) below, which SQLite evaluates in UTC.
            # See utc_today() — a local date here loses or double-counts the
            # activity logged between UTC midnight and local midnight.
            target_date = utc_today().isoformat()
        conn = self._get_db()
        rows = conn.execute(
            "SELECT activity_type, COUNT(*) as cnt, SUM(duration_seconds) as total_time "
            "FROM activity_log WHERE DATE(created_at) = ? AND student_id = ? GROUP BY activity_type",
            (target_date, _sid(student_id))
        ).fetchall()
        summary = {}
        for r in rows:
            summary[r["activity_type"]] = {"count": r["cnt"], "total_seconds": r["total_time"] or 0}
        return summary

    def get_streak(self, student_id: str = None) -> int:
        """Consecutive days with activity, ending today (or yesterday if today
        isn't logged yet). Walks distinct activity days newest-first against a
        decreasing anchor so a gap correctly ends the streak (the old version
        applied the 'today missing' tolerance on every row and over-counted
        across gaps)."""
        conn = self._get_db()
        rows = conn.execute(
            "SELECT DISTINCT DATE(created_at) as day FROM activity_log WHERE student_id = ? ORDER BY day DESC",
            (_sid(student_id),)
        ).fetchall()
        days = [date.fromisoformat(r["day"]) for r in rows if r["day"]]
        if not days:
            return 0
        # created_at defaults to SQLite CURRENT_TIMESTAMP, which is UTC, so the
        # anchor must be UTC too. Comparing UTC-stored days against a LOCAL
        # date.today() silently broke every user's streak for the window
        # between UTC midnight and local midnight — 6 hours a day in MDT, and
        # up to 12+ in other zones. Verified: at 18:06 MDT SQLite reports
        # 2026-08-03 while date.today() reports 2026-08-02, so the most recent
        # activity looked like it was in the future and the streak returned 0.
        today = utc_today()
        if days[0] == today:
            anchor = today
        elif days[0] == today - timedelta(days=1):
            anchor = today - timedelta(days=1)  # today not logged yet
        else:
            return 0  # most recent activity is older than yesterday — streak broken
        streak = 0
        for day in days:
            if day == anchor:
                streak += 1
                anchor -= timedelta(days=1)
            else:
                break
        return streak


class ScheduleStore:
    """SQLite scheduled reviews (SM-2 intervals)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def schedule_unit_reviews(self, course_uid: str, unit_uid: str,
                               unit_title: str, start_date: str,
                               intervals: List[int] = None, student_id: str = None):
        """DEPRECATED: Use FSRS-based flashcard scheduling instead.
        Create scheduled review entries for a unit."""
        if intervals is None:
            intervals = [1, 3, 7, 16, 35]
        conn = self._get_db()
        base = date.fromisoformat(start_date)
        for i, days in enumerate(intervals, 1):
            review_date = (base + timedelta(days=days)).isoformat()
            conn.execute(
                "INSERT INTO scheduled_reviews (course_uid, unit_uid, unit_title, scheduled_date, review_number, student_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (course_uid, unit_uid, unit_title, review_date, i, _sid(student_id))
            )
        conn.commit()
        logger.info(f"Scheduled {len(intervals)} reviews for unit {unit_title}")

    # Fallback intervals, used only when the FSRS engine cannot be constructed.
    # Previously this table WAS the scheduler; it is now the degraded path.
    _FALLBACK_INTERVALS = {1: [1, 3], 2: [2, 7], 3: [3, 14], 4: [7, 30]}

    def schedule_concept_review(self, course_uid: str, concept_uid: str,
                                 concept_title: str, rating: int = 3,
                                 student_id: str = None):
        """Schedule a concept's next review from its FSRS memory state.

        This used to be a fixed grade→interval table: grade 4 always meant
        [7, 30] days, whether it was the learner's first encounter with the
        concept or their tenth consecutive correct recall. The schedule ignored
        review history entirely, which is the difference between spaced
        repetition and a reminder — and it did so while the FSRS engine, with
        48 passing tests, was already scheduling flashcards properly.

        Now the concept's stability/difficulty are read from user_progress,
        advanced through the engine, and written back, so the interval grows
        with demonstrated retention. A concept with no memory state yet takes
        the engine's own first-review path.

        Writes ONE row. The old table wrote two rows per call (a review and a
        follow-up), which is how a single answer created a small pile of
        pending reviews; under FSRS the next interval is computed at the next
        review, from what the learner actually does then.
        """
        rating = min(max(int(rating), 1), 4)
        sid = _sid(student_id)
        conn = self._get_db()
        try:
            engine, days = None, None
            try:
                from services.core.fsrs_engine import FSRSEngine
                engine = FSRSEngine()
            except Exception as e:
                logger.warning(
                    f"FSRS engine unavailable, falling back to fixed intervals: {e}")

            if engine is not None:
                row = conn.execute(
                    "SELECT stability, difficulty, lapses, last_review_date "
                    "FROM user_progress WHERE concept_uid = ? AND student_id = ?",
                    (concept_uid, sid)
                ).fetchone()
                prior = dict(row) if row else {}

                elapsed = 0
                if prior.get("last_review_date"):
                    try:
                        elapsed = max(0, (date.today() - date.fromisoformat(
                            prior["last_review_date"])).days)
                    except (ValueError, TypeError):
                        elapsed = 0

                stability, difficulty = engine.calculate_memory(
                    stability=prior.get("stability"),
                    difficulty=prior.get("difficulty"),
                    rating=rating,
                    days_elapsed=elapsed,
                )
                days = engine.next_interval(stability)
                lapses = (prior.get("lapses") or 0)
                if rating == 1:
                    # A lapse comes back tomorrow regardless of prior stability,
                    # matching the flashcard path.
                    days, lapses = 1, lapses + 1

                # Persist the memory state, or the next review recomputes from
                # scratch and the whole point is lost. UPSERT because a concept
                # can be graded before any progress row exists for it.
                conn.execute(
                    "INSERT INTO user_progress "
                    "(student_id, concept_uid, course_uid, status, grade, "
                    " stability, difficulty, lapses, last_review_date, "
                    " next_review_date, interval_days, times_reviewed, "
                    " times_correct) "
                    "VALUES (?, ?, ?, 'reviewed', ?, ?, ?, ?, ?, ?, ?, 1, ?) "
                    "ON CONFLICT(student_id, concept_uid) DO UPDATE SET "
                    "  stability = excluded.stability, "
                    "  difficulty = excluded.difficulty, "
                    "  lapses = excluded.lapses, "
                    "  grade = excluded.grade, "
                    "  last_review_date = excluded.last_review_date, "
                    "  next_review_date = excluded.next_review_date, "
                    "  interval_days = excluded.interval_days, "
                    "  times_reviewed = COALESCE(user_progress.times_reviewed, 0) + 1, "
                    # Accuracy — the field that answers "what do I actually
                    # know?". It was never written by anything: the only code
                    # computing it lived in the SM-2 module, which has zero
                    # callers, so it read 0 forever and any progress surface
                    # built on it would have shown a flat zero.
                    "  times_correct = COALESCE(user_progress.times_correct, 0) + ?, "
                    "  updated_at = CURRENT_TIMESTAMP",
                    (sid, concept_uid, course_uid, rating,
                     stability, difficulty, lapses,
                     date.today().isoformat(),
                     (date.today() + timedelta(days=days)).isoformat(), days,
                     1 if rating >= 3 else 0,
                     1 if rating >= 3 else 0)
                )

            base = date.today()
            offsets = ([days] if days is not None
                       else self._FALLBACK_INTERVALS.get(rating, [3, 14]))
            for i, offset in enumerate(offsets, 1):
                conn.execute(
                    "INSERT INTO scheduled_reviews (course_uid, unit_uid, unit_title, scheduled_date, review_number, student_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (course_uid, concept_uid, concept_title,
                     (base + timedelta(days=offset)).isoformat(), i, sid)
                )
            conn.commit()
            logger.info(
                f"Scheduled concept review for '{concept_title}' "
                f"(grade {rating}): +{offsets} days"
                + ("" if days is not None else " [FALLBACK: no FSRS]"))
        except Exception as e:
            logger.warning(f"Failed to schedule concept review: {e}")

    def get_scheduled_reviews(self, start_date: str = None, end_date: str = None,
                               course_uid: str = None, student_id: str = None) -> List[dict]:
        conn = self._get_db()
        query = "SELECT * FROM scheduled_reviews WHERE student_id = ?"
        params = [_sid(student_id)]
        if start_date:
            query += " AND scheduled_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND scheduled_date <= ?"
            params.append(end_date)
        if course_uid:
            query += " AND course_uid = ?"
            params.append(course_uid)
        query += " ORDER BY scheduled_date ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def complete_review(self, review_id: int, student_id: str = None):
        conn = self._get_db()
        conn.execute(
            "UPDATE scheduled_reviews SET status = 'completed', completed_at = ? WHERE id = ? AND student_id = ?",
            (datetime.utcnow().isoformat(), review_id, _sid(student_id))
        )
        conn.commit()

    def reschedule_review(self, review_id: int, new_date: str, student_id: str = None):
        conn = self._get_db()
        conn.execute(
            "UPDATE scheduled_reviews SET scheduled_date = ?, status = 'pending' WHERE id = ? AND student_id = ?",
            (new_date, review_id, _sid(student_id))
        )
        conn.commit()

    def mark_overdue(self, student_id: str = None):
        """Mark past pending reviews as overdue."""
        today = date.today().isoformat()
        conn = self._get_db()
        conn.execute(
            "UPDATE scheduled_reviews SET status = 'overdue' WHERE scheduled_date < ? AND status = 'pending' AND student_id = ?",
            (today, _sid(student_id))
        )
        conn.commit()

    def get_upcoming_count(self, days: int = 7, student_id: str = None) -> int:
        """Count reviews scheduled in the next N days."""
        today = date.today()
        end = (today + timedelta(days=days)).isoformat()
        conn = self._get_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM scheduled_reviews WHERE scheduled_date BETWEEN ? AND ? "
            "AND status IN ('pending', 'overdue') AND student_id = ?",
            (today.isoformat(), end, _sid(student_id))
        ).fetchone()
        return row["cnt"] if row else 0


class SettingsStore:
    """SQLite user settings key-value store."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def get(self, key: str, default: str = None) -> Optional[str]:
        conn = self._get_db()
        row = conn.execute("SELECT value FROM user_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str):
        conn = self._get_db()
        conn.execute(
            "INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()

    def get_all(self) -> dict:
        conn = self._get_db()
        rows = conn.execute("SELECT key, value FROM user_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


class AccountStore:
    """Parents + students CRUD (B15.1). parents.id / students.id are the
    principals — there is no separate users table (spec 03 §1)."""

    _PARENT_COLUMNS = {'email', 'password_hash', 'display_name', 'status',
                       'email_verified_at', 'updated_at'}
    _STUDENT_COLUMNS = {'display_name', 'pin_hash', 'grade_band', 'grade_numeric',
                        'avatar_url', 'interests', 'settings', 'status', 'updated_at'}

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    # -- parents ------------------------------------------------------------
    def create_parent(self, email: str, password_hash: str,
                      display_name: str = None, status: str = 'pending_verify') -> str:
        pid = f"par_{uuid.uuid4().hex[:8]}"
        conn = self._get_db()
        conn.execute(
            "INSERT INTO parents (id, email, password_hash, display_name, status) VALUES (?, ?, ?, ?, ?)",
            (pid, email.strip().lower(), password_hash, display_name, status))
        conn.commit()
        return pid

    def get_parent(self, parent_id: str) -> Optional[dict]:
        row = self._get_db().execute("SELECT * FROM parents WHERE id = ?", (parent_id,)).fetchone()
        return dict(row) if row else None

    def get_parent_by_email(self, email: str) -> Optional[dict]:
        row = self._get_db().execute(
            "SELECT * FROM parents WHERE email = ?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None

    def update_parent(self, parent_id: str, **kwargs):
        kwargs = {k: v for k, v in kwargs.items() if k in self._PARENT_COLUMNS}
        if not kwargs:
            return
        kwargs['updated_at'] = datetime.utcnow().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._get_db()
        conn.execute(f"UPDATE parents SET {sets} WHERE id = ?", list(kwargs.values()) + [parent_id])
        conn.commit()

    # -- students -----------------------------------------------------------
    def create_student(self, parent_id: str, display_name: str,
                       grade_band: str = '6-8', grade_numeric: int = None,
                       pin_hash: str = None, interests: list = None,
                       settings: dict = None) -> str:
        sid = f"stu_{uuid.uuid4().hex[:8]}"
        conn = self._get_db()
        conn.execute(
            "INSERT INTO students (id, parent_id, display_name, grade_band, grade_numeric, pin_hash, interests, settings) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, parent_id, display_name, grade_band, grade_numeric, pin_hash,
             json.dumps(interests or []), json.dumps(settings or {})))
        conn.commit()
        return sid

    def get_student(self, student_id: str) -> Optional[dict]:
        row = self._get_db().execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        return dict(row) if row else None

    def list_students(self, parent_id: str, include_archived: bool = False) -> List[dict]:
        q = "SELECT * FROM students WHERE parent_id = ?"
        if not include_archived:
            q += " AND status = 'active'"
        rows = self._get_db().execute(q + " ORDER BY created_at, rowid",
                                      (parent_id,)).fetchall()
        return [dict(r) for r in rows]

    def count_active_students(self, parent_id: str) -> int:
        row = self._get_db().execute(
            "SELECT COUNT(*) AS cnt FROM students WHERE parent_id = ? AND status = 'active'",
            (parent_id,)).fetchone()
        return row["cnt"] if row else 0

    def update_student(self, student_id: str, **kwargs):
        kwargs = {k: v for k, v in kwargs.items() if k in self._STUDENT_COLUMNS}
        if not kwargs:
            return
        for jkey in ('interests', 'settings'):
            if jkey in kwargs and not isinstance(kwargs[jkey], str):
                kwargs[jkey] = json.dumps(kwargs[jkey])
        kwargs['updated_at'] = datetime.utcnow().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._get_db()
        conn.execute(f"UPDATE students SET {sets} WHERE id = ?", list(kwargs.values()) + [student_id])
        conn.commit()

    def owns_student(self, parent_id: str, student_id: str) -> bool:
        """Cross-tenant guard (spec 03 §8.1)."""
        row = self._get_db().execute(
            "SELECT 1 FROM students WHERE id = ? AND parent_id = ?",
            (student_id, parent_id)).fetchone()
        return row is not None


class EnrollmentStore:
    """Student↔course enrollments (B15.1; elective approval lands with B19.3)."""

    _VALID_COLUMNS = {'current_concept_uid', 'status', 'approved_by', 'approved_at',
                      'course_kind'}

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def enroll(self, student_id: str, course_uid: str,
               course_kind: str = 'catalog', status: str = 'active') -> str:
        eid = f"enr_{uuid.uuid4().hex[:8]}"
        conn = self._get_db()
        conn.execute(
            "INSERT OR IGNORE INTO enrollments (id, student_id, course_uid, course_kind, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, _sid(student_id), course_uid, course_kind, status))
        conn.commit()
        return eid

    def get(self, student_id: str, course_uid: str) -> Optional[dict]:
        row = self._get_db().execute(
            "SELECT * FROM enrollments WHERE student_id = ? AND course_uid = ?",
            (_sid(student_id), course_uid)).fetchone()
        return dict(row) if row else None

    def list_for_student(self, student_id: str, status: str = None) -> List[dict]:
        q = "SELECT * FROM enrollments WHERE student_id = ?"
        params = [_sid(student_id)]
        if status:
            q += " AND status = ?"
            params.append(status)
        rows = self._get_db().execute(q + " ORDER BY enrolled_at", params).fetchall()
        return [dict(r) for r in rows]

    def update(self, student_id: str, course_uid: str, **kwargs):
        kwargs = {k: v for k, v in kwargs.items() if k in self._VALID_COLUMNS}
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._get_db()
        conn.execute(
            f"UPDATE enrollments SET {sets} WHERE student_id = ? AND course_uid = ?",
            list(kwargs.values()) + [_sid(student_id), course_uid])
        conn.commit()


class ConsentStore:
    """COPPA/TOS/privacy consent audit trail (B15.1; consumed by B21)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def record(self, parent_id: str, consent_type: str, granted: bool,
               policy_version: str, student_id: str = None,
               method: str = None, ip_address: str = None) -> str:
        cid = f"cns_{uuid.uuid4().hex[:8]}"
        conn = self._get_db()
        conn.execute(
            "INSERT INTO consent_records (id, parent_id, student_id, consent_type, granted, policy_version, method, ip_address) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, parent_id, student_id, consent_type, 1 if granted else 0,
             policy_version, method, ip_address))
        conn.commit()
        return cid

    def has_consent(self, parent_id: str, consent_type: str, student_id: str = None) -> bool:
        """Latest record for this (parent, type[, student]) wins."""
        q = ("SELECT granted FROM consent_records WHERE parent_id = ? AND consent_type = ?"
             + (" AND student_id = ?" if student_id else " AND student_id IS NULL")
             + " ORDER BY created_at DESC, rowid DESC LIMIT 1")
        params = [parent_id, consent_type] + ([student_id] if student_id else [])
        row = self._get_db().execute(q, params).fetchone()
        return bool(row and row["granted"])

    def list_for_parent(self, parent_id: str) -> List[dict]:
        rows = self._get_db().execute(
            "SELECT * FROM consent_records WHERE parent_id = ? ORDER BY created_at DESC",
            (parent_id,)).fetchall()
        return [dict(r) for r in rows]


class FsmSessionStore:
    """Per-student FSM session blob (B15.7). Single-row upsert in WAL — atomic
    by construction, replacing the global data/user_state.json file."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def upsert(self, student_id: str, blob: str):
        conn = self._get_db()
        conn.execute(
            "INSERT OR REPLACE INTO fsm_sessions (student_id, blob, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (_sid(student_id), blob))
        conn.commit()

    def get(self, student_id: str) -> Optional[dict]:
        row = self._get_db().execute(
            "SELECT * FROM fsm_sessions WHERE student_id = ?", (_sid(student_id),)).fetchone()
        return dict(row) if row else None

    def delete(self, student_id: str):
        conn = self._get_db()
        conn.execute("DELETE FROM fsm_sessions WHERE student_id = ?", (_sid(student_id),))
        conn.commit()


class StandardsStore:
    """Utah standards + concept↔standard tagging (B16.1). Global, read-only
    to students; written only by the standards loader and the catalog admin
    job — never in the student request path."""

    _VALID_COLUMNS = {'code', 'subject', 'grade_band', 'grade_numeric', 'strand',
                      'text', 'is_enrichment', 'source', 'adopted_year'}
    SUBJECTS = {'math', 'ela', 'science', 'social_studies', 'world_lang',
                'health', 'cs', 'financial_lit', 'library_media'}
    COVERAGE = {'full', 'partial', 'enrichment'}

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def upsert(self, code: str, subject: str, strand: str, text: str,
               grade_band: str = None, grade_numeric: int = None,
               is_enrichment: bool = False, source: str = 'USBE',
               adopted_year: int = None) -> bool:
        """Idempotent upsert keyed on the Utah code. Returns True if a new
        row was inserted, False if an existing one was replaced."""
        if subject not in self.SUBJECTS:
            raise ValueError(f"unknown subject {subject!r}")
        if not code or not strand or not text:
            raise ValueError("code, strand and text are required")
        conn = self._get_db()
        existed = conn.execute("SELECT 1 FROM standards WHERE code = ?", (code,)).fetchone()
        conn.execute(
            "INSERT OR REPLACE INTO standards (code, subject, grade_band, grade_numeric, "
            "strand, text, is_enrichment, source, adopted_year) VALUES (?,?,?,?,?,?,?,?,?)",
            (code, subject, grade_band, grade_numeric, strand, text,
             1 if is_enrichment else 0, source, adopted_year))
        conn.commit()
        return existed is None

    def get(self, code: str) -> Optional[dict]:
        row = self._get_db().execute("SELECT * FROM standards WHERE code = ?", (code,)).fetchone()
        return dict(row) if row else None

    def list(self, subject: str = None, grade_band: str = None,
             include_enrichment: bool = True) -> List[dict]:
        q = "SELECT * FROM standards WHERE 1=1"
        params = []
        if subject:
            q += " AND subject = ?"
            params.append(subject)
        if grade_band:
            q += " AND grade_band = ?"
            params.append(grade_band)
        if not include_enrichment:
            q += " AND is_enrichment = 0"
        rows = self._get_db().execute(q + " ORDER BY code", params).fetchall()
        return [dict(r) for r in rows]

    def delete(self, code: str) -> bool:
        """Delete a retired code. Refuses when concept_standards rows point at
        it (would orphan published content)."""
        conn = self._get_db()
        used = conn.execute("SELECT 1 FROM concept_standards WHERE standard_code = ? LIMIT 1",
                            (code,)).fetchone()
        if used:
            return False
        conn.execute("DELETE FROM standards WHERE code = ?", (code,))
        conn.commit()
        return True

    # -- concept tagging ------------------------------------------------------

    def sync_concept_standards(self, concept_uids: List[str], mappings: List[dict]):
        """Delete-then-insert the concept_standards rows for one course's
        concepts. `mappings` = [{concept_uid, standard_code, coverage}]. Only
        the catalog admin job calls this."""
        conn = self._get_db()
        if concept_uids:
            ph = ",".join("?" for _ in concept_uids)
            conn.execute(f"DELETE FROM concept_standards WHERE concept_uid IN ({ph})",
                         concept_uids)
        for m in mappings:
            coverage = m.get("coverage", "full")
            if coverage not in self.COVERAGE:
                raise ValueError(f"invalid coverage {coverage!r}")
            conn.execute(
                "INSERT OR REPLACE INTO concept_standards (concept_uid, standard_code, coverage) "
                "VALUES (?, ?, ?)",
                (m["concept_uid"], m["standard_code"], coverage))
        conn.commit()

    def standards_for_concept(self, concept_uid: str) -> List[dict]:
        rows = self._get_db().execute(
            "SELECT cs.standard_code, cs.coverage, s.strand, s.subject, s.text "
            "FROM concept_standards cs JOIN standards s ON s.code = cs.standard_code "
            "WHERE cs.concept_uid = ?", (concept_uid,)).fetchall()
        return [dict(r) for r in rows]

    def concept_is_health_strand6(self, concept_uid: str) -> bool:
        """B21.4: a concept is HD-gated iff any linked standard is Health /
        Human Development. Single source of truth for the consent gate."""
        row = self._get_db().execute(
            "SELECT 1 FROM concept_standards cs JOIN standards s ON s.code = cs.standard_code "
            "WHERE cs.concept_uid = ? AND s.subject = 'health' "
            "AND s.strand = 'Human Development' LIMIT 1",
            (concept_uid,)).fetchone()
        return row is not None

    def concepts_for_standard(self, standard_code: str) -> List[dict]:
        rows = self._get_db().execute(
            "SELECT concept_uid, coverage FROM concept_standards WHERE standard_code = ?",
            (standard_code,)).fetchall()
        return [dict(r) for r in rows]

    def coverage_report(self, subject: str = None) -> List[dict]:
        """Per-standard count of tagged concepts (B26.4 audit input)."""
        q = ("SELECT s.code, s.subject, s.grade_band, s.strand, s.is_enrichment, "
             "COUNT(cs.concept_uid) AS concept_count "
             "FROM standards s LEFT JOIN concept_standards cs ON cs.standard_code = s.code ")
        params = []
        if subject:
            q += "WHERE s.subject = ? "
            params.append(subject)
        q += "GROUP BY s.code ORDER BY s.subject, s.code"
        rows = self._get_db().execute(q, params).fetchall()
        return [dict(r) for r in rows]


class ExamStore:
    """Exams, attempts, and item responses (B18, spec 01 §5). Attempts and
    responses are student-scoped; exam definitions are global."""

    _ATTEMPT_COLUMNS = {'status', 'score', 'passed', 'theme', 'accommodations',
                        'submitted_at'}
    _RESPONSE_COLUMNS = {'response', 'grade', 'is_correct'}

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    # -- exam definitions -----------------------------------------------------

    def create_exam(self, kind: str, blueprint: dict, course_uid: str = None,
                    scope_uid: str = None, pass_threshold: float = 0.8) -> str:
        codes = sorted({s.get("standard_code") for s in blueprint.get("slots", [])
                        if s.get("standard_code")})
        eid = f"exm_{uuid.uuid4().hex[:8]}"
        conn = self._get_db()
        conn.execute(
            "INSERT INTO exams (id, course_uid, scope_uid, kind, standard_codes, blueprint, pass_threshold) "
            "VALUES (?,?,?,?,?,?,?)",
            (eid, course_uid, scope_uid, kind, json.dumps(codes),
             json.dumps(blueprint), pass_threshold))
        conn.commit()
        return eid

    def get_exam(self, exam_id: str) -> Optional[dict]:
        row = self._get_db().execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["blueprint"] = json.loads(d["blueprint"] or "{}")
        d["standard_codes"] = json.loads(d["standard_codes"] or "[]")
        return d

    def list_exams(self, course_uid: str = None, scope_uid: str = None,
                   kind: str = None) -> List[dict]:
        q = "SELECT * FROM exams WHERE 1=1"
        params = []
        if course_uid:
            q += " AND course_uid = ?"
            params.append(course_uid)
        if scope_uid:
            q += " AND scope_uid = ?"
            params.append(scope_uid)
        if kind:
            q += " AND kind = ?"
            params.append(kind)
        rows = self._get_db().execute(q + " ORDER BY created_at", params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["blueprint"] = json.loads(d["blueprint"] or "{}")
            d["standard_codes"] = json.loads(d["standard_codes"] or "[]")
            out.append(d)
        return out

    # -- attempts -------------------------------------------------------------

    def create_attempt(self, exam_id: str, course_uid: str = None,
                       theme: str = None, accommodations: dict = None,
                       student_id: str = None) -> str:
        aid = f"att_{uuid.uuid4().hex[:8]}"
        conn = self._get_db()
        conn.execute(
            "INSERT INTO exam_attempts (id, student_id, exam_id, course_uid, theme, accommodations) "
            "VALUES (?,?,?,?,?,?)",
            (aid, _sid(student_id), exam_id, course_uid, theme,
             json.dumps(accommodations or {})))
        conn.commit()
        return aid

    def get_attempt(self, attempt_id: str, student_id: str = None) -> Optional[dict]:
        row = self._get_db().execute(
            "SELECT * FROM exam_attempts WHERE id = ? AND student_id = ?",
            (attempt_id, _sid(student_id))).fetchone()
        if not row:
            return None
        d = dict(row)
        d["accommodations"] = json.loads(d["accommodations"] or "{}")
        return d

    def in_progress_attempt(self, exam_id: str, student_id: str = None) -> Optional[dict]:
        row = self._get_db().execute(
            "SELECT * FROM exam_attempts WHERE exam_id = ? AND student_id = ? "
            "AND status = 'in_progress' ORDER BY started_at DESC LIMIT 1",
            (exam_id, _sid(student_id))).fetchone()
        return dict(row) if row else None

    def update_attempt(self, attempt_id: str, student_id: str = None, **kwargs):
        kwargs = {k: v for k, v in kwargs.items() if k in self._ATTEMPT_COLUMNS}
        if not kwargs:
            return
        if "accommodations" in kwargs and not isinstance(kwargs["accommodations"], str):
            kwargs["accommodations"] = json.dumps(kwargs["accommodations"])
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._get_db()
        conn.execute(f"UPDATE exam_attempts SET {sets} WHERE id = ? AND student_id = ?",
                     list(kwargs.values()) + [attempt_id, _sid(student_id)])
        conn.commit()

    def count_attempts_since(self, exam_id: str, since_iso: str,
                             student_id: str = None) -> int:
        """Attempts counting toward the limit: graded/submitted always;
        abandoned only when at least one item was answered (spec 05 §9.3)."""
        row = self._get_db().execute(
            "SELECT COUNT(*) AS cnt FROM exam_attempts a WHERE a.exam_id = ? "
            "AND a.student_id = ? AND a.started_at >= ? AND ("
            "  a.status IN ('submitted','graded') OR "
            "  (a.status = 'abandoned' AND EXISTS ("
            "     SELECT 1 FROM exam_item_responses r WHERE r.attempt_id = a.id "
            "     AND r.response IS NOT NULL)))",
            (exam_id, _sid(student_id), since_iso)).fetchone()
        return row["cnt"] if row else 0

    # -- item responses -------------------------------------------------------

    def add_item(self, attempt_id: str, item: dict, correct: dict,
                 theme_validated: bool = False) -> str:
        """Persist a generated item WITH its answer key (server-side only)."""
        rid = f"rsp_{uuid.uuid4().hex[:8]}"
        conn = self._get_db()
        conn.execute(
            "INSERT INTO exam_item_responses (id, attempt_id, standard_code, bloom_level, "
            "item_type, prompt, correct, theme_validated) VALUES (?,?,?,?,?,?,?,?)",
            (rid, attempt_id, item.get("standard_code"), item.get("bloom"),
             item.get("item_type"), json.dumps(item), json.dumps(correct),
             1 if theme_validated else 0))
        conn.commit()
        return rid

    def get_item(self, response_id: str) -> Optional[dict]:
        row = self._get_db().execute(
            "SELECT * FROM exam_item_responses WHERE id = ?", (response_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["prompt"] = json.loads(d["prompt"] or "{}")
        d["correct"] = json.loads(d["correct"] or "{}")
        return d

    def update_item(self, response_id: str, **kwargs):
        kwargs = {k: v for k, v in kwargs.items() if k in self._RESPONSE_COLUMNS}
        if not kwargs:
            return
        if "response" in kwargs and not isinstance(kwargs["response"], str):
            kwargs["response"] = json.dumps(kwargs["response"])
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._get_db()
        conn.execute(f"UPDATE exam_item_responses SET {sets} WHERE id = ?",
                     list(kwargs.values()) + [response_id])
        conn.commit()

    def items_for_attempt(self, attempt_id: str) -> List[dict]:
        rows = self._get_db().execute(
            "SELECT * FROM exam_item_responses WHERE attempt_id = ? ORDER BY rowid",
            (attempt_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["prompt"] = json.loads(d["prompt"] or "{}")
            d["correct"] = json.loads(d["correct"] or "{}")
            out.append(d)
        return out


class NotificationStore:
    """In-app notifications (B24.3; schema v8)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def create(self, recipient_id: str, recipient_role: str, kind: str,
               title: str = None, body: str = None, ref_uid: str = None) -> str:
        nid = f"ntf_{uuid.uuid4().hex[:8]}"
        conn = self._get_db()
        conn.execute(
            "INSERT INTO notifications (id, recipient_id, recipient_role, kind, title, body, ref_uid) "
            "VALUES (?,?,?,?,?,?,?)",
            (nid, recipient_id, recipient_role, kind, title, body, ref_uid))
        conn.commit()
        return nid

    def list_for(self, recipient_id: str, unread_only: bool = False) -> List[dict]:
        q = "SELECT * FROM notifications WHERE recipient_id = ?"
        if unread_only:
            q += " AND read_at IS NULL"
        rows = self._get_db().execute(q + " ORDER BY created_at DESC", (recipient_id,)).fetchall()
        return [dict(r) for r in rows]

    def unread_count(self, recipient_id: str) -> int:
        row = self._get_db().execute(
            "SELECT COUNT(*) AS cnt FROM notifications WHERE recipient_id = ? AND read_at IS NULL",
            (recipient_id,)).fetchone()
        return row["cnt"] if row else 0

    def mark_read(self, notification_id: str, recipient_id: str):
        conn = self._get_db()
        conn.execute(
            "UPDATE notifications SET read_at = datetime('now') WHERE id = ? AND recipient_id = ?",
            (notification_id, recipient_id))
        conn.commit()


class AccommodationStore:
    """IEP/504 accommodation flags (B25.4; schema v8)."""

    _FLAGS = ('extended_time', 'no_timer', 'reduced_distraction', 'larger_targets',
              'extra_scaffolding', 'simplified_language', 'read_aloud_default')

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def get(self, student_id: str) -> dict:
        row = self._get_db().execute(
            "SELECT * FROM accommodations WHERE student_id = ?", (student_id,)).fetchone()
        if not row:
            return {f: 0 for f in self._FLAGS}
        return dict(row)

    def set(self, student_id: str, set_by: str = None, notes: str = None, **flags):
        flags = {k: 1 if v else 0 for k, v in flags.items() if k in self._FLAGS}
        current = self.get(student_id)
        merged = {f: flags.get(f, current.get(f, 0)) for f in self._FLAGS}
        conn = self._get_db()
        conn.execute(
            "INSERT OR REPLACE INTO accommodations (student_id, " + ", ".join(self._FLAGS) +
            ", notes, set_by, updated_at) VALUES (?" + ",?" * len(self._FLAGS) +
            ", ?, ?, datetime('now'))",
            [student_id] + [merged[f] for f in self._FLAGS] + [notes, set_by])
        conn.commit()


class AuditStore:
    """FERPA/Utah data-access audit trail (B21.2; schema v8). Distinct from
    activity_log (learning events) — this records who looked at / exported /
    deleted whose data."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def record(self, action: str, actor_id: str = None, actor_role: str = None,
               subject_student_id: str = None, detail: dict = None,
               ip_address: str = None):
        conn = self._get_db()
        conn.execute(
            "INSERT INTO audit_log (actor_id, actor_role, action, subject_student_id, detail, ip_address) "
            "VALUES (?,?,?,?,?,?)",
            (actor_id, actor_role, action, subject_student_id,
             json.dumps(detail) if detail else None, ip_address))
        conn.commit()

    def list(self, subject_student_id: str = None, actor_id: str = None,
             limit: int = 200) -> List[dict]:
        q = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        if subject_student_id:
            q += " AND subject_student_id = ?"
            params.append(subject_student_id)
        if actor_id:
            q += " AND actor_id = ?"
            params.append(actor_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._get_db().execute(q, params).fetchall()
        return [dict(r) for r in rows]


class SubscriptionStore:
    """Stripe subscription mirror (B20; schema v4). Seats gate add-student."""

    _VALID_COLUMNS = {'provider', 'provider_customer_id', 'provider_sub_id',
                      'plan', 'seats', 'status', 'current_period_end'}

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def get(self, parent_id: str) -> Optional[dict]:
        row = self._get_db().execute(
            "SELECT * FROM subscriptions WHERE parent_id = ?", (parent_id,)).fetchone()
        return dict(row) if row else None

    def upsert(self, parent_id: str, **kwargs):
        kwargs = {k: v for k, v in kwargs.items() if k in self._VALID_COLUMNS}
        current = self.get(parent_id) or {}
        merged = {**{k: current.get(k) for k in self._VALID_COLUMNS}, **kwargs}
        conn = self._get_db()
        conn.execute(
            "INSERT OR REPLACE INTO subscriptions (parent_id, provider, provider_customer_id, "
            "provider_sub_id, plan, seats, status, current_period_end, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
            (parent_id, merged.get('provider') or 'stripe',
             merged.get('provider_customer_id'), merged.get('provider_sub_id'),
             merged.get('plan'), merged.get('seats') or 1,
             merged.get('status') or 'inactive', merged.get('current_period_end')))
        conn.commit()

    def event_seen(self, provider_event_id: str) -> bool:
        row = self._get_db().execute(
            "SELECT 1 FROM billing_events WHERE provider_event_id = ?",
            (provider_event_id,)).fetchone()
        return row is not None

    def mark_event(self, provider_event_id: str, event_type: str = None,
                   parent_id: str = None, payload_hash: str = None):
        conn = self._get_db()
        conn.execute(
            "INSERT OR IGNORE INTO billing_events (provider_event_id, type, parent_id, payload_hash) "
            "VALUES (?,?,?,?)", (provider_event_id, event_type, parent_id, payload_hash))
        conn.commit()

    def seats_for(self, parent_id: str, default_seats: int = 3) -> int:
        """Seat allowance: active subscription seats, else the free default
        (families can add up to `default_seats` learners before billing lands)."""
        sub = self.get(parent_id)
        if sub and sub.get('status') in ('active', 'trialing'):
            return int(sub.get('seats') or 1)
        return default_seats


class GamificationStore:
    """Per-student XP / level / streak over the v7 tables (B22.1), with an
    audit ledger (xp_ledger) for anti-cheat and analytics. Replaces the
    librarian's global gamification K-V; the legacy totals are adopted into
    the legacy student's row on first read."""

    LEVEL_THRESHOLDS = [0, 100, 300, 600, 1000, 1500, 2200, 3000, 4000, 5500, 7500, 10000]
    BASE_XP = {'answer': 10, 'complete_concept': 25, 'complete_module': 100,
               'review': 15, 'exam_pass': 50, 'quest': 20}

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def level_from_xp(self, xp: int) -> int:
        level = 1
        for i, threshold in enumerate(self.LEVEL_THRESHOLDS):
            if xp >= threshold:
                level = i + 1
        return level

    def _row(self, student_id: str) -> dict:
        conn = self._get_db()
        row = conn.execute("SELECT * FROM student_gamification WHERE student_id = ?",
                           (student_id,)).fetchone()
        if row:
            return dict(row)
        # first touch: adopt the legacy global K-V for the legacy student
        # (spec 01 §1 backfill step 3), fresh zeros for everyone else
        seed = {"total_xp": 0, "level": 1, "streak_days": 0,
                "streak_last_date": None, "daily_xp": 0, "daily_date": None}
        if student_id == DEFAULT_STUDENT_ID:
            try:
                legacy = {r["key"]: r["value"] for r in conn.execute(
                    "SELECT key, value FROM gamification").fetchall()}
                for k in ("total_xp", "streak_days", "daily_xp"):
                    if legacy.get(k) is not None:
                        seed[k] = int(legacy[k])
                seed["streak_last_date"] = legacy.get("streak_last_date")
                seed["daily_date"] = legacy.get("daily_date")
                seed["level"] = self.level_from_xp(seed["total_xp"])
            except sqlite3.OperationalError:
                pass  # no legacy table — fresh install
        conn.execute(
            "INSERT OR IGNORE INTO student_gamification "
            "(student_id, total_xp, level, streak_days, streak_last_date, daily_xp, daily_date) "
            "VALUES (?,?,?,?,?,?,?)",
            (student_id, seed["total_xp"], seed["level"], seed["streak_days"],
             seed["streak_last_date"], seed["daily_xp"], seed["daily_date"]))
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM student_gamification WHERE student_id = ?",
            (student_id,)).fetchone())

    def get(self, student_id: str = None) -> dict:
        sid = _sid(student_id)
        row = self._row(sid)
        today = date.today().isoformat()
        if row.get("daily_date") != today:
            row["daily_xp"] = 0
        row["level"] = self.level_from_xp(row["total_xp"])
        nxt = (self.LEVEL_THRESHOLDS[row["level"]]
               if row["level"] < len(self.LEVEL_THRESHOLDS)
               else self.LEVEL_THRESHOLDS[-1] + 2000)
        prev = self.LEVEL_THRESHOLDS[row["level"] - 1] if row["level"] > 1 else 0
        row["next_level_xp"] = nxt
        row["prev_level_xp"] = prev
        return row

    def award_xp(self, action: str, grade: int = 3, bloom_level: int = 1,
                 first_try: bool = False, ref_uid: str = None,
                 student_id: str = None) -> dict:
        """XP for a graded interaction. Correctness-gated for answers; every
        award appends an xp_ledger row."""
        sid = _sid(student_id)
        if action == 'answer' and grade < 3:
            current = self.get(sid)
            return {"xp_earned": 0, "total_xp": current["total_xp"],
                    "level": current["level"], "level_up": False}
        base = self.BASE_XP.get(action, 10)
        multiplier = 1.0
        if first_try:
            multiplier *= 1.5
        if bloom_level >= 4:
            multiplier *= 2.0
        earned = int(base * multiplier)

        row = self._row(sid)
        today = date.today().isoformat()
        old_level = self.level_from_xp(row["total_xp"])
        new_total = row["total_xp"] + earned
        new_level = self.level_from_xp(new_total)
        daily = earned if row.get("daily_date") != today else row["daily_xp"] + earned

        conn = self._get_db()
        conn.execute(
            "UPDATE student_gamification SET total_xp = ?, level = ?, daily_xp = ?, "
            "daily_date = ? WHERE student_id = ?",
            (new_total, new_level, daily, today, sid))
        conn.execute(
            "INSERT INTO xp_ledger (student_id, amount, reason, ref_uid) VALUES (?,?,?,?)",
            (sid, earned, action, ref_uid))
        conn.commit()
        return {"xp_earned": earned, "total_xp": new_total, "level": new_level,
                "level_up": new_level > old_level,
                "new_level": new_level if new_level > old_level else None,
                "daily_xp": daily}

    def check_streak(self, student_id: str = None) -> dict:
        """Daily streak: +1 on consecutive days, reset after a gap."""
        sid = _sid(student_id)
        row = self._row(sid)
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        last = row.get("streak_last_date")
        if last == today:
            return {"streak_days": row["streak_days"], "incremented": False}
        streak = row["streak_days"] + 1 if last == yesterday else 1
        conn = self._get_db()
        conn.execute(
            "UPDATE student_gamification SET streak_days = ?, streak_last_date = ? "
            "WHERE student_id = ?", (streak, today, sid))
        conn.commit()
        return {"streak_days": streak, "incremented": True}

    def ledger(self, student_id: str = None, limit: int = 100) -> List[dict]:
        rows = self._get_db().execute(
            "SELECT * FROM xp_ledger WHERE student_id = ? ORDER BY created_at DESC LIMIT ?",
            (_sid(student_id), limit)).fetchall()
        return [dict(r) for r in rows]

    # -- badges (B22.3): threshold criteria over the student's own numbers ----

    DEFAULT_BADGES = [
        ("bdg_streak_3",  "On a Roll",      "3-day learning streak",  "🔥", "streak:3",  25, "streak"),
        ("bdg_streak_7",  "Week Warrior",   "7-day learning streak",  "🔥", "streak:7",  75, "streak"),
        ("bdg_streak_30", "Habit Hero",     "30-day learning streak", "🏆", "streak:30", 300, "streak"),
        ("bdg_level_5",   "Climber",        "Reach level 5",          "⛰️", "level:5",   50, "special"),
        ("bdg_xp_1000",   "Knowledge Bank", "Earn 1000 XP",           "💎", "xp:1000",  100, "special"),
        ("bdg_exam_pass", "Checkpoint Champ", "Pass your first checkpoint", "✅", "exam_pass:1", 50, "standard"),
    ]

    def seed_badges(self):
        """Idempotent insert of the default badge catalog (global rows)."""
        conn = self._get_db()
        for bid, name, desc, icon, criteria, xp, scope in self.DEFAULT_BADGES:
            conn.execute(
                "INSERT OR IGNORE INTO badges (id, name, description, icon, criteria, xp_reward, scope) "
                "VALUES (?,?,?,?,?,?,?)", (bid, name, desc, icon, criteria, xp, scope))
        conn.commit()

    def check_and_award_badges(self, student_id: str = None) -> List[dict]:
        """Evaluate every locked badge's threshold against the student's own
        numbers (never another family's — B22.6: no cross-family comparison
        exists anywhere). Returns newly unlocked badges."""
        sid = _sid(student_id)
        self.seed_badges()
        conn = self._get_db()
        row = self.get(sid)
        exam_passes = conn.execute(
            "SELECT COUNT(*) AS c FROM xp_ledger WHERE student_id = ? AND reason = 'exam_pass'",
            (sid,)).fetchone()["c"]
        values = {"streak": row["streak_days"], "level": row["level"],
                  "xp": row["total_xp"], "exam_pass": exam_passes}
        unlocked_now = []
        owned = {r["badge_id"] for r in conn.execute(
            "SELECT badge_id FROM student_badges WHERE student_id = ?", (sid,)).fetchall()}
        for b in conn.execute("SELECT * FROM badges").fetchall():
            b = dict(b)
            if b["id"] in owned:
                continue
            try:
                metric, threshold = b["criteria"].split(":")
                if values.get(metric, 0) >= int(threshold):
                    conn.execute(
                        "INSERT OR IGNORE INTO student_badges (student_id, badge_id) VALUES (?, ?)",
                        (sid, b["id"]))
                    if b.get("xp_reward"):
                        conn.execute(
                            "INSERT INTO xp_ledger (student_id, amount, reason, ref_uid) "
                            "VALUES (?,?,?,?)", (sid, b["xp_reward"], "quest", b["id"]))
                        conn.execute(
                            "UPDATE student_gamification SET total_xp = total_xp + ? "
                            "WHERE student_id = ?", (b["xp_reward"], sid))
                    unlocked_now.append(b)
            except (ValueError, AttributeError):
                continue
        conn.commit()
        return unlocked_now

    def badges_for(self, student_id: str = None) -> dict:
        sid = _sid(student_id)
        self.seed_badges()
        conn = self._get_db()
        owned = {r["badge_id"]: r["unlocked_at"] for r in conn.execute(
            "SELECT badge_id, unlocked_at FROM student_badges WHERE student_id = ?",
            (sid,)).fetchall()}
        out = {"unlocked": [], "locked": []}
        for b in conn.execute("SELECT * FROM badges").fetchall():
            b = dict(b)
            if b["id"] in owned:
                b["unlocked_at"] = owned[b["id"]]
                out["unlocked"].append(b)
            else:
                out["locked"].append(b)
        return out

    # -- daily quests (B22.3) --------------------------------------------------

    DEFAULT_QUESTS = [
        ("qst_answer_5", "Answer 5 questions", "answer", 5, 20, "daily"),
        ("qst_review_3", "Review 3 flashcards", "review", 3, 15, "daily"),
    ]

    def seed_quests(self):
        conn = self._get_db()
        for qid, title, kind, target, xp, cadence in self.DEFAULT_QUESTS:
            conn.execute(
                "INSERT OR IGNORE INTO quests (id, title, kind, target, xp_reward, cadence) "
                "VALUES (?,?,?,?,?,?)", (qid, title, kind, target, xp, cadence))
        conn.commit()

    def quests_for(self, student_id: str = None) -> List[dict]:
        """Today's quests with this student's progress (created on demand)."""
        sid = _sid(student_id)
        self.seed_quests()
        conn = self._get_db()
        today = date.today().isoformat()
        out = []
        for q in conn.execute("SELECT * FROM quests WHERE cadence = 'daily'").fetchall():
            q = dict(q)
            conn.execute(
                "INSERT OR IGNORE INTO student_quests (student_id, quest_id, period_key) "
                "VALUES (?,?,?)", (sid, q["id"], today))
            sq = conn.execute(
                "SELECT * FROM student_quests WHERE student_id = ? AND quest_id = ? "
                "AND period_key = ?", (sid, q["id"], today)).fetchone()
            q.update({"progress": sq["progress"], "status": sq["status"],
                      "period_key": today})
            out.append(q)
        conn.commit()
        return out

    # -- cosmetics (B22.4): interest-themed avatar unlocks by level ------------

    COSMETIC_CATALOG = [
        # (id, name, theme keyword, unlock level)
        ("cos_star",     "Star Learner",     None,      1),
        ("cos_rocket",   "Rocket",           "space",   2),
        ("cos_planet",   "Planet Explorer",  "space",   4),
        ("cos_ball",     "Golden Ball",      "soccer",  2),
        ("cos_trophy",   "Champion Trophy",  "soccer",  4),
        ("cos_dino",     "Dino Buddy",       "dinosaur", 2),
        ("cos_dragon",   "Book Dragon",      "reading", 3),
        ("cos_paw",      "Animal Friend",    "animal",  2),
        ("cos_palette",  "Artist Palette",   "art",     2),
        ("cos_crown",    "Scholar Crown",    None,      5),
        ("cos_gem",      "Brilliant Gem",    None,      7),
    ]

    def cosmetics_for(self, student_id: str = None) -> dict:
        """Unlockable cosmetics: themed items whose keyword matches the
        student's interests surface first; unlock is by level. Never
        references another family's data (B22.6)."""
        sid = _sid(student_id)
        row = self.get(sid)
        level = row["level"]
        state = json.loads(row.get("cosmetics") or "{}")
        equipped = state.get("equipped")
        interests = []
        try:
            conn = self._get_db()
            student = conn.execute("SELECT interests FROM students WHERE id = ?",
                                   (sid,)).fetchone()
            if student:
                interests = [i.lower() for i in json.loads(student["interests"] or "[]")]
        except Exception:
            pass

        def _themed(theme):
            return theme is None or any(theme in i or i in theme for i in interests)

        unlocked, locked = [], []
        for cid, name, theme, need in self.COSMETIC_CATALOG:
            item = {"id": cid, "name": name, "theme": theme,
                    "unlock_level": need, "themed": _themed(theme),
                    "equipped": cid == equipped}
            (unlocked if level >= need else locked).append(item)
        # interest-matched items first inside each bucket
        unlocked.sort(key=lambda c: (not c["themed"], c["unlock_level"]))
        locked.sort(key=lambda c: (not c["themed"], c["unlock_level"]))
        return {"level": level, "equipped": equipped,
                "unlocked": unlocked, "locked": locked}

    def equip_cosmetic(self, cosmetic_id: str, student_id: str = None) -> bool:
        """Equip an UNLOCKED cosmetic; returns False if locked/unknown."""
        sid = _sid(student_id)
        state = self.cosmetics_for(sid)
        if cosmetic_id not in {c["id"] for c in state["unlocked"]}:
            return False
        row = self._row(sid)
        blob = json.loads(row.get("cosmetics") or "{}")
        blob["equipped"] = cosmetic_id
        conn = self._get_db()
        conn.execute("UPDATE student_gamification SET cosmetics = ? WHERE student_id = ?",
                     (json.dumps(blob), sid))
        conn.commit()
        return True

    def increment_quest(self, kind: str, student_id: str = None) -> List[dict]:
        """Advance today's quests of `kind` by one; completing awards XP once.
        Returns quests completed by this increment."""
        sid = _sid(student_id)
        self._row(sid)   # ensure the row exists so the XP UPDATE lands
        completed = []
        conn = self._get_db()
        for q in self.quests_for(sid):
            if q["kind"] != kind or q["status"] != "active":
                continue
            progress = q["progress"] + 1
            status = "completed" if progress >= q["target"] else "active"
            conn.execute(
                "UPDATE student_quests SET progress = ?, status = ? "
                "WHERE student_id = ? AND quest_id = ? AND period_key = ?",
                (progress, status, sid, q["id"], q["period_key"]))
            if status == "completed":
                conn.execute(
                    "INSERT INTO xp_ledger (student_id, amount, reason, ref_uid) "
                    "VALUES (?,?,?,?)", (sid, q["xp_reward"], "quest", q["id"]))
                conn.execute(
                    "UPDATE student_gamification SET total_xp = total_xp + ? "
                    "WHERE student_id = ?", (q["xp_reward"], sid))
                completed.append(q)
        conn.commit()
        return completed
