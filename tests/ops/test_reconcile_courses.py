"""Tests for tools/reconcile_courses.py — the courses-table/disk reconciler.

The tool exists because the two stores diverged on real data (19 rows, 3
directories). What matters here is not just that it *detects* both directions
of divergence, but that --fix stays conservative: it must never delete anything
on disk, and it must not quietly discard the last remaining record of a course
that was finished.
"""

import io
import json
import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import reconcile_courses  # noqa: E402


COURSES_DDL = """
CREATE TABLE courses (
    uid TEXT PRIMARY KEY,
    title TEXT,
    overview TEXT,
    status TEXT,
    teaching_style TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    subject TEXT,
    grade_band TEXT,
    grade_numeric INTEGER,
    is_catalog INTEGER DEFAULT 0,
    catalog_status TEXT DEFAULT 'draft',
    version INTEGER DEFAULT 1,
    visibility TEXT DEFAULT 'private',
    reviewed_by TEXT,
    published_at TEXT,
    enrichment_included INTEGER DEFAULT 0
);
CREATE TABLE user_progress (course_uid TEXT, concept_uid TEXT);
CREATE TABLE flashcards (course_uid TEXT, front TEXT);
"""


def make_row(data_dir, uid, title="t", status="skeleton"):
    db_path = os.path.join(data_dir, "helga.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO courses (uid, title, status) VALUES (?, ?, ?)",
            (uid, title, status),
        )
        conn.execute(
            "INSERT INTO user_progress (course_uid, concept_uid) VALUES (?, ?)",
            (uid, "con_deadbeef"),
        )
        conn.commit()


def make_dir(data_dir, uid, title="t", status="ready", structure=None):
    course_dir = os.path.join(data_dir, "courses", uid)
    os.makedirs(os.path.join(course_dir, "content"), exist_ok=True)
    if structure is None:
        structure = {"uid": uid, "title": title, "status": status, "modules": []}
    with open(os.path.join(course_dir, "structure.json"), "w") as f:
        json.dump(structure, f)
    return course_dir


@pytest.fixture
def data_dir(tmp_path):
    d = str(tmp_path / "data")
    os.makedirs(os.path.join(d, "courses"))
    db_path = os.path.join(d, "helga.db")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(COURSES_DDL)
        conn.commit()
    return d


def run(data_dir, **kwargs):
    out = io.StringIO()
    summary = reconcile_courses.reconcile(data_dir, out=out, **kwargs)
    summary["_output"] = out.getvalue()
    return summary


def row_uids(data_dir):
    with sqlite3.connect(os.path.join(data_dir, "helga.db")) as conn:
        return {r[0] for r in conn.execute("SELECT uid FROM courses")}


# --- direction 1: rows with no directory ------------------------------------

def test_detects_row_without_directory(data_dir):
    make_row(data_dir, "course_aaaaaaaa", status="skeleton")
    summary = run(data_dir)
    assert summary["orphan_rows"] == ["course_aaaaaaaa"]
    assert summary["orphan_dirs"] == []
    # Dry run touches nothing.
    assert row_uids(data_dir) == {"course_aaaaaaaa"}


def test_fix_removes_dead_row_and_cascades(data_dir):
    make_row(data_dir, "course_aaaaaaaa", status="available")
    summary = run(data_dir, apply_fix=True)
    assert summary["removed"] == ["course_aaaaaaaa"]
    assert row_uids(data_dir) == set()
    with sqlite3.connect(os.path.join(data_dir, "helga.db")) as conn:
        n = conn.execute("SELECT COUNT(*) FROM user_progress").fetchone()[0]
    assert n == 0, "related rows must not survive the course they belong to"


# --- direction 2: directories with no row -----------------------------------

def test_detects_directory_without_row(data_dir):
    make_dir(data_dir, "course_bbbbbbbb", title="Quantum Computing")
    summary = run(data_dir)
    assert summary["orphan_dirs"] == ["course_bbbbbbbb"]
    assert summary["orphan_rows"] == []


