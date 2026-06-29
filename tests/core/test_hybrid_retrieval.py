"""Tests for optional dense/hybrid retrieval on top of FTS5.

All tests in this file run WITHOUT sqlite-vec or sentence-transformers installed.
They verify:
  - is_dense_available() returns False when sqlite-vec is absent.
  - hybrid_search() with dense unavailable returns exactly the FTS5 results.
  - hybrid_search() with embed_fn=None returns exactly the FTS5 results.
  - build_dense_index() no-ops gracefully when sqlite-vec is absent.
  - dense_search() returns [] when sqlite-vec is absent.
  - RRF fusion of a synthetic FTS5 list + empty dense list preserves FTS5 order.
  - hybrid_search() never raises — always degrades to FTS5.

No network calls, no model downloads, no optional deps required.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from services.common.storage import StorageManager, SearchStore
from services.common.retrieval import reciprocal_rank_fusion


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def storage(tmp_path):
    """Fresh StorageManager backed by a temp directory (same style as test_search.py)."""
    return StorageManager(str(tmp_path))


def _make_course(storage, title, concepts):
    """Helper: create a minimal course with the given concepts.

    `concepts` is a list of (uid, title, body) tuples.
    """
    uid = storage.courses.create_course({
        "title": title,
        "modules": [{
            "title": "M1",
            "uid": "m_" + title.replace(" ", "_"),
            "units": [{
                "title": "U1",
                "uid": "u_" + title.replace(" ", "_"),
                "lessons": [{
                    "title": "L1",
                    "uid": "l_" + title.replace(" ", "_"),
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


# ---------------------------------------------------------------------------
# is_dense_available()
# ---------------------------------------------------------------------------

class TestIsDenseAvailable:
    def test_returns_false_without_sqlite_vec(self, storage):
        """sqlite-vec is not installed in this test environment."""
        assert storage.search.is_dense_available() is False

    def test_is_dense_available_static_method(self):
        """Callable as a static method directly on the class."""
        assert SearchStore.is_dense_available() is False


# ---------------------------------------------------------------------------
# build_dense_index() — no-op when sqlite-vec absent
# ---------------------------------------------------------------------------

class TestBuildDenseIndex:
    def test_noop_returns_zero_without_sqlite_vec(self, storage):
        """build_dense_index should no-op and return 0, not raise."""
        dummy_embed_fn = lambda texts: [[0.0] * 384 for _ in texts]
        result = storage.search.build_dense_index(dummy_embed_fn)
        assert result == 0

    def test_noop_does_not_raise_with_none_embed_fn(self, storage):
        """Should not raise even if embed_fn is None — guarded by availability check."""
        # None embed_fn is irrelevant when dense is unavailable; the guard fires first.
        result = storage.search.build_dense_index(None)
        assert result == 0


# ---------------------------------------------------------------------------
# dense_search() — returns [] when sqlite-vec absent
# ---------------------------------------------------------------------------

class TestDenseSearch:
    def test_returns_empty_without_sqlite_vec(self, storage):
        query_vec = [0.1] * 384
        result = storage.search.dense_search(query_vec, course_uid=None, limit=10)
        assert result == []

    def test_returns_empty_with_course_uid_filter(self, storage):
        query_vec = [0.0] * 384
        result = storage.search.dense_search(query_vec, course_uid="course_abc", limit=5)
        assert result == []


# ---------------------------------------------------------------------------
# hybrid_search() — degrades to FTS5 when dense unavailable
# ---------------------------------------------------------------------------

def _fts5_available():
    import sqlite3
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _fts5_available(), reason="FTS5 not compiled into SQLite")
class TestHybridSearchDegradesToFTS5:
    def test_no_embed_fn_returns_fts5_results(self, storage):
        """embed_fn=None → hybrid_search must return the FTS5 result unchanged."""
        _make_course(storage, "Science", [
            ("c1", "Photosynthesis", "plants convert sunlight to sugar"),
            ("c2", "Respiration", "cells use oxygen to make ATP"),
        ])
        fts_results = storage.search.search("photosynthesis")
        hybrid_results = storage.search.hybrid_search("photosynthesis", embed_fn=None)
        # Both should contain c1; order and content should match.
        fts_uids = [r["concept_uid"] for r in fts_results]
        hybrid_uids = [r["concept_uid"] for r in hybrid_results]
        assert fts_uids == hybrid_uids

    def test_dense_unavailable_returns_fts5_results(self, storage):
        """When is_dense_available() is False, hybrid_search returns FTS5 results."""
        _make_course(storage, "Math", [
            ("m1", "Calculus", "derivatives and integrals"),
            ("m2", "Algebra", "variables and equations"),
        ])
        # Provide an embed_fn but dense is unavailable — should degrade to FTS5.
        dummy_embed = lambda texts: [[0.5] * 384 for _ in texts]
        fts_results = storage.search.search("calculus")
        hybrid_results = storage.search.hybrid_search("calculus", embed_fn=dummy_embed)
        fts_uids = [r["concept_uid"] for r in fts_results]
        hybrid_uids = [r["concept_uid"] for r in hybrid_results]
        assert fts_uids == hybrid_uids

    def test_hybrid_search_empty_query_returns_empty(self, storage):
        """Empty query must return [] from both FTS5 and hybrid paths."""
        _make_course(storage, "History", [("h1", "Napoleon", "emperor of France")])
        assert storage.search.hybrid_search("") == []
        assert storage.search.hybrid_search("   ") == []

    def test_hybrid_search_does_not_raise_on_bad_embed_fn(self, storage):
        """An embed_fn that raises must NOT propagate — hybrid falls back to FTS5."""
        _make_course(storage, "Bio", [("b1", "DNA", "double helix")])

        def bad_embed(texts):
            raise RuntimeError("model exploded")

        # Should not raise; dense path aborted, returns FTS5 results.
        try:
            result = storage.search.hybrid_search("DNA", embed_fn=bad_embed)
        except Exception as exc:
            pytest.fail(f"hybrid_search raised unexpectedly: {exc}")

        # FTS5 result should still be there.
        uids = [r["concept_uid"] for r in result]
        assert "b1" in uids

    def test_hybrid_respects_limit(self, storage):
        """hybrid_search should honour the limit parameter."""
        concepts = [(f"c{i}", f"Topic {i}", f"content about topic {i}") for i in range(10)]
        _make_course(storage, "LimitTest", concepts)
        result = storage.search.hybrid_search("topic", embed_fn=None, limit=3)
        assert len(result) <= 3

    def test_hybrid_respects_course_uid_filter(self, storage):
        """course_uid filter should propagate to FTS5 fallback."""
        uid_a = _make_course(storage, "CourseA", [("a1", "Gravity", "mass and force")])
        uid_b = _make_course(storage, "CourseB", [("b1", "Gravity advanced", "relativity")])

        result = storage.search.hybrid_search("gravity", embed_fn=None, course_uid=uid_a)
        uids = [r["concept_uid"] for r in result]
        assert "a1" in uids
        assert "b1" not in uids


# ---------------------------------------------------------------------------
# RRF fusion: FTS5 list + empty dense list == FTS5 order (pure logic test)
# ---------------------------------------------------------------------------

class TestRRFFusionWithEmptyDenseList:
    """Test the RRF helper directly with a synthetic FTS5 list and empty dense list.

    This verifies the contract that hybrid_search relies on when dense_results=[].
    """

    def test_fts_only_preserves_order(self):
        """RRF with one non-empty list and one empty list preserves first-list order."""
        fts_list = [
            {"concept_uid": "c1", "title": "First"},
            {"concept_uid": "c2", "title": "Second"},
            {"concept_uid": "c3", "title": "Third"},
        ]
        dense_list = []

        fused = reciprocal_rank_fusion(
            [fts_list, dense_list],
            k=60,
            key=lambda r: r.get("concept_uid", ""),
        )
        result_uids = [item["concept_uid"] for item, _ in fused]
        assert result_uids == ["c1", "c2", "c3"]

    def test_fts_plus_empty_dense_score_equals_fts_alone(self):
        """Scores from [fts, []] must equal scores from [fts] alone."""
        fts_list = [{"concept_uid": "x"}, {"concept_uid": "y"}]

        fused_with_empty = reciprocal_rank_fusion(
            [fts_list, []],
            k=60,
            key=lambda r: r["concept_uid"],
        )
        fused_alone = reciprocal_rank_fusion(
            [fts_list],
            k=60,
            key=lambda r: r["concept_uid"],
        )
        scores_with_empty = {uid: score for item, score in fused_with_empty for uid in [item["concept_uid"]]}
        scores_alone = {uid: score for item, score in fused_alone for uid in [item["concept_uid"]]}
        assert scores_with_empty == scores_alone

    def test_rrf_two_equal_lists_boosts_shared_items(self):
        """An item appearing in both FTS5 and dense lists scores higher than one in only one."""
        # c1 appears in both; c2 only in FTS5.
        fts_list = [{"concept_uid": "c1"}, {"concept_uid": "c2"}]
        dense_list = [{"concept_uid": "c1"}]

        fused = reciprocal_rank_fusion(
            [fts_list, dense_list],
            k=60,
            key=lambda r: r["concept_uid"],
        )
        scores = {uid: score for item, score in fused for uid in [item["concept_uid"]]}
        assert scores["c1"] > scores["c2"], "c1 (in both lists) must outscore c2 (FTS5 only)"

    def test_rrf_empty_fts_and_dense_returns_empty(self):
        """Both lists empty → fused result is empty."""
        fused = reciprocal_rank_fusion([[], []], k=60, key=lambda r: r)
        assert fused == []
