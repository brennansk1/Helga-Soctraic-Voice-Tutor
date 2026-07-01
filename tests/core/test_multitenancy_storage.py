"""B15.1–B15.3 tests: v4 tenancy migration, legacy backfill, student_id scoping.

Covers the spec-03 §9 acceptance rows that live purely in the storage layer:
migration backfill (row counts preserved, everything lands under stu_legacy0,
user_progress composite-PK rebuild) and storage isolation (student A cannot
read or write student B's progress / flashcards / reviews / activity).
"""

import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.storage import (
    StorageManager, DEFAULT_STUDENT_ID, LEGACY_PARENT_ID,
)


@pytest.fixture
def storage(tmp_path):
    return StorageManager(str(tmp_path))


@pytest.fixture
def two_students(storage):
    """Seed a parent with two students; return their ids."""
    pid = storage.accounts.create_parent("mt@example.com", "hash", "MT Parent", status="active")
    a = storage.accounts.create_student(pid, "Alice", grade_band="3-5")
    b = storage.accounts.create_student(pid, "Bob", grade_band="9-12")
    return a, b


# ---------------------------------------------------------------- migration

class TestV4Migration:
    def test_fresh_db_reaches_v4(self, storage):
        conn = sqlite3.connect(storage.db_path)
        ver = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert ver >= 4
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in ("parents", "students", "enrollments", "consent_records",
                  "subscriptions", "fsm_sessions"):
            assert t in tables, f"missing tenancy table {t}"
        conn.close()

    def test_legacy_pair_seeded(self, storage):
        parent = storage.accounts.get_parent(LEGACY_PARENT_ID)
        student = storage.accounts.get_student(DEFAULT_STUDENT_ID)
        assert parent and parent["status"] == "active"
        assert student and student["parent_id"] == LEGACY_PARENT_ID

    def test_user_progress_composite_pk(self, storage):
        conn = sqlite3.connect(storage.db_path)
        cols = conn.execute("PRAGMA table_info(user_progress)").fetchall()
        pk_cols = [c[1] for c in sorted((c for c in cols if c[5] > 0), key=lambda c: c[5])]
        assert pk_cols == ["student_id", "concept_uid"]
        conn.close()

    def test_v3_data_backfilled_without_loss(self, tmp_path):
        """Build a v3-shape DB by hand, migrate, assert counts + legacy owner."""
        data_dir = str(tmp_path)
        db_path = os.path.join(data_dir, "helga.db")
        os.makedirs(os.path.join(data_dir, "courses"), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE user_progress (
                concept_uid TEXT PRIMARY KEY, course_uid TEXT NOT NULL,
                status TEXT DEFAULT 'locked', grade INTEGER DEFAULT 0,
                easiness_factor REAL DEFAULT 2.5, interval_days INTEGER DEFAULT 0,
                repetitions INTEGER DEFAULT 0, next_review_date TEXT,
                last_review_date TEXT, times_reviewed INTEGER DEFAULT 0,
                times_correct INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                bloom_level INTEGER DEFAULT 1);
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, course_uid TEXT NOT NULL,
                concept_uid TEXT, unit_uid TEXT, activity_type TEXT NOT NULL,
                duration_seconds INTEGER DEFAULT 0, grade INTEGER, details TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE scheduled_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT, course_uid TEXT NOT NULL,
                unit_uid TEXT NOT NULL, unit_title TEXT NOT NULL,
                scheduled_date TEXT NOT NULL, review_number INTEGER DEFAULT 1,
                status TEXT DEFAULT 'pending', completed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE flashcards (
                uid TEXT PRIMARY KEY, course_uid TEXT NOT NULL,
                concept_uid TEXT NOT NULL, front TEXT NOT NULL, back TEXT NOT NULL,
                status TEXT DEFAULT 'new', next_review_date TEXT,
                easiness_factor REAL DEFAULT 2.5, interval_days INTEGER DEFAULT 0,
                repetitions INTEGER DEFAULT 0, stability REAL, difficulty REAL,
                last_review_date TEXT, lapses INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE user_settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            CREATE TABLE courses (uid TEXT PRIMARY KEY, title TEXT, overview TEXT,
                status TEXT, teaching_style TEXT, created_at TEXT DEFAULT (datetime('now')));
            INSERT INTO schema_version (version) VALUES (3);
        """)
        for i in range(5):
            conn.execute(
                "INSERT INTO user_progress (concept_uid, course_uid, status, grade, bloom_level) "
                "VALUES (?, 'course_x', 'completed', 3, 2)", (f"con_{i:08d}",))
        conn.execute("INSERT INTO activity_log (course_uid, activity_type) VALUES ('course_x', 'session')")
        conn.execute("INSERT INTO scheduled_reviews (course_uid, unit_uid, unit_title, scheduled_date) "
                     "VALUES ('course_x', 'unit_1', 'U1', '2026-07-01')")
        conn.execute("INSERT INTO flashcards (uid, course_uid, concept_uid, front, back) "
                     "VALUES ('card_1', 'course_x', 'con_00000000', 'f', 'b')")
        conn.commit()
        conn.close()

        sm = StorageManager(data_dir)  # runs v3→v4

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] >= 4
        rows = conn.execute("SELECT student_id, COUNT(*) c FROM user_progress GROUP BY student_id").fetchall()
        assert len(rows) == 1 and rows[0]["student_id"] == DEFAULT_STUDENT_ID and rows[0]["c"] == 5
        for table, expected in (("activity_log", 1), ("scheduled_reviews", 1), ("flashcards", 1)):
            r = conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE student_id = ?",
                             (DEFAULT_STUDENT_ID,)).fetchone()
            assert r["c"] == expected, f"{table} backfill lost rows"
        # migrated data readable through the scoped store under the legacy default
        p = sm.progress.get_progress("con_00000001")
        assert p and p["status"] == "completed" and p["grade"] == 3 and p["bloom_level"] == 2
        conn.close()

    def test_migration_idempotent(self, tmp_path):
        StorageManager(str(tmp_path))
        sm2 = StorageManager(str(tmp_path))  # re-open: migrations must not re-run/crash
        assert sm2.accounts.get_student(DEFAULT_STUDENT_ID) is not None


# ---------------------------------------------------------------- isolation

class TestStudentIsolation:
    def test_progress_isolated(self, storage, two_students):
        a, b = two_students
        storage.progress.update_progress("con_shared00", "course_x", student_id=a, status="completed", grade=4)
        storage.progress.update_progress("con_shared00", "course_x", student_id=b, status="in_progress", grade=2)
        pa = storage.progress.get_progress("con_shared00", student_id=a)
        pb = storage.progress.get_progress("con_shared00", student_id=b)
        assert pa["status"] == "completed" and pa["grade"] == 4
        assert pb["status"] == "in_progress" and pb["grade"] == 2

    def test_progress_write_never_leaks(self, storage, two_students):
        a, b = two_students
        storage.progress.update_progress("con_aaaa0001", "course_x", student_id=a, status="completed")
        assert storage.progress.get_progress("con_aaaa0001", student_id=b) is None
        assert storage.progress.get_course_progress("course_x", student_id=b) == []

    def test_completion_percentage_isolated(self, storage, two_students):
        a, b = two_students
        for i in range(4):
            storage.progress.update_progress(f"con_pct{i:05d}", "course_p", student_id=a, status="completed")
        assert storage.progress.get_completion_percentage("course_p", 4, student_id=a) == 100.0
        assert storage.progress.get_completion_percentage("course_p", 4, student_id=b) == 0.0

    def test_flashcards_isolated(self, storage, two_students):
        a, b = two_students
        uid_a = storage.flashcards.add_card("course_x", "con_1", "Q", "A", student_id=a)
        assert storage.flashcards.get_due_cards(student_id=b) == []
        due_a = storage.flashcards.get_due_cards(student_id=a)
        assert [c["uid"] for c in due_a] == [uid_a]
        # B cannot update A's card
        storage.flashcards.update_card(uid_a, student_id=b, front="HACKED")
        card = storage.flashcards.get_due_cards(student_id=a)[0]
        assert card["front"] == "Q"

    def test_grade_card_fsrs_cross_student_denied(self, storage, two_students):
        a, b = two_students
        from services.core.fsrs_engine import FSRSEngine
        uid_a = storage.flashcards.add_card("course_x", "con_1", "Q", "A", student_id=a)
        with pytest.raises(ValueError):
            storage.flashcards.grade_card_fsrs(uid_a, 3, FSRSEngine(), student_id=b)

    def test_activity_and_streak_isolated(self, storage, two_students):
        a, b = two_students
        storage.activity.log_activity("course_x", "session", student_id=a)
        assert storage.activity.get_activities(student_id=a) != []
        assert storage.activity.get_activities(student_id=b) == []
        assert storage.activity.get_streak(student_id=a) == 1
        assert storage.activity.get_streak(student_id=b) == 0

    def test_schedule_isolated(self, storage, two_students):
        a, b = two_students
        storage.schedule.schedule_concept_review("course_x", "con_1", "C1", rating=3, student_id=a)
        assert storage.schedule.get_scheduled_reviews(student_id=a) != []
        assert storage.schedule.get_scheduled_reviews(student_id=b) == []
        # B cannot complete A's review
        rid = storage.schedule.get_scheduled_reviews(student_id=a)[0]["id"]
        storage.schedule.complete_review(rid, student_id=b)
        assert storage.schedule.get_scheduled_reviews(student_id=a)[0]["status"] == "pending"

    def test_default_falls_back_to_legacy(self, storage):
        storage.progress.update_progress("con_legacy01", "course_x", status="completed")
        row = storage.progress.get_progress("con_legacy01")
        assert row["student_id"] == DEFAULT_STUDENT_ID


# ---------------------------------------------------------------- new stores

class TestAccountStore:
    def test_parent_roundtrip(self, storage):
        pid = storage.accounts.create_parent("P@Example.COM", "h", "Pat")
        assert storage.accounts.get_parent(pid)["email"] == "p@example.com"
        assert storage.accounts.get_parent_by_email("p@example.com")["id"] == pid

    def test_duplicate_email_rejected(self, storage):
        storage.accounts.create_parent("dup@example.com", "h")
        with pytest.raises(sqlite3.IntegrityError):
            storage.accounts.create_parent("dup@example.com", "h2")

    def test_owns_student(self, storage, two_students):
        a, _ = two_students
        parent = storage.accounts.get_student(a)["parent_id"]
        assert storage.accounts.owns_student(parent, a)
        assert not storage.accounts.owns_student(LEGACY_PARENT_ID, a)

    def test_list_and_archive(self, storage):
        pid = storage.accounts.create_parent("l@example.com", "h")
        s1 = storage.accounts.create_student(pid, "Kid1")
        storage.accounts.create_student(pid, "Kid2")
        assert storage.accounts.count_active_students(pid) == 2
        storage.accounts.update_student(s1, status="archived")
        assert storage.accounts.count_active_students(pid) == 1
        assert len(storage.accounts.list_students(pid, include_archived=True)) == 2

    def test_update_student_whitelist(self, storage, two_students):
        a, _ = two_students
        storage.accounts.update_student(a, grade_band="K-2", parent_id="par_evil")
        s = storage.accounts.get_student(a)
        assert s["grade_band"] == "K-2"
        assert s["parent_id"] != "par_evil"


class TestEnrollmentStore:
    def test_enroll_and_unique(self, storage, two_students):
        a, _ = two_students
        storage.enrollments.enroll(a, "course_x")
        storage.enrollments.enroll(a, "course_x")  # OR IGNORE — no dupe
        assert len(storage.enrollments.list_for_student(a)) == 1

    def test_pending_approval_flow(self, storage, two_students):
        a, _ = two_students
        storage.enrollments.enroll(a, "course_e", course_kind="elective", status="pending_approval")
        assert storage.enrollments.list_for_student(a, status="active") == []
        storage.enrollments.update(a, "course_e", status="active", approved_by="par_x")
        assert storage.enrollments.get(a, "course_e")["status"] == "active"


class TestConsentStore:
    def test_latest_record_wins(self, storage):
        pid = storage.accounts.create_parent("c@example.com", "h")
        storage.consent.record(pid, "coppa_data", True, "v1")
        assert storage.consent.has_consent(pid, "coppa_data")
        storage.consent.record(pid, "coppa_data", False, "v2")
        assert not storage.consent.has_consent(pid, "coppa_data")

    def test_student_scoped_consent(self, storage, two_students):
        a, _ = two_students
        pid = storage.accounts.get_student(a)["parent_id"]
        storage.consent.record(pid, "health_strand6", True, "v1", student_id=a)
        assert storage.consent.has_consent(pid, "health_strand6", student_id=a)
        assert not storage.consent.has_consent(pid, "health_strand6")  # account-level unset


class TestFsmSessionStore:
    def test_upsert_get_delete(self, storage, two_students):
        a, b = two_students
        storage.fsm.upsert(a, '{"state": "SOCRATIC_LEARNING"}')
        storage.fsm.upsert(b, '{"state": "LOBBY"}')
        assert '"SOCRATIC_LEARNING"' in storage.fsm.get(a)["blob"]
        assert '"LOBBY"' in storage.fsm.get(b)["blob"]
        storage.fsm.upsert(a, '{"state": "LOBBY"}')  # replace, not append
        assert '"LOBBY"' in storage.fsm.get(a)["blob"]
        storage.fsm.delete(a)
        assert storage.fsm.get(a) is None
        assert storage.fsm.get(b) is not None
