"""Tests for the RRF hybrid-fusion helper (B2)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from services.common.retrieval import reciprocal_rank_fusion as rrf


def test_single_list_preserves_order():
    fused = rrf([["a", "b", "c"]])
    assert [item for item, _ in fused] == ["a", "b", "c"]


def test_item_in_both_lists_scores_higher():
    # 'b' is mid-rank in both lists; its summed score should beat list-unique items.
    lex = ["a", "b", "c"]
    dense = ["d", "b", "e"]
    fused = rrf([lex, dense], k=60)
    top = [item for item, _ in fused][0]
    assert top == "b"


def test_score_formula_k60():
    # 'a' is rank 0 in one list only: score = 1/(60+0+1).
    fused = dict(rrf([["a"]], k=60))
    assert abs(fused["a"] - 1.0 / 61) < 1e-12


def test_key_dedupes_by_identity():
    # Same document identity from two retrievers, different object instances.
    lex = [{"uid": "u1", "src": "lex"}, {"uid": "u2", "src": "lex"}]
    dense = [{"uid": "u1", "src": "dense"}]
    fused = rrf([lex, dense], key=lambda d: d["uid"])
    idents = [d["uid"] for d, _ in fused]
    assert idents.count("u1") == 1  # not double-counted as two rows
    assert fused[0][0]["uid"] == "u1"  # appears in both -> ranked first
    assert fused[0][0]["src"] == "lex"  # first-seen object retained


def test_smaller_k_sharpens_top_rank_weight():
    # With smaller k, the rank-0 item's share of total score is larger.
    share = lambda k: dict(rrf([["a", "b", "c"]], k=k))["a"] / sum(
        s for _, s in rrf([["a", "b", "c"]], k=k)
    )
    assert share(10) > share(60)


def test_invalid_k_raises():
    try:
        rrf([["a"]], k=0)
        assert False, "expected ValueError"
    except ValueError:
        pass
