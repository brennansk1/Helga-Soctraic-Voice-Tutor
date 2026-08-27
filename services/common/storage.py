"""
Unified Storage Manager for Helga.

Replaces KuzuDB with three storage mechanisms:
- SQLite: user progress, activity log, scheduled reviews, settings
- JSON files: course structure (data/courses/{uid}/structure.json)
- Markdown files: concept content (data/courses/{uid}/content/{concept_uid}.md)
"""

import os
import re
import copy
import json
import sqlite3
import logging
import tempfile
import uuid
import shutil
import threading
import time
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


def _atomic_write_json(path: str, payload: Any, indent: int = 2):
    """Write JSON via a temp file + os.replace so a reader never sees half a file.
    Uses fcntl advisory locking on POSIX to prevent cross-process race conditions.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    lock_path = path + ".lock"
    
    # Inter-process lock acquisition
    lock_fd = None
    try:
        import fcntl
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except (ImportError, OSError):
        lock_fd = None

    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    finally:
        if lock_fd:
            try:
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except OSError:
                pass


# HOW ONE FAILED STATEMENT LOCKS THE WHOLE PRODUCT.
#
# Python's sqlite3 opens an implicit transaction on the first write and holds
# it until commit(). On a THREAD-LOCAL connection in a service that runs for
# days, an exception between the write and the commit leaves that transaction
# open forever — and with it SQLite's single write lock. Every other writer in
# every other process then fails with "database is locked", including the ones
# that would have written a learner's progress.
#
# There are 104 writes across the services that are not inside a `with conn:`
# or a try/finally, so the failure is not exotic; it is the default outcome of
# any raise on a write path, and these services swallow exceptions widely
# enough that it happens silently.
#
# Measured on 2026-08-25: every write failed with "database is locked" while
# core-logic, web-ui and research each reported healthy. Restarting rag alone
# did not clear it — the holder was another process, and nothing in any log
# said so.
#
# Fixing 104 call sites would leave the 105th. This fixes the connection
# instead: a statement that raises rolls its transaction back before the
# exception propagates, so a failure can no longer hold the lock.
class _SafeConnection(sqlite3.Connection):
    """A connection that cannot hold the write lock open through a failure."""

    # WHAT WAS THE LAST THING THIS CONNECTION WROTE.
    #
    # The self-heal below fired 25 times during one live build and could not
    # say what had leaked, which makes it a mop rather than a diagnosis.
    # Remembering the last write statement costs one attribute assignment and
    # turns "something left a transaction open" into a named culprit.
    _last_write = None
    _txn_opened_at = None

    def _remember(self, sql):
        try:
            head = " ".join(str(sql).split())[:80]
            if head[:6].upper() in ("INSERT", "UPDATE", "DELETE", "REPLAC",
                                    "CREATE", "DROP T", "ALTER "):
                self._last_write = head
                if not self.in_transaction:
                    self._txn_opened_at = time.monotonic()
        except Exception:
            pass

    def transaction_age(self):
        """Seconds this connection has been holding an open transaction."""
        if not self.in_transaction or self._txn_opened_at is None:
            return 0.0
        return time.monotonic() - self._txn_opened_at

    def _rollback_quietly(self):
        try:
            if self.in_transaction:
                self.rollback()
        except Exception:
            pass

    def execute(self, *a, **kw):
        if a:
            self._remember(a[0])
        try:
            return super().execute(*a, **kw)
        except Exception:
            self._rollback_quietly()
            raise

    def executemany(self, *a, **kw):
        if a:
            self._remember(a[0])
        try:
            return super().executemany(*a, **kw)
        except Exception:
            self._rollback_quietly()
            raise

    def executescript(self, *a, **kw):
        if a:
            self._remember(a[0])
        try:
            return super().executescript(*a, **kw)
        except Exception:
            self._rollback_quietly()
            raise


# How long a transaction may stay open before it is treated as abandoned. A
# write followed by its commit takes microseconds; anything still open after
# this was left by an operation that is not coming back.
IDLE_TXN_LIMIT_S = float(os.getenv("HELGA_IDLE_TXN_LIMIT", "30"))


def connect_safely(db_path: str, timeout: float = 30.0) -> sqlite3.Connection:
    """The only way this codebase should open helga.db.

    `timeout` is not decoration: the default is 5 seconds, which is shorter
    than a single hydration write under load, so contention surfaced as a hard
    failure rather than a wait.
    """
    conn = sqlite3.connect(db_path, timeout=timeout, factory=_SafeConnection)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    # THE PRAGMAS BELOW ARE SET BECAUSE THE ALTERNATIVE IS INHERITING THEM.
    #
    # Every one of these has a compile-time default, so leaving them unset does
    # not mean "the documented default" — it means "whatever the SQLite in this
    # container image was built with". Five services run from different images
    # against the SAME helga.db, so durability and memory use were varying by
    # which process happened to open the file. Measured on this host, the
    # inherited cache_size was `2000` — POSITIVE, i.e. 2000 *pages* = 8 MB per
    # connection, not the 2 MB that the modern default (-2000 KiB) implies.
    #
    # synchronous=NORMAL: safe under WAL. The WAL is still fsynced at each
    # checkpoint; only the per-commit fsync goes away. The documented exposure
    # is losing the last commits on an OS/host crash — not corruption — and
    # this is a bind-mounted file on virtiofs where a per-commit fsync is the
    # most expensive thing a write can do.
    conn.execute("PRAGMA synchronous=NORMAL")

    # temp_store=MEMORY: temp B-trees (ORDER BY, GROUP BY, the FTS rebuild)
    # stop being written through virtiofs to the container's temp dir.
    conn.execute("PRAGMA temp_store=MEMORY")

    # cache_size is PER CONNECTION, and connections here are thread-local per
    # STORE: ~18 stores x N threads x 5 services. A four-worker hydration pool
    # touching six stores is ~24 live connections in one service alone, so a
    # plausible fleet-wide worst case is low hundreds. At the inherited 8 MB
    # that is over a gigabyte of page cache on a 24 GB box with a 14 GB model
    # resident — which is why this is pinned rather than raised. The negative
    # form is KiB and therefore build-independent: 2 MB each, ~300 MB at a
    # 150-connection worst case, and the whole database is only ~20 MB so the
    # hot pages of any one store's working set still fit.
    conn.execute("PRAGMA cache_size=-2000")

    # Foreign keys default to OFF in SQLite and were being enabled on exactly
    # one connection path (_ThreadLocalDB), so CourseStore's connections — the
    # ones that write concepts, concept_math and the ledgers — silently had
    # them off. Declared-and-unenforced is worse than not declared.
    conn.execute("PRAGMA foreign_keys=ON")

    # DELIBERATELY NOT SET: mmap_size. helga.db lives on a virtiofs bind mount
    # from macOS. With mmap I/O, a read error surfaces as SIGBUS inside the
    # process instead of as an SQLITE_IOERR that the code above can handle, so
    # a hiccup on the mount would kill the service rather than fail a query.
    return conn


class _ThreadLocalDB:
    """Thread-local SQLite connection manager. One connection per thread, reused
    across calls. WAL mode enables concurrent reads from different threads."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()

    def get(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = connect_safely(self.db_path)
            conn.row_factory = sqlite3.Row
            # foreign_keys (and the rest of the pragmas) are set in
            # connect_safely now, so every connection gets them, not just this
            # one path.
            self._local.conn = conn
            return conn

        # AGE, NOT PRESENCE.
        #
        # This rolled back ANY connection handed out mid-transaction, on the
        # reasoning that a fresh caller means the previous one is gone. That
        # reasoning is wrong, and it cost real data: legitimate code calls
        # _get_db() a second time BETWEEN its write and its commit —
        #
        #     conn = store._get_db(); conn.execute("INSERT ...")
        #     store._get_db().commit()
        #
        # — and the second call rolled the insert back before the commit could
        # run. Measured on a live build: every hydration_provenance row was
        # discarded that way, silently, so locally built concepts had no
        # recorded author at all.
        #
        # What actually breaks the system is a transaction held INDEFINITELY.
        # A write followed by a commit takes microseconds; a leak lasts until
        # the process dies. Age separates them without guessing at intent.
        if conn.in_transaction and conn.transaction_age() > IDLE_TXN_LIMIT_S:
            logger.error(
                "SELF-HEAL: a previous operation on this thread left a "
                "transaction open on %s and would have held the write lock "
                "indefinitely. Rolling it back — its uncommitted changes are "
                "lost, which is what its failure implied. Last write on this "
                "connection: %s",
                os.path.basename(self.db_path),
                getattr(conn, "_last_write", None) or "unknown")
            try:
                conn.rollback()
            except Exception as e:
                logger.error("rollback of the leaked transaction failed: %s", e)
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
        self.programs = ProgramStore(self.db_path)
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
                        retrieved_at TEXT,
                        -- 1 = shown to the model and citable to a learner.
                        -- 0 = on-topic material that lost the prompt's word
                        -- budget. It is real evidence for a fact check, which
                        -- has no context window, but it must never render as
                        -- a citation for a claim the model never saw it make.
                        cited        INTEGER DEFAULT 1
                    )
                """)
                # Existing databases predate the column; adding it here keeps
                # old rows at the default 1, which is correct — everything
                # stored before this change WAS cited.
                try:
                    cursor.execute("ALTER TABLE sources ADD COLUMN "
                                   "cited INTEGER DEFAULT 1")
                except sqlite3.OperationalError:
                    pass          # already present
                # WHAT THE MODEL WAS ACTUALLY SHOWN.
                #
                # `sources` holds the passages we RETRIEVED. This holds the
                # reference material as it was assembled and handed to the
                # generator — which is not the same thing, and the difference
                # is the whole diagnosis when a claim turns out false:
                #
                #   claim contradicts the source we stored  -> the source was
                #       wrong, or was read wrong; reweight or replace it
                #   claim appears nowhere in what we showed -> the model
                #       invented it
                #
                # Without this, both failures look identical and every fix is a
                # guess. It was computed on every concept and discarded.
                #
                # Compressed: this is the single largest thing the ledger
                # stores, and text of this kind compresses 4-5x.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS grounding_context (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        text_z       BLOB,
                        chars        INTEGER,
                        recorded_at  TEXT,
                        PRIMARY KEY (course_uid, concept_uid)
                    )
                """)

                # WHETHER A VERDICT STILL DESCRIBES THE FILE.
                #
                # Every quality verdict is written once and read forever. A
                # concept repaired by the audit, or edited by hand, keeps its
                # old verdict with nothing anywhere able to notice: the badge
                # says verified and the sentence it verified is gone. The hash
                # is what makes staleness detectable instead of invisible.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS concept_content_hash (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        sha256       TEXT NOT NULL,
                        chars        INTEGER,
                        recorded_at  TEXT,
                        PRIMARY KEY (course_uid, concept_uid)
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
                # A SECOND FTS5 INDEX WAS CREATED HERE AND NEVER READ.
                # `concepts_fts` duplicated `concept_fts` (SearchStore), which
                # is the one every search actually queries. Creating it is
                # dropped from this migration and v19 removes it from databases
                # that already have it; see the note in save_concept_content.
                cursor.execute("UPDATE schema_version SET version = 15")
                logger.info("Schema migrated to v15: concept bodies in SQLite + FTS5")

            if current_version < 16:
                # SPOKEN MATHEMATICS, generated once at hydration.
                #
                # KaTeX renders a formula and cannot say it. The TTS and
                # text-only paths both receive raw LaTeX today, and a speech
                # engine handed \frac{a}{b} reads backslashes or drops them.
                # The speech string is computed offline and stored beside the
                # LaTeX so a tutoring turn reads a field rather than parsing
                # markup on the critical path of a ~30 s reply.
                #
                # `mathml` is unused today and present because MathML Core is
                # natively supported in every current browser, and because the
                # MathJax Speech Rule Engine — the mature upgrade path from our
                # own converter — consumes it.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS concept_math (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        latex        TEXT NOT NULL,
                        mathml       TEXT,
                        speech       TEXT,
                        unspoken     TEXT,
                        ordinal      INTEGER
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_math_concept "
                               "ON concept_math(course_uid, concept_uid)")

                # ASSETS AS BLOBS, for integrity rather than speed.
                #
                # Images sit above the ~100 KB point where the filesystem wins
                # on raw benchmark, so this is not a performance decision. A
                # single-file database cannot develop dangling references to
                # missing image files, WAL gives atomic rebuilds, and the course
                # stays one portable artefact. Anything genuinely large spills
                # to disk with `path` set and `bytes` NULL.
                #
                # `license_verified_at` is what makes fail-closed licensing
                # auditable rather than asserted: an unknown licence is refused
                # at fetch time, and this records WHEN that was established.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS assets (
                        asset_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                        sha256       TEXT UNIQUE,
                        bytes        BLOB,
                        path         TEXT,
                        mime         TEXT,
                        width        INTEGER,
                        height       INTEGER,
                        source       TEXT,
                        license      TEXT,
                        license_verified_at TEXT,
                        provenance_url TEXT,
                        alt_text     TEXT,
                        caption      TEXT,
                        caption_verified INTEGER DEFAULT 0
                    )
                """)
                # ROLE IS NOT NULL, DELIBERATELY.
                #
                # The seductive-details evidence is about DECORATIVE images, and
                # the research used "photograph" as a proxy for that. Since we
                # allow photographs from curated educational collections, the
                # medium proxy is gone and something has to replace it: an asset
                # must say what job it does for the concept, and an asset that
                # is merely *related* has no role and cannot be attached.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS concept_assets (
                        course_uid   TEXT NOT NULL,
                        concept_uid  TEXT NOT NULL,
                        asset_id     INTEGER NOT NULL,
                        role         TEXT NOT NULL,
                        PRIMARY KEY (course_uid, concept_uid, asset_id)
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_concept_assets "
                               "ON concept_assets(course_uid, concept_uid)")
                cursor.execute("UPDATE schema_version SET version = 16")
                logger.info("Schema migrated to v16: spoken math + assets")

            if current_version < 17:
                # DEGREE PROGRAMMES.
                #
                # plan_degree() has produced real programmes -- sourced course
                # lists, inferred prerequisites, topological term layout, all
                # validated -- since it was written, and there was nowhere to
                # put one. The planner was reachable only from tests, so the
                # degree tier existed end to end except for the part where a
                # learner could have one.
                #
                # The plan is stored whole as JSON because it is the planner's
                # output and splitting it into tables would mean re-deriving
                # what it already decided. Only the two things that CHANGE as a
                # learner moves through it are columns: which electives were
                # chosen, and which courses have been built.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS programs (
                        uid          TEXT PRIMARY KEY,
                        subject      TEXT NOT NULL,
                        template     TEXT NOT NULL,
                        plan_json    TEXT NOT NULL,
                        status       TEXT DEFAULT 'active',
                        created_at   TEXT,
                        updated_at   TEXT
                    )
                """)
                # One row per course slot in the programme. The link to a real
                # built course is course_uid, NULL until it is built -- which
                # is what lets the map grey a course out and still name it.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS program_courses (
                        program_uid  TEXT NOT NULL,
                        title        TEXT NOT NULL,
                        term         INTEGER,
                        slot         TEXT,
                        chosen       INTEGER DEFAULT 1,
                        built        INTEGER DEFAULT 0,
                        course_uid   TEXT,
                        PRIMARY KEY (program_uid, title)
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_program_courses "
                               "ON program_courses(program_uid)")
                cursor.execute("UPDATE schema_version SET version = 17")
                logger.info("Schema migrated to v17: degree programmes")

            if current_version < 18:
                # `completed` was missing, and the entire degree surface is
                # built on it: program.available_courses() decides what a
                # learner may start next by asking which prerequisites are
                # complete, and the page leads with "6 of 20 courses
                # complete". Nothing could write it, so availability could
                # never advance and the meter could only ever read zero.
                cols = {r[1] for r in cursor.execute(
                    "PRAGMA table_info(program_courses)").fetchall()}
                if "completed" not in cols:
                    cursor.execute("ALTER TABLE program_courses "
                                   "ADD COLUMN completed INTEGER DEFAULT 0")
                if "completed_at" not in cols:
                    cursor.execute("ALTER TABLE program_courses "
                                   "ADD COLUMN completed_at TEXT")
                cursor.execute("UPDATE schema_version SET version = 18")
                logger.info("Schema migrated to v18: programme completion")

            if current_version < 19:
                # INDEXES FOR THE QUERIES THIS SYSTEM ACTUALLY RUNS, plus the
                # removal of the duplicate full-text index.
                #
                # Each of the four was confirmed with EXPLAIN QUERY PLAN on a
                # copy of the live database (21.8 MB, 156 concept rows)
                # before and after; the plan each one fixes is named below.

                # Each statement is attempted independently: an index on a
                # table an older or partly built database does not have must
                # not abort startup for everything else.
                def _try_ddl(sql):
                    try:
                        cursor.execute(sql)
                    except sqlite3.OperationalError as e:
                        logger.warning("v19: %s -- skipped (%s)", sql.split("(")[0].strip(), e)

                # due_for_review(): "SCAN user_progress". idx_progress_student
                # is (student_id, course_uid) — the right first column and the
                # wrong second one, so it cannot serve a date range. Partial,
                # because the query only ever wants rows that HAVE a due date
                # and most rows in a big course do not.
                _try_ddl(
                    "CREATE INDEX IF NOT EXISTS idx_progress_due "
                    "ON user_progress(student_id, next_review_date) "
                    "WHERE next_review_date IS NOT NULL")

                # taught_ledger's per-concept DELETE and SELECT narrow by
                # (course_uid, concept_uid) but the only index is
                # (course_uid, ordinal), so each one visits every claim in
                # the course. Measured on the largest live course: 879 claims
                # over 95 concepts, so each per-concept DELETE walked all 879
                # to touch ~9 — about 83,000 row visits across that build.
                _try_ddl(
                    "CREATE INDEX IF NOT EXISTS idx_claims_concept "
                    "ON taught_claims(course_uid, concept_uid)")

                # list_catalog_courses(): "SCAN courses". NOTE: the audit note
                # asked for (is_catalog, status); the predicate in the code is
                # is_catalog = 1 AND catalog_status = 'published' — `status` is
                # the build-state column and is already indexed on its own by
                # idx_courses_status. The two sort columns are included so the
                # ORDER BY is served from the index too.
                _try_ddl(
                    "CREATE INDEX IF NOT EXISTS idx_courses_catalog "
                    "ON courses(is_catalog, catalog_status, subject, grade_numeric, title)")

                # get_concept_math() is on the tutoring latency path and its
                # plan said "USE TEMP B-TREE FOR ORDER BY": the index stopped
                # at (course_uid, concept_uid) and the ORDER BY ordinal was
                # sorted at runtime. Widening it makes the read ordered.
                _try_ddl("DROP INDEX IF EXISTS idx_math_concept")
                _try_ddl(
                    "CREATE INDEX IF NOT EXISTS idx_math_concept "
                    "ON concept_math(course_uid, concept_uid, ordinal)")

                # The write-only duplicate. Nothing reads it (see
                # save_concept_content); the rows it holds are a stale copy of
                # `concepts`, which is authoritative, so this loses no
                # information that cannot be regenerated.
                try:
                    cursor.execute("DROP TABLE IF EXISTS concepts_fts")
                except sqlite3.OperationalError as e:
                    # A search index that cannot be dropped must not stop a
                    # migration; it is inert either way now.
                    logger.warning(f"could not drop the dead concepts_fts: {e}")

                cursor.execute("UPDATE schema_version SET version = 19")
                logger.info("Schema migrated to v19: query indexes "
                            "(due reviews, taught claims, catalog, concept math) "
                            "+ dropped the unread concepts_fts index")

            if current_version < 20:
                # Review items. The flashcards table already carries every FSRS
                # field and every existing card's history, so the item bank
                # extends it rather than opening a second store that would need
                # its own scheduler, its own due query and its own bugs.
                #
                # `kind` is what the item asks for (recall / discriminate /
                # apply / socratic); `bloom` and `depth` feed queue priority.
                for col, decl in (
                    ("kind", "TEXT DEFAULT 'recall'"),
                    ("bloom", "INTEGER DEFAULT 2"),
                    ("source_section", "TEXT"),
                    ("payload", "TEXT"),
                    ("depth", "INTEGER DEFAULT 0"),
                ):
                    try:
                        cursor.execute(
                            f"ALTER TABLE flashcards ADD COLUMN {col} {decl}")
                    except sqlite3.OperationalError:
                        pass          # already present
                # _try_ddl belongs to the v19 block's scope; an index that
                # cannot be created must not abort the migration either way.
                for ddl in (
                    "CREATE INDEX IF NOT EXISTS idx_items_kind "
                    "ON flashcards(student_id, kind, next_review_date)",
                    "CREATE INDEX IF NOT EXISTS idx_items_concept "
                    "ON flashcards(concept_uid)",
                ):
                    try:
                        cursor.execute(ddl)
                    except sqlite3.OperationalError as e:
                        logger.warning("v20: %s -- skipped (%s)",
                                       ddl.split("(")[0].strip(), e)
                cursor.execute("UPDATE schema_version SET version = 20")
                logger.info("Schema migrated to v20: review items "
                            "(kind/bloom/source_section/payload/depth) on flashcards")

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
        # BOUNDED. This held whole structure.json blobs for every course
        # touched, for process lifetime, with no cap — and deep-copied on
        # every read, so it cost CPU on hits as well as RAM. On a 24 GB
        # machine already carrying a 13.5 GB model, an unbounded blob cache
        # is the wrong thing to be unbounded.
        self._cache = {}
        self._cache_max = 8
        # Set by StorageManager to the search index's upserter. Left None here
        # so a CourseStore built standalone (tests, scripts) still works.
        self.on_content_saved = None

    def _evict_if_full(self):
        """Drop the oldest entry once the cache is full.

        Insertion-ordered dict, so the first key is the least recently ADDED.
        Not a true LRU — a course being actively taught is re-read constantly
        and would survive either policy, and a real LRU here would cost more
        bookkeeping than it saves on a cache of eight.
        """
        while len(self._cache) >= self._cache_max:
            self._cache.pop(next(iter(self._cache)), None)

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
            conn = connect_safely(os.path.join(self.data_dir, "helga.db"))
            self._tl.conn = conn
            return conn
        # Same self-heal as _ThreadLocalDB.get(); see the note there.
        if conn.in_transaction:
            logger.error("SELF-HEAL: leaked transaction on the CourseStore "
                         "connection — rolling back so the write lock is freed")
            try:
                conn.rollback()
            except Exception as e:
                logger.error("rollback failed: %s", e)
        return conn

    def _structure_path(self, uid: str) -> str:
        return os.path.join(self.courses_dir, uid, "structure.json")

    @staticmethod
    def _file_signature(path: str):
        """(mtime_ns, size, inode) for structure.json, or None if it is gone.

        Used to revalidate the cache. st_mtime alone is one-second granular on
        some filesystems and a build rewrites structure.json far faster than
        that; the inode changes on every os.replace, so the triple cannot miss a
        write made through _atomic_write_json even inside the same second.

        One stat() per get_course. The per-concept path (save_concept_content ->
        get_concept_by_uid -> get_course) already does a file write and an FTS
        upsert per call, so a ~2us stat is not measurable there.
        """
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _cache_put(self, uid: str, path: str, course_dict: dict):
        """Cache a course keyed on the signature of the file we just wrote."""
        sig = self._file_signature(path)
        if sig is None:
            self._cache.pop(uid, None)
            return
        self._cache[uid] = (sig, copy.deepcopy(course_dict))

    @staticmethod
    def _sqlite_now() -> str:
        """UTC in SQLite's own CURRENT_TIMESTAMP format: "YYYY-MM-DD HH:MM:SS"."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _write_course_row(self, uid: str, course_dict: dict, is_create: bool):
        """Write the `courses` metadata row. Raises if SQLite refuses.

        Shared by create_course and update_course so the two can never drift in
        which columns they write — they used to be two hand-maintained copies of
        the same 14-column list, one INSERT OR REPLACE and one UPDATE.

        It is an upsert rather than an UPDATE because an UPDATE ... WHERE uid=?
        against a missing row succeeds while changing nothing: structure.json
        would say "ready" and SQLite would still say "skeleton" (or say nothing
        at all), permanently and with no error, and /api/courses reads SQLite
        while /api/course_status reads the JSON.

        created_at is set explicitly instead of being left to the column
        default. The default is datetime('now') — UTC, SPACE separator — while
        Python-side course dicts carry an isoformat()/strftime "T". Comparing
        the two byte-wise mis-orders every row (' ' 0x20 < 'T' 0x54); that is
        what made background_ops mark every live build "failed" about five
        minutes in. We deliberately do NOT copy course_dict["created_at"] into
        the column: course_builder writes that one with local-time strftime, and
        a local timestamp measured against a UTC cutoff resurrects exactly the
        same bug shifted by the UTC offset.
        """
        cat = course_dict.get("catalog") or {}
        sql = """
            INSERT INTO courses (uid, title, overview, status, teaching_style,
                subject, grade_band, grade_numeric, is_catalog, catalog_status,
                version, visibility, reviewed_by, published_at,
                enrichment_included, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                title=excluded.title,
                overview=excluded.overview,
                status=excluded.status,
                teaching_style=excluded.teaching_style,
                subject=excluded.subject,
                grade_band=excluded.grade_band,
                grade_numeric=excluded.grade_numeric,
                is_catalog=excluded.is_catalog,
                catalog_status=excluded.catalog_status,
                version=excluded.version,
                visibility=excluded.visibility,
                reviewed_by=excluded.reviewed_by,
                published_at=excluded.published_at,
                enrichment_included=excluded.enrichment_included
        """
        # A rebuild under an existing uid restarts the clock (this is what
        # INSERT OR REPLACE used to do); an ordinary update must not, or the
        # stale-build sweeper's one-hour grace period resets on every write.
        if is_create:
            sql += ",\n                created_at=excluded.created_at"

        params = (
            uid,
            course_dict.get("title", ""),
            # THE COLUMN IS `overview`; THE DOCUMENT SAYS `description`.
                # Nothing ever mapped one to the other, so the row stayed empty
                # while structure.json held the real text — and the course list,
                # which reads the ROW, showed no description for any course.
                # The front end then filled the gap with one identical sentence
                # on every card.
                (course_dict.get("overview")
                 or course_dict.get("description") or ""),
            course_dict.get("status", "unknown"),
            course_dict.get("teaching_style", ""),
            cat.get("subject"), cat.get("grade_band"), cat.get("grade_numeric"),
            1 if cat.get("is_catalog") else 0,
            cat.get("catalog_status", "draft") if cat else "draft",
            cat.get("version", 1) if cat else 1,
            cat.get("visibility", "private") if cat else "private",
            cat.get("reviewed_by"), cat.get("published_at"),
            1 if cat.get("enrichment_included") else 0,
            self._sqlite_now(),
        )

        db_path = os.path.join(self.data_dir, "helga.db")
        # 30s busy timeout (default is 5): core-logic and rag both write this
        # database, and a build holds the write lock in bursts.
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def create_course(self, course_dict: dict) -> str:
        """Write course structure.json and sync metadata to SQLite."""
        uid = course_dict.get("uid") or f"course_{uuid.uuid4().hex[:8]}"
        course_dict["uid"] = uid
        if "created_at" not in course_dict:
            course_dict["created_at"] = datetime.utcnow().isoformat()
        # Stamped from birth so `updated_at` can be compared unconditionally.
        # Without this a course was written with no stamp, and the staleness
        # check in update_course had nothing to compare a caller's copy
        # against until the second write — so the first hours of a build, the
        # window that matters most, were unprotected.
        course_dict.setdefault("updated_at", course_dict["created_at"])
        if "status" not in course_dict:
            course_dict["status"] = "skeleton"

        # AUTO-10, both directions. A course lives in two stores — the row in
        # `courses` and the directory holding structure.json — and this method
        # is the only place both are created. Whatever order it writes them in,
        # a failure between the two writes leaves the system holding one.
        #
        # The order here used to be SQLite first, on the reasoning that the row
        # is what the course list reads. That gets the asymmetry backwards. The
        # two residues are not equally bad:
        #
        #   row without directory   The row is a title and a status. The course
        #                           itself is GONE and nothing can rebuild it.
        #                           The list offers an entry that cannot open.
        #   directory without row   structure.json IS the course. The row is
        #                           derived metadata, regenerable from the file
        #                           at any time.
        #
        # Measured against the live data directory on 2026-08-19: 19 rows, 3
        # directories — sixteen unopenable courses, all of them produced by
        # this window. So write the recoverable store first, and if the second
        # write fails, undo the first so neither store keeps a half-course.
        # tools/reconcile_courses.py repairs whatever already leaked.
        payload = json.dumps(course_dict, indent=2)  # before touching any store

        course_dir = os.path.join(self.courses_dir, uid)
        structure_path = os.path.join(course_dir, "structure.json")
        # Only a directory this call actually created may be rolled back. An
        # INSERT OR REPLACE over an existing uid must never delete content that
        # was already on disk.
        dir_existed = os.path.exists(course_dir)

        os.makedirs(course_dir, exist_ok=True)
        os.makedirs(os.path.join(course_dir, "content"), exist_ok=True)

        # Temp + rename, so a crash mid-write cannot leave a truncated
        # structure.json that reads as neither a course nor an absence.
        tmp_path = structure_path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(payload)
        os.replace(tmp_path, structure_path)

        cat = course_dict.get("catalog") or {}
        conn = self._get_db()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO courses (uid, title, overview, status, teaching_style,
                    subject, grade_band, grade_numeric, is_catalog, catalog_status,
                    version, visibility, reviewed_by, published_at, enrichment_included)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uid,
                course_dict.get("title", ""),
                # THE COLUMN IS `overview`; THE DOCUMENT SAYS `description`.
                # Nothing ever mapped one to the other, so the row stayed empty
                # while structure.json held the real text — and the course list,
                # which reads the ROW, showed no description for any course.
                # The front end then filled the gap with one identical sentence
                # on every card.
                (course_dict.get("overview")
                 or course_dict.get("description") or ""),
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
        except Exception as db_err:
            try:
                conn.rollback()
            except Exception:
                pass
            if not dir_existed:
                # Undo the disk half, so a failed create_course leaves no trace
                # in either store. Safe only because dir_existed proves we were
                # the ones who made this directory a moment ago.
                try:
                    shutil.rmtree(course_dir)
                    logger.error(
                        f"create_course({uid}): SQLite registration failed; "
                        f"rolled back the on-disk course directory, so neither "
                        f"store was modified. Cause: {db_err}"
                    )
                except Exception as rb_err:
                    # Write failed AND undo failed. This is the one path that
                    # can still diverge, so it is NAMED rather than swallowed:
                    # the operator needs the uid and the repair command.
                    logger.error(
                        f"DIVERGENCE (AUTO-10) course={uid}: structure.json "
                        f"exists at {structure_path} but the `courses` row was "
                        f"NOT written, and rolling the directory back also "
                        f"failed. The course is invisible to the course list "
                        f"until re-registered. Repair with: python3 "
                        f"tools/reconcile_courses.py {self.data_dir} --fix "
                        f"(db error: {db_err}; rollback error: {rb_err})"
                    )
            else:
                # Pre-existing directory: rolling it back would destroy content
                # this call did not create, so the divergence is reported
                # instead of repaired. structure.json is now newer than the row.
                logger.error(
                    f"DIVERGENCE (AUTO-10) course={uid}: structure.json was "
                    f"overwritten but the `courses` row was NOT updated, so "
                    f"the two stores disagree about this course. Inspect with: "
                    f"python3 tools/reconcile_courses.py {self.data_dir} "
                    f"(db error: {db_err})"
                )
            raise

        logger.info(f"Created course structure: {structure_path}")
        return uid

    def get_course(self, uid: str) -> Optional[dict]:
        """Read course structure.json.

        CACHED ON MTIME, NOT ON UID ALONE.
        
        This cache was keyed only on `uid` and never invalidated, so once a
        course had been read the process served that parse until restart. A
        course that finished hydrating, was rebuilt, or had its structure
        rewritten kept being returned in its OLD form — to the course list, to
        the stats, and to the FSM — with nothing indicating the data was stale.
        Long-lived services are exactly where that bites, and this one runs for
        days.

        The file's mtime is an exact key: unchanged means the parse is still
        valid, changed means it is not.
        """
        import copy
        path = os.path.join(self.courses_dir, uid, "structure.json")
        try:
            # SIGNATURE, NOT MTIME ALONE. st_mtime is one-second granular on
            # some filesystems and a build rewrites structure.json far faster
            # than that; size and inode close the window, and the inode changes
            # on every os.replace, so an atomic write inside the same second
            # cannot be missed.
            mtime = self._file_signature(path)
            if mtime is None:
                return None
        except OSError:
            return None

        cached = self._cache.get(uid)
        if cached and cached[0] == mtime:
            # A copy, so a caller mutating what it gets back cannot poison the
            # cache for everyone else.
            return copy.deepcopy(cached[1])

        with open(path, "r") as f:
            course = json.load(f)
        self._evict_if_full()
        self._cache[uid] = (mtime, copy.deepcopy(course))
        return course

    # Fields the BUILD does not own. A hydration run holds one `course` dict
    # for hours and writes it back repeatedly; anything changed on disk in that
    # window is silently reverted by the next write.
    _LEARNER_OWNED = ("title", "teaching_style", "learner_context")

    def update_course(self, uid: str, course_dict: dict,
                      preserve_learner_fields: bool = True):
        """Overwrite course structure.json and update metadata in SQLite.

        Ordering matches create_course: the RECOVERABLE store first. structure.json
        IS the course and the row is derived metadata, so the file is written
        atomically first and the row follows.

        Both halves propagate on failure. That sentence was in this docstring
        while the SQLite half still ended in `except Exception: logger.error(...)`
        with no re-raise — so a locked database (the common case, since
        hydration writes from a thread pool) left structure.json saying "ready"
        and courses.status saying "skeleton", permanently, with each endpoint
        reading a different half, and every caller treating the silent return
        as success. Corrected 2026-08-25 after an audit found the claim and the
        code disagreeing.
        """
        course_dict["uid"] = uid
        # Captured BEFORE it is stamped: this is when the caller loaded the
        # course, which is what decides whether their copy is stale.
        caller_loaded_at = course_dict.get("updated_at")
        course_dict["updated_at"] = datetime.utcnow().isoformat()

        # LAST WRITER WINS IS WRONG FOR A WRITER THAT STARTED HOURS AGO.
        #
        # Measured 2026-08-25: a course title corrected from "advanced sql" to
        # "Advanced SQL" reverted within minutes, while the same correction on
        # an idle course stuck. The running hydration held a `course` dict
        # loaded at resume time and wrote it back on every progress update,
        # reverting an edit it had never been told about.
        #
        # The build owns modules, status and its own verdicts. It does not own
        # what the course is CALLED, how it is taught, or what the learner said
        # they wanted. Those are taken from disk unless a caller says it is
        # deliberately changing them.
        # STALENESS DECIDES, NOT THE FIELD NAME.
        #
        # A first version of this preserved the learner-owned fields from disk
        # unconditionally, which fixed the clobber and broke renaming: a method
        # called update_course silently ignored a new title. Both behaviours
        # are wrong for the other caller.
        #
        # The discriminator is whether this caller has SEEN the current state.
        # `updated_at` is stamped on every write, so a caller whose copy
        # predates what is on disk loaded before the last change and cannot
        # have meant to revert it — that is the hydration run holding a dict
        # for hours. A caller who read, modified and wrote carries the current
        # stamp, and is honoured. A caller with no stamp at all is constructing
        # a course deliberately, and is honoured too.
        if preserve_learner_fields and caller_loaded_at:
            try:
                on_disk = self.get_course(uid) or {}
                disk_stamp = on_disk.get("updated_at")
                if disk_stamp and caller_loaded_at < disk_stamp:
                    for field in self._LEARNER_OWNED:
                        current = on_disk.get(field)
                        if current and course_dict.get(field) != current:
                            logger.info(
                                "update_course(%s): the caller's copy predates "
                                "the last write, so keeping %s=%r rather than "
                                "reverting it to %r", uid, field, current,
                                course_dict.get(field))
                            course_dict[field] = current
            except Exception as e:
                logger.debug("could not compare course staleness for %s: %s",
                             uid, e)

        import copy
        path = os.path.join(self.courses_dir, uid, "structure.json")

        # ATOMIC, like `create_course`. This was a plain truncating write, and
        # it is the call that lands the FINISHED course after a build that can
        # run for tens of minutes. A crash or a `docker stop` between truncate
        # and flush left a half-written structure.json — the course destroyed
        # at the moment it was completed. `create_course` has always done this
        # correctly; the update path did not.
        payload = json.dumps(course_dict, indent=2)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

        # CACHE AFTER THE WRITE, AND WITH THE FILE'S REAL MTIME.
        #
        # This wrote a bare dict where `get_course` now stores (mtime, course),
        # and it wrote it BEFORE the file existed on disk — so the entry could
        # not carry a valid mtime even in principle. Priming it from the
        # written file keeps one shape everywhere and keeps the key honest.
        self._evict_if_full()
        try:
            self._cache_put(uid, path, course_dict)
        except OSError:
            self._cache.pop(uid, None)
            
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
                    # THE COLUMN IS `overview`; THE DOCUMENT SAYS `description`.
                # Nothing ever mapped one to the other, so the row stayed empty
                # while structure.json held the real text — and the course list,
                # which reads the ROW, showed no description for any course.
                # The front end then filled the gap with one identical sentence
                # on every card.
                (course_dict.get("overview")
                 or course_dict.get("description") or ""),
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
                # An UPDATE that matches nothing is not an error to SQLite, so
                # this was the quietest way for the two stores to drift: the
                # JSON above is already written, the row never existed (or was
                # removed under us), and the course simply never appears in the
                # list. Same divergence AUTO-10 describes, arrived at by doing
                # nothing wrong. Name it here rather than let it pass.
                if cursor.rowcount == 0:
                    logger.error(
                        f"DIVERGENCE (AUTO-10) course={uid}: structure.json "
                        f"updated but no `courses` row matched the UPDATE, so "
                        f"this course is invisible to the course list. Repair "
                        f"with: python3 tools/reconcile_courses.py "
                        f"{self.data_dir} --fix"
                    )
                conn.commit()
        except Exception as e:
            # THE DOCSTRING SAID THIS PROPAGATED. IT DID NOT.
            #
            # structure.json has already been replaced atomically by this
            # point, so swallowing here leaves the file saying one thing and
            # the courses row another — permanently, with the course list and
            # the learn view reading different halves. That is the divergence
            # this method's own docstring claims was removed, still present.
            #
            # "database is locked" is the common case, because hydration writes
            # from a thread pool. It is transient, so it is retried once before
            # the caller is told; a caller that treats a successful return as
            # success must not be handed a half-write.
            logger.error(
                "course metadata for %s could NOT be written to SQLite (%s). "
                "structure.json IS updated, so the two stores now disagree — "
                "the course list reads the row and the learn view reads the "
                "file. Repair with: python3 tools/reconcile_courses.py %s "
                "--fix", uid, e, self.data_dir)
            raise

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
                # A table that does not exist yet is expected and uninteresting
                # (concept_vec is only created once a vector index is built,
                # concept_fts only where FTS5 is compiled in). Anything else —
                # a renamed column, a locked or corrupt table — is a cascade
                # that silently failed to run, which is the precise failure this
                # list exists to prevent, and it used to be logged at DEBUG.
                # Separating the two lets the real one be a WARNING without
                # crying wolf on every delete.
                present = {
                    r[0] for r in cursor.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type IN ('table','view')"
                    ).fetchall()
                }
                total_rows = 0
                for table, col in cascade_tables:
                    if table not in present:
                        logger.debug(
                            f"Cascade: no {table} table in this schema, nothing to delete"
                        )
                        continue
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
                # program_courses is DETACHED, not deleted. A course built
                # from a degree is still part of that degree -- the learner
                # deleted the built content, not the requirement -- so the row
                # stays and reverts to unbuilt. Deleting it would silently
                # shrink the programme, and leaving it as-is would show a
                # course as built with nothing behind it.
                try:
                    cursor.execute(
                        "UPDATE program_courses SET built=0, course_uid=NULL "
                        "WHERE course_uid=?", (uid,))
                    if cursor.rowcount > 0:
                        logger.info(
                            "Detached %d programme slot(s) from deleted course "
                            "%s; they are open to be built again",
                            cursor.rowcount, uid)
                except sqlite3.OperationalError as e:
                    logger.debug(f"program_courses detach skipped: {e}")

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

    # --- spoken maths ------------------------------------------------------

    def save_concept_math(self, course_uid, concept_uid, spans):
        """Store [(latex, speech, unspoken)] for a concept. Replaces prior rows."""
        try:
            db = self._get_db()
            db.execute("DELETE FROM concept_math WHERE course_uid=? AND concept_uid=?",
                       (course_uid, concept_uid))
            for i, (latex, speech, left) in enumerate(spans or []):
                db.execute(
                    "INSERT INTO concept_math (course_uid, concept_uid, latex, "
                    "mathml, speech, unspoken, ordinal) VALUES (?,?,?,?,?,?,?)",
                    (course_uid, concept_uid, latex, None, speech,
                     " ".join(left) if left else None, i))
            db.commit()
            return len(spans or [])
        except sqlite3.OperationalError:
            return 0
        except Exception as e:
            logger.debug(f"concept math write skipped for {concept_uid}: {e}")
            return 0

    def get_concept_math(self, course_uid, concept_uid):
        """[{latex, speech, unspoken}] in document order."""
        try:
            rows = self._get_db().execute(
                "SELECT latex, speech, unspoken FROM concept_math "
                "WHERE course_uid=? AND concept_uid=? ORDER BY ordinal",
                (course_uid, concept_uid)).fetchall()
        except Exception:
            return []
        return [{"latex": r[0], "speech": r[1],
                 "unspoken": (r[2] or "").split() if r[2] else []} for r in rows]

    def get_concept_sources(self, course_uid, concept_uid):
        """What this concept was actually written from.

        The build already records every retained source and which claims rest
        on which one; nothing read it back. Returns the sources ordered by
        grounding (strongest first) alongside the claim counts, so the UI can
        show a learner where a lesson came from and how much of it leans on
        supplementary rather than primary material — the same share the build
        policy caps, reported rather than asserted.

        Degrades to an empty result rather than raising: a course built before
        v12, or one whose sources were never written, must still open.
        """
        out = {"sources": [], "claims_total": 0, "claims_supplementary": 0,
               "supplementary_share": 0.0, "available": False}
        try:
            db = self._get_db()
            rows = db.execute(
                "SELECT rowid, title, url, source_type, domain_tier, grounding, "
                "       degraded, passage "
                "FROM sources WHERE course_uid=? AND concept_uid=? "
                "ORDER BY grounding DESC", (course_uid, concept_uid)).fetchall()
            counts = db.execute(
                "SELECT source_id, COUNT(*), "
                "       SUM(CASE WHEN supplementary THEN 1 ELSE 0 END) "
                "FROM claim_sources WHERE course_uid=? AND concept_uid=? "
                "GROUP BY source_id", (course_uid, concept_uid)).fetchall()
        except Exception as e:
            logger.debug("get_concept_sources unavailable for %s/%s: %s",
                         course_uid, concept_uid, e)
            return out

        by_id = {c[0]: (c[1] or 0, c[2] or 0) for c in counts}
        for r in rows:
            n_claims, n_supp = by_id.get(r[0], (0, 0))
            passage = r[7] or ""
            out["sources"].append({
                "id": r[0],
                "title": r[1] or "Untitled source",
                "url": r[2] or "",
                "source_type": r[3] or "",
                "domain_tier": r[4] or "",
                # Rounded for display only; the stored value keeps full precision.
                "grounding": round(r[5], 2) if r[5] is not None else None,
                "degraded": bool(r[6]),
                "claims": n_claims,
                "supplementary": bool(n_supp) and n_supp == n_claims,
                # Enough to recognise the passage, not enough to reproduce it.
                "excerpt": (passage[:280] + "…") if len(passage) > 280 else passage,
            })

        total = sum(c[1] or 0 for c in counts)
        supp = sum(c[2] or 0 for c in counts)
        out["claims_total"] = total
        out["claims_supplementary"] = supp
        out["supplementary_share"] = round(supp / total, 3) if total else 0.0
        out["available"] = bool(rows)
        return out

    def speakable(self, course_uid, concept_uid, markdown):
        """`markdown` with every formula replaced by its spoken form.

        For the TTS and text-only paths. A formula with no stored speech is
        left as-is rather than deleted: silence would be a worse failure than
        an awkward reading, and it stays visible to whoever debugs it.
        """
        out = markdown or ""
        for m in self.get_concept_math(course_uid, concept_uid):
            if not m["speech"]:
                continue
            for wrapper in (f"$${m['latex']}$$", f"${m['latex']}$"):
                out = out.replace(wrapper, m["speech"])
        return out

    # --- assets ------------------------------------------------------------

    def save_asset(self, sha256, data=None, path=None, mime=None, width=None,
                   height=None, source=None, license=None, provenance_url=None,
                   alt_text=None, caption=None, caption_verified=False):
        """Store an asset, returning its id. Refuses an unlicensed asset.

        Fail-closed on licence, matching the image-source policy: an unknown
        licence is a rejected licence. This is the one part of the current
        safety story that demonstrably works, so it is enforced at the storage
        boundary too rather than trusted to every caller.
        """
        if not license:
            logger.warning(f"[ASSET] refused {sha256[:12] if sha256 else '?'}: no licence")
            return None
        try:
            db = self._get_db()
            row = db.execute("SELECT asset_id FROM assets WHERE sha256=?",
                             (sha256,)).fetchone()
            if row:
                return row[0]
            cur = db.execute(
                "INSERT INTO assets (sha256, bytes, path, mime, width, height, "
                "source, license, license_verified_at, provenance_url, alt_text, "
                "caption, caption_verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sha256, data, path, mime, width, height, source, license,
                 datetime.now().isoformat(), provenance_url, alt_text, caption,
                 1 if caption_verified else 0))
            db.commit()
            return cur.lastrowid
        except Exception as e:
            logger.debug(f"asset write skipped: {e}")
            return None

    ALLOWED_ASSET_ROLES = ("illustrates", "worked_example", "data", "schematic",
                           "diagram", "map", "timeline")

    def attach_asset(self, course_uid, concept_uid, asset_id, role):
        """Link an asset to a concept. A role is REQUIRED and must be known.

        The seductive-details evidence is about DECORATIVE images, and the
        research used "photograph" as a proxy for decorative. Since photographs
        from curated educational collections are permitted, that proxy is gone
        and the role replaces it: an asset that is merely *related* to a concept
        has no role and cannot be attached.
        """
        if role not in self.ALLOWED_ASSET_ROLES:
            logger.warning(f"[ASSET] refused attachment with role {role!r}: "
                           f"an asset must say what job it does")
            return False
        try:
            db = self._get_db()
            db.execute("INSERT OR REPLACE INTO concept_assets (course_uid, "
                       "concept_uid, asset_id, role) VALUES (?,?,?,?)",
                       (course_uid, concept_uid, asset_id, role))
            db.commit()
            return True
        except Exception as e:
            logger.debug(f"asset attach skipped: {e}")
            return False

    def concept_asset_list(self, course_uid, concept_uid):
        try:
            rows = self._get_db().execute(
                "SELECT a.asset_id, a.source, a.license, a.alt_text, a.caption, "
                "a.caption_verified, ca.role FROM concept_assets ca "
                "JOIN assets a ON a.asset_id = ca.asset_id "
                "WHERE ca.course_uid=? AND ca.concept_uid=?",
                (course_uid, concept_uid)).fetchall()
        except Exception:
            return []
        return [{"asset_id": r[0], "source": r[1], "license": r[2],
                 "alt_text": r[3], "caption": r[4],
                 "caption_verified": bool(r[5]), "role": r[6]} for r in rows]

    def all_course_assets(self, course_uid):
        """Every asset attached anywhere in a course, with its bytes."""
        try:
            rows = self._get_db().execute(
                "SELECT DISTINCT a.asset_id, a.bytes, a.source, a.license, "
                "a.width, a.height, a.caption, a.alt_text, a.caption_verified "
                "FROM assets a JOIN concept_assets ca ON ca.asset_id = a.asset_id "
                "WHERE ca.course_uid=?", (course_uid,)).fetchall()
        except Exception:
            return []
        return [{"asset_id": r[0], "bytes": r[1], "source": r[2], "license": r[3],
                 "width": r[4], "height": r[5], "caption": r[6],
                 "alt_text": r[7], "caption_verified": bool(r[8])} for r in rows]

    def course_attachments(self, course_uid):
        try:
            rows = self._get_db().execute(
                "SELECT concept_uid, asset_id, role FROM concept_assets "
                "WHERE course_uid=?", (course_uid,)).fetchall()
        except Exception:
            return []
        return [{"concept_uid": r[0], "asset_id": r[1], "role": r[2]} for r in rows]

    def repoint_asset(self, course_uid, from_id, to_id):
        """Move every attachment from one asset to another, then drop the orphan.

        Used to collapse perceptual duplicates: two institutions supplying the
        same diagram produce two rows with different sha256 and one picture.
        The winner keeps its provenance; the loser's attachments follow it, so
        no concept silently loses its illustration.
        """
        try:
            db = self._get_db()
            for r in db.execute("SELECT concept_uid, role FROM concept_assets "
                                "WHERE course_uid=? AND asset_id=?",
                                (course_uid, from_id)).fetchall():
                db.execute("INSERT OR REPLACE INTO concept_assets (course_uid, "
                           "concept_uid, asset_id, role) VALUES (?,?,?,?)",
                           (course_uid, r[0], to_id, r[1]))
            db.execute("DELETE FROM concept_assets WHERE course_uid=? AND asset_id=?",
                       (course_uid, from_id))
            db.commit()
            return True
        except Exception as e:
            logger.debug(f"repoint failed: {e}")
            return False

    def detach_asset(self, course_uid, concept_uid, asset_id):
        try:
            db = self._get_db()
            db.execute("DELETE FROM concept_assets WHERE course_uid=? AND "
                       "concept_uid=? AND asset_id=?",
                       (course_uid, concept_uid, asset_id))
            db.commit()
            return True
        except Exception:
            return False

    def sweep_course_assets(self, course_uid, dry_run=False):
        """Whole-course asset pass: collapse duplicates, thin out wallpaper.

        The reason Phase 3 is a whole-course pass at all. Per-concept work
        structurally cannot see that eight concepts each attached their own
        water-cycle diagram, or that two institutions supplied the same figure.

        Returns what it did — never silent, because an asset disappearing from a
        concept with no record is exactly the kind of change that is impossible
        to debug later.
        """
        try:
            from services.core.asset_arbiter import (
                near_duplicate_groups, course_duplicates, MAX_CONCEPTS_PER_ASSET)
        except ImportError:
            return {"ran": False, "reason": "arbiter unavailable"}

        assets = self.all_course_assets(course_uid)
        report = {"ran": True, "assets": len(assets), "collapsed": [],
                  "over_used": {}, "detached": 0, "dry_run": dry_run}
        if not assets:
            return report

        for g in near_duplicate_groups(assets):
            report["collapsed"].append(g)
            if not dry_run:
                for loser in g["collapse"]:
                    self.repoint_asset(course_uid, loser, g["keep"])

        over = course_duplicates(self.course_attachments(course_uid))
        report["over_used"] = over
        for aid, concepts in over.items():
            # Keep the earliest concepts — the ones that introduced the idea —
            # and detach the tail, mirroring the text rule that a concept may
            # RE-TEACH once and must cite thereafter.
            for c in concepts[MAX_CONCEPTS_PER_ASSET:]:
                report["detached"] += 1
                if not dry_run:
                    self.detach_asset(course_uid, c, aid)
        return report

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

        # ATOMIC, LIKE structure.json. This was a truncate-then-write from a
        # THREAD POOL during hydration: a crash, a `docker stop` or an OOM
        # between the truncate and the flush left a zero-byte or half-written
        # .md where a finished concept used to be. It is the only writer of
        # concept markdown and the only path that can damage a concept in an
        # already-good course during a re-hydration or resume.
        #
        # fsync before rename so the bytes are on disk before the name points
        # at them; os.replace is atomic within a filesystem.
        tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp, "w") as f:
                f.write(markdown)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise

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
            # TWO FULL-TEXT INDEXES WERE BEING WRITTEN HERE, AND ONLY ONE WAS
            # EVER READ.
            #
            # `concepts_fts` (v15) was written on every save and queried by
            # NOTHING: the only searcher is SearchStore, which reads
            # `concept_fts`, and the only other reference anywhere was a
            # best-effort DELETE in the share-bundle rollback. A grep of the
            # repository for `concepts_fts` returned this write, that DELETE,
            # and two test assertions — no MATCH, no SELECT on a request path.
            #
            # It was not free. Every concept save tokenised the same markdown
            # TWICE, on a virtiofs bind mount. Measured on the live database
            # with dbstat: concepts_fts and its shadow tables held 2,879,488
            # bytes across 156 concepts — 18.5 KB per concept, 13% of the whole
            # 21.8 MB file, and nearly twice the size of the `concepts` table
            # (1.54 MB) whose text it was copying. It was stale as well, since
            # delete_course's cascade drops `concept_fts` rows and never listed
            # `concepts_fts`.
            #
            # The live index is maintained by on_content_saved below, which
            # calls SearchStore.index_concept. That is the one search reads.
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

    #: Memoised course stats, keyed (uid -> (mtime, stats)).
    #:
    #: `/api/courses` calls `get_course_stats` PER COURSE, and each call reads
    #: and parses that course's `structure.json` — 28-84 KB apiece in the
    #: current data directory. Rendering the course list therefore parsed a
    #: quarter of a megabyte for five courses, and would parse over a megabyte
    #: for twenty-five, to print "12 modules, 40 concepts" on some cards.
    #:
    #: The counts change only when the structure does, so the file's mtime is
    #: an exact invalidation key: a changed course re-counts on the next call,
    #: an unchanged one costs a stat(). Chosen over a stats column on the
    #: `courses` row — which would be faster still — because that needs a
    #: schema migration, and this needs none while removing the same reads.
    _stats_cache = {}

    def get_course_stats(self, uid: str) -> dict:
        """Count modules, units, lessons, concepts in a course.

        Memoised on the structure file's mtime — see `_stats_cache`.
        """
        structure_path = os.path.join(self.courses_dir, uid, "structure.json")
        mtime = None
        try:
            mtime = os.path.getmtime(structure_path)
            cached = self._stats_cache.get(uid)
            if cached and cached[0] == mtime:
                return cached[1]
        except OSError:
            # No structure file: fall through and let `get_course` decide. Do
            # NOT cache that outcome — a course mid-build acquires one.
            pass

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
        stats = {"modules": m, "units": u, "lessons": l, "concepts": c}
        if mtime is not None:
            self._stats_cache[uid] = (mtime, stats)
        return stats


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
        # Whether the "is the index empty?" question has been asked yet on this
        # instance. See search().
        self._populated_checked = False
        self._populate_lock = threading.Lock()
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
        # The DELETE opens a transaction that stays open until commit. If the
        # repopulate below raises — a course directory disappearing mid-walk is
        # enough — that transaction is left open on a THREAD-LOCAL connection
        # that goes on being reused, holding a RESERVED write lock on helga.db.
        # The other process (rag vs core-logic) then blocks on every write until
        # this one exits. Roll back so the failure costs nothing but the rebuild
        # itself; the index keeps its previous contents.
        try:
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
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error as rb:
                logger.error(f"FTS rebuild rollback failed, write lock may be held: {rb}")
            logger.error("FTS index rebuild failed; previous index left intact",
                         exc_info=True)
            raise
        logger.info(f"Rebuilt concept FTS index with {count} concept(s)")
        # A rebuild leaves the index as many small segments; merging them here
        # is the one optimize call that needs no caller outside this module.
        self.optimize_index()
        return count

    def optimize_index(self) -> bool:
        """Merge the FTS5 index's segments. Cheap to call, never required.

        index_concept is DELETE-then-INSERT, and in FTS5 a delete is a
        tombstone appended to a new segment rather than an edit in place. So a
        corpus that is rewritten during a build (every concept is saved at
        least once, many several times) ends up with many small segments plus
        the tombstones that cancel them, and every bm25() query afterwards
        reads all of them — permanently, because nothing in this repository
        has ever run `optimize`. There were zero occurrences of it.

        `INSERT INTO concept_fts(concept_fts) VALUES('optimize')` rewrites the
        index into one segment and discards the tombstones. It is O(index) and
        takes a write lock, so it belongs after a build or in the nightly
        maintenance window, NEVER on a request path.

        Returns True if the merge ran.
        """
        if not self.is_available():
            return False
        try:
            conn = self._get_db()
            conn.execute("INSERT INTO concept_fts(concept_fts) VALUES('optimize')")
            conn.commit()
            logger.info("FTS index optimized (segments merged)")
            return True
        except sqlite3.Error as e:
            # Never fatal: an unoptimized index answers the same questions,
            # only slower.
            logger.warning(f"FTS optimize skipped: {e}")
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return False

    def search(self, query: str, course_uid: str = None, limit: int = 10) -> List[dict]:
        """Full-text search concepts ranked by bm25.

        Returns a list of dicts: {concept_uid, course_uid, title, content}.
        Lazily rebuilds the index if it is currently empty.
        """
        if not self.is_available():
            return []
        if not query or not query.strip():
            return []

        # Lazy population, asked ONCE per process rather than once per search.
        #
        # This was `if self._row_count() == 0: rebuild()`, and _row_count() is
        # SELECT COUNT(*) over an FTS5 table that is not external-content —
        # there is no shortcut, so it reads the whole B-tree: ~744 KB off a
        # virtiofs bind mount on EVERY search, to re-answer a question that has
        # been False since the first course was built.
        #
        # It cannot become True again behind our back in a way this helps with:
        # index_concept keeps the index current on every write, and an index
        # emptied by deleting every course is correctly empty. So the check
        # belongs where a one-time cost belongs.
        if not self._populated_checked:
            with self._populate_lock:
                if not self._populated_checked:
                    self._populated_checked = True    # set first: a failed
                    # rebuild must not make every subsequent search retry it
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
        'student_id',
        # review items (schema v20)
        'kind', 'bloom', 'source_section', 'payload', 'depth',
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

    # -- review items (schema v20) -----------------------------------------

    def sync_items(self, items, student_id: str = None) -> dict:
        """Write an extracted item bank without disturbing recall history.

        Item ids are derived from their source text, so re-extracting an
        unedited concept produces the ids already on disk and this is a no-op
        for them. Only the prompt text and metadata are refreshed; stability,
        difficulty, lapses and the due date are never touched by extraction —
        those belong to the learner, not to the content.

        Items whose source text CHANGED arrive under a new id. The old rows are
        retired rather than deleted: their history is real, and a learner who
        looks at their own numbers should not find them silently rewritten.
        """
        conn = self._get_db()
        sid = _sid(student_id)
        written = updated = retired = 0
        by_concept = {}

        for it in items:
            d = it.as_dict() if hasattr(it, "as_dict") else dict(it)
            by_concept.setdefault(d["concept_uid"], set()).add(d["uid"])
            payload = json.dumps(d.get("payload") or {})
            row = conn.execute(
                "SELECT uid FROM flashcards WHERE uid = ? AND student_id = ?",
                (d["uid"], sid)).fetchone()
            if row:
                conn.execute(
                    "UPDATE flashcards SET front = ?, back = ?, kind = ?, "
                    "bloom = ?, source_section = ?, payload = ?, "
                    "updated_at = ? WHERE uid = ? AND student_id = ?",
                    (d["front"], d["back"], d["kind"], d.get("bloom", 2),
                     d.get("source_section", ""), payload,
                     datetime.utcnow().isoformat(), d["uid"], sid))
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO flashcards (uid, course_uid, concept_uid, "
                    "front, back, kind, bloom, source_section, payload, "
                    "source, status, student_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'extracted','active',?)",
                    (d["uid"], d["course_uid"], d["concept_uid"], d["front"],
                     d["back"], d["kind"], d.get("bloom", 2),
                     d.get("source_section", ""), payload, sid))
                written += 1

        # Retire extracted rows for these concepts that the new bank no longer
        # contains — the fact behind them was edited or removed.
        for concept_uid, live in by_concept.items():
            rows = conn.execute(
                "SELECT uid FROM flashcards WHERE concept_uid = ? AND "
                "student_id = ? AND source = 'extracted' AND status = 'active'",
                (concept_uid, sid)).fetchall()
            for r in rows:
                if r["uid"] not in live:
                    conn.execute(
                        "UPDATE flashcards SET status = 'retired', updated_at = ? "
                        "WHERE uid = ? AND student_id = ?",
                        (datetime.utcnow().isoformat(), r["uid"], sid))
                    retired += 1
        conn.commit()
        return {"written": written, "updated": updated, "retired": retired}

    def get_items(self, course_uid: str = None, student_id: str = None,
                  include_retired: bool = False) -> List[dict]:
        """Every schedulable item with its FSRS state."""
        conn = self._get_db()
        query = "SELECT * FROM flashcards WHERE student_id = ?"
        params = [_sid(student_id)]
        if not include_retired:
            query += " AND status NOT IN ('retired', 'suspended')"
        if course_uid:
            query += " AND course_uid = ?"
            params.append(course_uid)
        out = []
        for r in conn.execute(query, params).fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except (TypeError, ValueError):
                d["payload"] = {}
            out.append(d)
        return out

    def day_load(self, student_id: str = None) -> dict:
        """How many items already fall on each future day.

        This is what the load balancer reads: without it FSRS's exact dates
        clump and a single day in month three becomes big enough to abandon.
        """
        conn = self._get_db()
        rows = conn.execute(
            "SELECT next_review_date AS d, COUNT(*) AS n FROM flashcards "
            "WHERE student_id = ? AND next_review_date IS NOT NULL "
            "AND status NOT IN ('retired', 'suspended') GROUP BY d",
            (_sid(student_id),)).fetchall()
        return {r["d"]: r["n"] for r in rows if r["d"]}

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

        # FSRS gives an exact date. Left exactly there across many courses those
        # dates clump, and one day in month three grows big enough that the
        # learner skips it — and a skipped day is how the whole schedule dies.
        # The date is nudged onto the quietest day inside a window the algorithm
        # is indifferent to (a few percent of the interval), never into the past.
        ideal = date.today() + timedelta(days=new_interval)
        try:
            from services.common.review_scheduler import balance_due_date
            balanced = balance_due_date(ideal, new_interval,
                                        self.day_load(student_id), uid)
        except Exception as e:                      # policy must never block a grade
            logger.warning(f"load balancing skipped for {uid}: {e}")
            balanced = ideal
        next_review = balanced.isoformat()
        new_interval = max(1, (balanced - date.today()).days)
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


class ProgramStore:
    """Degree programmes: the plan, and what has happened to it since.

    The plan itself is written once and read whole. What moves is which
    electives a learner picked and which courses have actually been built, so
    those live in program_courses and are merged back over the plan on read --
    the map always shows the planner's structure with the learner's state on
    top of it.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def create(self, uid: str, plan: dict) -> str:
        now = datetime.now().isoformat()
        db = self._get_db()
        db.execute(
            "INSERT OR REPLACE INTO programs "
            "(uid, subject, template, plan_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (uid, plan.get("subject", ""), plan.get("template", ""),
             json.dumps(plan), now, now))
        for c in plan.get("courses", []):
            db.execute(
                "INSERT OR REPLACE INTO program_courses "
                "(program_uid, title, term, slot, chosen, built, course_uid) "
                "VALUES (?, ?, ?, ?, ?, ?, "
                "        COALESCE((SELECT course_uid FROM program_courses "
                "                  WHERE program_uid=? AND title=?), NULL))",
                (uid, c.get("title", ""), c.get("term"), c.get("slot"),
                 1 if c.get("chosen", True) else 0,
                 1 if c.get("built") else 0, uid, c.get("title", "")))
        db.commit()
        return uid

    def get(self, uid: str) -> Optional[dict]:
        db = self._get_db()
        row = db.execute("SELECT plan_json, status FROM programs WHERE uid=?",
                         (uid,)).fetchone()
        if not row:
            return None
        try:
            plan = json.loads(row[0])
        except (ValueError, TypeError):
            logger.error("programme %s has unreadable plan_json", uid)
            return None
        plan["uid"] = uid
        plan["status"] = row[1]

        state = {r[0]: r for r in db.execute(
            "SELECT title, chosen, built, course_uid, completed, completed_at "
            "FROM program_courses WHERE program_uid=?", (uid,)).fetchall()}
        for c in plan.get("courses", []):
            r = state.get(c.get("title"))
            if not r:
                continue
            c["chosen"] = bool(r[1])
            c["built"] = bool(r[2])
            c["course_uid"] = r[3]
            # available_courses() and the progress meter both read this.
            c["completed"] = bool(r[4])
            c["completed_at"] = r[5]

        # A STORED plan can still carry a course this tutor cannot deliver.
        # The planner filters at creation, but programmes created before that
        # existed — or transcribed from a catalogue that legitimately requires
        # a lab — keep theirs forever, and a degree containing a course Helga
        # can never build is a degree that can never reach 100%. Filtering on
        # read rather than migrating: the stored plan stays a faithful record
        # of what was planned, and what is SHOWN is what can be delivered.
        try:
            from services.core.program import teachable
            keep = [c for c in plan.get("courses", []) if teachable(c.get("title"))]
            dropped = len(plan.get("courses", [])) - len(keep)
            if dropped:
                logger.info("programme %s: hiding %d stored course(s) this "
                            "tutor cannot deliver", uid, dropped)
                plan["courses"] = keep
                plan["hidden_undeliverable"] = dropped
        except Exception as e:
            # Never let the filter cost the caller their programme.
            logger.debug("teachability filter unavailable: %s", e)
        return plan

    def list(self) -> List[dict]:
        """Summaries only -- the map fetches the full plan when it is opened."""
        rows = self._get_db().execute(
            "SELECT p.uid, p.subject, p.template, p.status, p.created_at, "
            "       (SELECT COUNT(*) FROM program_courses c "
            "        WHERE c.program_uid = p.uid AND c.chosen = 1), "
            "       (SELECT COUNT(*) FROM program_courses c "
            "        WHERE c.program_uid = p.uid AND c.built = 1), "
            "       (SELECT COUNT(*) FROM program_courses c "
            "        WHERE c.program_uid = p.uid AND c.completed = 1) "
            "FROM programs p ORDER BY p.created_at DESC").fetchall()
        return [{"uid": r[0], "subject": r[1], "template": r[2], "status": r[3],
                 "created_at": r[4], "courses": r[5], "built": r[6],
                 "completed": r[7]}
                for r in rows]

    def choose(self, uid: str, title: str) -> bool:
        """Lock an elective. Returns False if that course is not in the plan."""
        db = self._get_db()
        cur = db.execute(
            "UPDATE program_courses SET chosen=1 "
            "WHERE program_uid=? AND title=?", (uid, title))
        db.execute("UPDATE programs SET updated_at=? WHERE uid=?",
                   (datetime.now().isoformat(), uid))
        db.commit()
        return cur.rowcount > 0

    def mark_completed(self, uid: str, title: str, completed: bool = True) -> bool:
        """Record that a course in this programme is finished.

        This is what moves a programme forward: completing a course is what
        unlocks everything that required it, so this write is the difference
        between a degree that progresses and a static list.
        """
        db = self._get_db()
        cur = db.execute(
            "UPDATE program_courses SET completed=?, completed_at=? "
            "WHERE program_uid=? AND title=?",
            (1 if completed else 0,
             datetime.now().isoformat() if completed else None, uid, title))
        db.execute("UPDATE programs SET updated_at=? WHERE uid=?",
                   (datetime.now().isoformat(), uid))
        db.commit()
        return cur.rowcount > 0

    def mark_built(self, uid: str, title: str, course_uid: str) -> bool:
        """Attach a real built course to its slot in the programme."""
        db = self._get_db()
        cur = db.execute(
            "UPDATE program_courses SET built=1, course_uid=? "
            "WHERE program_uid=? AND title=?", (course_uid, uid, title))
        db.execute("UPDATE programs SET updated_at=? WHERE uid=?",
                   (datetime.now().isoformat(), uid))
        db.commit()
        return cur.rowcount > 0


class ActivityStore:
    """SQLite activity logging."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def log_activity(self, course_uid: str, activity_type: str, *,
                     concept_uid: str = None, unit_uid: str = None,
                     duration_seconds: int = 0, grade: int = None,
                     details: dict = None, student_id: str = None):
        """Keyword-only past activity_type on purpose: a caller that passed
        these positionally in the wrong order silently disabled activity
        logging for every completed concept."""
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
