"""Tests for SearchStore — SQLite FTS5 full-text search over concepts.

Mirrors the fixture style in tests/core/test_storage.py: a fresh StorageManager
backed by a tmp_path directory.
"""

import os
import sqlite3
import pytest

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.storage import StorageManager


# Skip the whole module if FTS5 isn't compiled into the runtime SQLite build.
def _fts5_available():
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True
    except sqlite3.OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _fts5_available(), reason="FTS5 not compiled into SQLite"
)


@pytest.fixture
def storage(tmp_path):
    """Fresh StorageManager with temp directory."""
    return StorageManager(str(tmp_path))


def _make_course(storage, title, concepts):
    """Create a course with a single module/unit/lesson holding `concepts`.

    `concepts` is a list of (uid, title, body) tuples; body is written to the
    concept .md file so we can test content-body matching.
    """
    uid = storage.courses.create_course({
        "title": title,
        "modules": [{
            "title": "M1", "uid": "m_" + title,
            "units": [{
                "title": "U1", "uid": "u_" + title,
                "lessons": [{
                    "title": "L1", "uid": "l_" + title,
                    "concepts": [
                        {"uid": cu, "title": ct} for cu, ct, _ in concepts
                    ],
                }],
            }],
        }],
    })
    for cu, _, body in concepts:
        storage.courses.save_concept_content(uid, cu, body)
    return uid


class TestIndexBuild:
    def test_rebuild_indexes_all_concepts(self, storage):
        _make_course(storage, "Physics", [
            ("c1", "Newton's Laws", "An object in motion stays in motion."),
            ("c2", "Energy", "Energy is conserved."),
        ])
        count = storage.search.rebuild_search_index()
        assert count == 2

    def test_fts_available(self, storage):
        assert storage.search.is_available() is True


class TestTitleMatch:
    def test_matches_concept_title(self, storage):
        _make_course(storage, "Physics", [
            ("c1", "Newton's Laws of Motion", "body text"),
            ("c2", "Thermodynamics", "heat body"),
        ])
        results = storage.search.search("Newton")
        uids = [r["concept_uid"] for r in results]
        assert "c1" in uids
        assert "c2" not in uids


class TestContentBodyMatch:
    """The key improvement over substring-on-title: matching the body."""

    def test_matches_word_only_in_body(self, storage):
        _make_course(storage, "Biology", [
            ("c1", "Cells", "The mitochondria is the powerhouse of the cell."),
            ("c2", "Plants", "Photosynthesis converts light."),
        ])
        # "mitochondria" appears only in c1's body, not in any title.
        results = storage.search.search("mitochondria")
        uids = [r["concept_uid"] for r in results]
        assert uids == ["c1"]


class TestCourseFilter:
    def test_course_uid_filter_scopes_results(self, storage):
        uid_a = _make_course(storage, "CourseA", [
            ("a1", "Gravity intro", "gravity pulls things down"),
        ])
        uid_b = _make_course(storage, "CourseB", [
            ("b1", "Gravity advanced", "gravity warps spacetime"),
        ])
        all_results = storage.search.search("gravity")
        assert {r["concept_uid"] for r in all_results} == {"a1", "b1"}

        scoped = storage.search.search("gravity", course_uid=uid_b)
        assert [r["concept_uid"] for r in scoped] == ["b1"]
        for r in scoped:
            assert r["course_uid"] == uid_b


class TestRanking:
    def test_more_relevant_ranks_higher(self, storage):
        _make_course(storage, "Search", [
            # c1 mentions "ocean" many times -> stronger bm25 relevance
            ("c1", "Oceans", "ocean ocean ocean tides and ocean currents"),
            ("c2", "Rivers", "a river flows to the ocean eventually"),
        ])
        results = storage.search.search("ocean", limit=10)
        uids = [r["concept_uid"] for r in results]
        assert uids[0] == "c1"
        assert set(uids) == {"c1", "c2"}


class TestEmptyAndNoMatch:
    def test_empty_query_returns_empty(self, storage):
        _make_course(storage, "X", [("c1", "Anything", "some content")])
        assert storage.search.search("") == []
        assert storage.search.search("   ") == []

    def test_no_match_returns_empty(self, storage):
        _make_course(storage, "X", [("c1", "Algebra", "solving equations")])
        assert storage.search.search("zzzznonexistent") == []

    def test_punctuation_query_does_not_error(self, storage):
        _make_course(storage, "X", [("c1", "C++ Pointers", "memory & addresses")])
        # Punctuation must be sanitized so FTS5 doesn't raise a syntax error.
        results = storage.search.search("C++ pointers!")
        uids = [r["concept_uid"] for r in results]
        assert "c1" in uids


class TestLazyRebuild:
    def test_search_lazily_builds_when_empty(self, storage):
        # Create content but never call rebuild explicitly.
        _make_course(storage, "Lazy", [
            ("c1", "Quantum", "superposition and entanglement"),
        ])
        # Index is empty; search() should populate it on demand.
        results = storage.search.search("entanglement")
        assert [r["concept_uid"] for r in results] == ["c1"]