def test_fix_reregisters_directory_from_structure_json(data_dir):
    make_dir(data_dir, "course_bbbbbbbb", title="Quantum Computing",
             status="ready")
    summary = run(data_dir, apply_fix=True)
    assert summary["registered"] == ["course_bbbbbbbb"]
    with sqlite3.connect(os.path.join(data_dir, "helga.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM courses WHERE uid=?",
                           ("course_bbbbbbbb",)).fetchone()
    assert row["title"] == "Quantum Computing"
    assert row["status"] == "ready", "status comes from the file, not a guess"
    # And the divergence is gone on a second pass.
    assert run(data_dir)["orphan_dirs"] == []


def test_both_directions_at_once(data_dir):
    make_row(data_dir, "course_aaaaaaaa", status="skeleton")
    make_dir(data_dir, "course_bbbbbbbb")
    make_row(data_dir, "course_cccccccc", status="ready")
    make_dir(data_dir, "course_cccccccc")  # matched pair, must be left alone
    summary = run(data_dir)
    assert summary["orphan_rows"] == ["course_aaaaaaaa"]
    assert summary["orphan_dirs"] == ["course_bbbbbbbb"]
    assert summary["rows"] == 2 and summary["dirs"] == 2


def test_clean_data_dir_reports_no_divergence(data_dir):
    make_row(data_dir, "course_cccccccc", status="ready")
    make_dir(data_dir, "course_cccccccc")
    summary = run(data_dir)
    assert not summary["orphan_rows"] and not summary["orphan_dirs"]
    assert "OK:" in summary["_output"]


# --- conservatism -----------------------------------------------------------

def test_fix_protects_ready_rows_by_default(data_dir):
    make_row(data_dir, "course_dddddddd", title="Causal Inference",
             status="ready")
    summary = run(data_dir, apply_fix=True)
    assert summary["protected"] == ["course_dddddddd"]
    assert summary["removed"] == []
    assert row_uids(data_dir) == {"course_dddddddd"}, \
        "a finished course's last record must survive a default --fix"


def test_include_ready_removes_but_announces_loudly(data_dir):
    make_row(data_dir, "course_dddddddd", title="Causal Inference",
             status="ready")
    summary = run(data_dir, apply_fix=True, include_ready=True)
    assert summary["removed"] == ["course_dddddddd"]
    assert row_uids(data_dir) == set()
    out = summary["_output"]
    assert "REMOVING A `ready` COURSE ROW" in out
    assert "course_dddddddd" in out and "Causal Inference" in out


def test_fix_never_deletes_anything_on_disk(data_dir):
    make_dir(data_dir, "course_bbbbbbbb")
    make_row(data_dir, "course_aaaaaaaa", status="available")
    before = sorted(os.listdir(os.path.join(data_dir, "courses")))
    run(data_dir, apply_fix=True, include_ready=True)
    after = sorted(os.listdir(os.path.join(data_dir, "courses")))
    assert before == after


def test_unreadable_directory_is_reported_not_registered(data_dir):
    course_dir = os.path.join(data_dir, "courses", "course_eeeeeeee")
    os.makedirs(course_dir)
    with open(os.path.join(course_dir, "structure.json"), "w") as f:
        f.write('{"uid": "course_eeeeeeee",')  # truncated mid-build
    summary = run(data_dir, apply_fix=True)
    assert "course_eeeeeeee" in summary["unreadable"]
    assert summary["registered"] == []
    assert row_uids(data_dir) == set()
    assert os.path.exists(os.path.join(course_dir, "structure.json"))


def test_directory_with_no_structure_json_is_left_alone(data_dir):
    os.makedirs(os.path.join(data_dir, "courses", "course_ffffffff", "content"))
    summary = run(data_dir, apply_fix=True)
    assert summary["unreadable"]["course_ffffffff"] == "no structure.json"
    assert summary["registered"] == []


def test_dry_run_opens_the_database_read_only(data_dir):
    """The dry-run guarantee is enforced by SQLite, not by our own care."""
    make_row(data_dir, "course_aaaaaaaa")
    conn = reconcile_courses.open_db(os.path.join(data_dir, "helga.db"),
                                     writable=False)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM courses")
    finally:
        conn.close()


def test_missing_database_is_an_error_not_a_silent_pass(tmp_path):
    with pytest.raises(FileNotFoundError):
        reconcile_courses.reconcile(str(tmp_path), out=io.StringIO())


# --- the hole in storage.py that produced the divergence --------------------

def test_create_course_rolls_back_disk_when_sqlite_fails(tmp_path, monkeypatch):
    """A failed registration must leave neither store holding a half-course."""
    from services.common.storage import CourseStore

    data_dir = str(tmp_path / "data")
    courses_dir = os.path.join(data_dir, "courses")
    os.makedirs(courses_dir)
    with sqlite3.connect(os.path.join(data_dir, "helga.db")) as conn:
        conn.executescript(COURSES_DDL)
        conn.commit()

    store = CourseStore(courses_dir, data_dir)

    class FailingConn:
        """Stands in for the thread-local connection and fails the one
        statement that registers the course, the way a full disk or a locked
        database would."""

        def __init__(self, real):
            self._real = real

        def execute(self, sql, *a, **kw):
            if "INSERT OR REPLACE INTO courses" in sql:
                raise sqlite3.OperationalError("disk I/O error")
            return self._real.execute(sql, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    real_conn = sqlite3.connect(os.path.join(data_dir, "helga.db"))
    monkeypatch.setattr(store, "_get_db", lambda: FailingConn(real_conn))

    with pytest.raises(sqlite3.OperationalError):
        store.create_course({"uid": "course_99999999", "title": "x"})

    real_conn.close()

    assert not os.path.exists(os.path.join(courses_dir, "course_99999999")), \
        "the on-disk half must be rolled back when the row cannot be written"
    with sqlite3.connect(os.path.join(data_dir, "helga.db")) as conn:
        n = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    assert n == 0


def test_create_course_writes_both_stores(tmp_path):
    from services.common.storage import CourseStore

    data_dir = str(tmp_path / "data")
    courses_dir = os.path.join(data_dir, "courses")
    os.makedirs(courses_dir)
    with sqlite3.connect(os.path.join(data_dir, "helga.db")) as conn:
        conn.executescript(COURSES_DDL)
        conn.commit()

    store = CourseStore(courses_dir, data_dir)
    uid = store.create_course({"title": "Linear Algebra", "status": "skeleton"})

    assert os.path.exists(os.path.join(courses_dir, uid, "structure.json"))
    assert not os.path.exists(
        os.path.join(courses_dir, uid, "structure.json.tmp")), \
        "the temp file must not survive the rename"
    with sqlite3.connect(os.path.join(data_dir, "helga.db")) as conn:
        row = conn.execute("SELECT title FROM courses WHERE uid=?",
                           (uid,)).fetchone()
    assert row and row[0] == "Linear Algebra"
    # The freshly created course is, by construction, reconciled.
    summary = run(data_dir)
    assert not summary["orphan_rows"] and not summary["orphan_dirs"]
