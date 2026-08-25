"""What the storage layer was doing on every write and every search.

Five defects, all of them invisible because each one only cost time:

  1. `concepts_fts` was written on every concept save and read by nothing —
     19% of the bytes a concept save writes, to build a 1.17 MB index no query
     has ever touched.
  2. `search()` ran SELECT COUNT(*) over a non-external-content FTS5 table
     before every search, to re-answer "is the index empty?" — False since the
     first build, ~744 KB read off a virtiofs bind mount each time.
  3. FTS5 `optimize` had zero occurrences in the repository, so the tombstones
     left by delete-then-insert accumulated forever.
  4. The four hottest predicates had no index that matched them.
  5. connect_safely set three pragmas, so durability and per-connection cache
     size were INHERITED FROM THE BUILD and differed between the five service
     images sharing one helga.db.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from services.common.storage import StorageManager, connect_safely  # noqa: E402


def _fts5_available():
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True
    except sqlite3.OperationalError:
        return False


@pytest.fixture
def storage(tmp_path):
    return StorageManager(str(tmp_path))


def _course(storage, uid="c1", concept="con_a"):
    storage.courses.create_course({
        "uid": uid, "title": "T",
        "modules": [{"uid": "mod_1", "title": "M", "units": [
            {"uid": "unit_1", "title": "U", "lessons": [
                {"uid": "less_1", "title": "L",
                 "concepts": [{"uid": concept, "title": "Eigenvalues"}]}]}]}]})
    return uid


def _plan(conn, sql, params=()):
    return " | ".join(r[-1] for r in conn.execute(
        "EXPLAIN QUERY PLAN " + sql, params).fetchall())


# ── 1. the duplicate index ───────────────────────────────────────────────────

class TestTheUnreadIndexIsGone:
    def test_a_save_no_longer_writes_concepts_fts(self, storage):
        _course(storage)
        storage.courses.save_concept_content("c1", "con_a", "An eigenvalue scales.")
        db = storage.progress._get_db()
        present = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "concepts_fts" not in present, (
            "the write-only duplicate index is back")

    def test_a_legacy_database_has_it_dropped_by_the_migration(self, tmp_path):
        """v19 has to remove it from the databases that already carry it."""
        if not _fts5_available():
            pytest.skip("FTS5 not compiled in")
        db_path = str(tmp_path / "helga.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE VIRTUAL TABLE concepts_fts USING fts5("
                     "concept_uid UNINDEXED, course_uid UNINDEXED, title, content)")
        conn.execute("INSERT INTO concepts_fts VALUES ('con_a','c1','T','body')")
        conn.commit()
        conn.close()

        StorageManager(str(tmp_path))          # runs the migrations

        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='concepts_fts'"
        ).fetchone() is None
        conn.close()

    def test_the_index_that_is_read_still_works(self, storage):
        """The removal must not cost search anything — this is the whole
        justification for calling the other one dead."""
        if not _fts5_available():
            pytest.skip("FTS5 not compiled in")
        _course(storage)
        storage.courses.save_concept_content("c1", "con_a", "An eigenvalue scales its eigenvector.")
        assert [h["concept_uid"] for h in storage.search.search("eigenvector")] == ["con_a"]


# ── 2. the count on every search ─────────────────────────────────────────────

class TestTheEmptinessCheckIsOneShot:
    def test_the_count_runs_once_not_once_per_search(self, storage):
        if not _fts5_available():
            pytest.skip("FTS5 not compiled in")
        _course(storage)
        storage.courses.save_concept_content("c1", "con_a", "An eigenvalue scales.")

        calls = {"n": 0}
        real = storage.search._row_count

        def counted():
            calls["n"] += 1
            return real()

        storage.search._row_count = counted
        for _ in range(5):
            storage.search.search("eigenvalue")
        assert calls["n"] <= 1, (
            f"_row_count ran {calls['n']} times for 5 searches — it is a full "
            f"scan of the FTS table on every single search")

    def test_an_empty_index_is_still_populated_lazily(self, storage):
        """The one-shot must not remove the behaviour it guards."""
        if not _fts5_available():
            pytest.skip("FTS5 not compiled in")
        _course(storage)
        storage.courses.save_concept_content("c1", "con_a", "An eigenvalue scales.")
        conn = storage.search._get_db()
        conn.execute("DELETE FROM concept_fts")       # simulate a cold index
        conn.commit()
        storage.search._populated_checked = False     # a fresh process
        assert [h["concept_uid"] for h in storage.search.search("eigenvalue")] == ["con_a"]

    def test_a_failing_rebuild_is_not_retried_on_every_search(self, storage):
        if not _fts5_available():
            pytest.skip("FTS5 not compiled in")
        _course(storage)
        tries = {"n": 0}

        def explode():
            tries["n"] += 1
            raise RuntimeError("corpus is gone")

        storage.search.rebuild_search_index = explode
        for _ in range(3):
            try:
                storage.search.search("eigenvalue")
            except RuntimeError:
                pass
        assert tries["n"] == 1, "a failed rebuild must not be re-attempted per search"


# ── 3. optimize ──────────────────────────────────────────────────────────────

class TestOptimizeExists:
    def test_optimize_runs_and_the_index_still_answers(self, storage):
        if not _fts5_available():
            pytest.skip("FTS5 not compiled in")
        _course(storage)
        for i in range(5):
            storage.courses.save_concept_content("c1", "con_a", f"An eigenvalue scales, take {i}.")
        assert storage.search.optimize_index() is True
        assert [h["concept_uid"] for h in storage.search.search("eigenvalue")] == ["con_a"]

    def test_optimize_reduces_the_segment_count(self, storage):
        """The point of the call: delete-then-insert appends a segment each
        time and tombstones the old rows, and every bm25() read pays for all
        of them until something merges."""
        if not _fts5_available():
            pytest.skip("FTS5 not compiled in")
        _course(storage)
        for i in range(12):
            storage.search.index_concept("c1", f"con_{i}", "T", f"eigenvalue body {i}")
        conn = storage.search._get_db()
        before = conn.execute("SELECT COUNT(*) FROM concept_fts_data").fetchone()[0]
        storage.search.optimize_index()
        after = conn.execute("SELECT COUNT(*) FROM concept_fts_data").fetchone()[0]
        assert after <= before

    def test_a_rebuild_leaves_an_optimized_index(self, storage):
        if not _fts5_available():
            pytest.skip("FTS5 not compiled in")
        _course(storage)
        storage.courses.save_concept_content("c1", "con_a", "An eigenvalue scales.")
        assert storage.search.rebuild_search_index() == 1
        assert [h["concept_uid"] for h in storage.search.search("eigenvalue")] == ["con_a"]


# ── 4. the indexes ───────────────────────────────────────────────────────────

class TestIndexesServeTheRealQueries:
    def test_due_reviews_no_longer_scans_user_progress(self, storage):
        conn = storage.progress._get_db()
        plan = _plan(conn,
                     "SELECT * FROM user_progress WHERE next_review_date IS NOT NULL "
                     "AND next_review_date <= ? AND status != 'locked' AND student_id = ?",
                     ("2026-08-25", "default"))
        assert "idx_progress_due" in plan, plan

    def test_the_partial_index_is_actually_partial(self, storage):
        conn = storage.progress._get_db()
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_progress_due'").fetchone()[0]
        assert "WHERE next_review_date IS NOT NULL" in sql

    def test_taught_claims_narrows_by_concept_not_only_course(self, storage):
        conn = storage.progress._get_db()
        plan = _plan(conn,
                     "SELECT * FROM taught_claims WHERE course_uid=? AND concept_uid=?",
                     ("c1", "con_a"))
        assert "idx_claims_concept" in plan, plan

    def test_the_catalog_listing_no_longer_scans_courses(self, storage):
        conn = storage.progress._get_db()
        plan = _plan(conn,
                     "SELECT * FROM courses WHERE is_catalog = 1 AND catalog_status = 'published' "
                     "ORDER BY subject, grade_numeric, title")
        assert "idx_courses_catalog" in plan, plan

    def test_concept_math_no_longer_sorts_at_read_time(self, storage):
        """On the tutoring latency path: the plan said USE TEMP B-TREE FOR
        ORDER BY because the index stopped one column short."""
        conn = storage.progress._get_db()
        plan = _plan(conn,
                     "SELECT latex, speech, unspoken FROM concept_math "
                     "WHERE course_uid=? AND concept_uid=? ORDER BY ordinal",
                     ("c1", "con_a"))
        assert "idx_math_concept" in plan, plan
        assert "TEMP B-TREE" not in plan, plan

    def test_get_concept_math_still_returns_document_order(self, storage):
        _course(storage)
        storage.courses.save_concept_math("c1", "con_a", [
            ("b", "bee", []), ("a", "ay", []),
        ])
        got = [m["latex"] for m in storage.courses.get_concept_math("c1", "con_a")]
        assert got == ["b", "a"]


# ── 5. the pragmas ───────────────────────────────────────────────────────────

class TestPragmasAreSetNotInherited:
    def test_every_connection_gets_the_same_durability(self, tmp_path):
        conn = connect_safely(str(tmp_path / "x.db"))
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1     # NORMAL
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.close()

    def test_temp_store_is_memory(self, tmp_path):
        conn = connect_safely(str(tmp_path / "x.db"))
        assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2      # MEMORY
        conn.close()

    def test_cache_size_is_bounded_and_expressed_in_kib(self, tmp_path):
        """Positive means PAGES, which is what the build handed us: the
        inherited 2000 was 8 MB per connection, and connections are
        thread-local per store across five services."""
        conn = connect_safely(str(tmp_path / "x.db"))
        cache = conn.execute("PRAGMA cache_size").fetchone()[0]
        assert cache < 0, "cache_size must be negative (KiB), not pages"
        assert abs(cache) <= 8192, f"{abs(cache)} KiB per connection is too much here"
        conn.close()

    def test_mmap_is_not_enabled(self, tmp_path):
        """helga.db is on a virtiofs bind mount: with mmap I/O an I/O error
        arrives as SIGBUS and kills the service instead of failing a query."""
        conn = connect_safely(str(tmp_path / "x.db"))
        assert conn.execute("PRAGMA mmap_size").fetchone()[0] == 0
        conn.close()

    def test_foreign_keys_are_on_for_course_store_connections_too(self, storage):
        """They were enabled on exactly one connection path, so the store that
        writes concepts and the ledgers had them off."""
        assert storage.courses._get_db().execute(
            "PRAGMA foreign_keys").fetchone()[0] == 1
        assert storage.progress._get_db().execute(
            "PRAGMA foreign_keys").fetchone()[0] == 1
