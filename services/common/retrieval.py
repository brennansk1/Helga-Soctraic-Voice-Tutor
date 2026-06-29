"""Retrieval helpers shared across services.

Currently provides Reciprocal Rank Fusion (RRF) — the score-normalization-free way
to combine multiple ranked result lists (e.g. lexical FTS5 + dense vector search)
into one hybrid ranking. This is the reusable core of the planned hybrid retrieval
pipeline (B2); the dense half (sqlite-vec + embeddings + reranker) is added in a
runtime-validated task, but RRF is pure and unit-tested here.

Reference: Cormack et al., "Reciprocal Rank Fusion outperforms Condorcet…", and the
widely-used default constant k=60.
"""
from typing import Callable, Hashable, Iterable, List, Sequence, Tuple, Any


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Iterable[Any]],
    k: int = 60,
    key: Callable[[Any], Hashable] = lambda x: x,
) -> List[Tuple[Any, float]]:
    """Fuse several best-first ranked lists by Reciprocal Rank Fusion.

    score(d) = sum over lists of 1 / (k + rank_in_list(d))   (rank is 0-based)

    Args:
        ranked_lists: an iterable of ranked lists, each ordered best-first.
        k: RRF constant. 60 is the standard default; 10-20 can suit very small
           corpora (see research notes). Larger k flattens the rank weighting.
        key: maps an item to a hashable identity for deduping the same document
             that appears in multiple lists (e.g. a concept uid). Defaults to the
             item itself.

    Returns:
        List of (item, fused_score) sorted by fused_score descending. When the
        same identity appears in multiple lists the first-seen item object is
        returned with the summed score. Ties keep first-seen order (stable).
    """
    if k < 1:
        raise ValueError("RRF k must be >= 1")

    scores = {}
    first_item = {}
    order = []
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            ident = key(item)
            if ident not in scores:
                scores[ident] = 0.0
                first_item[ident] = item
                order.append(ident)
            scores[ident] += 1.0 / (k + rank + 1)

    # Sort by score desc, breaking ties by first-seen order for determinism.
    order_index = {ident: i for i, ident in enumerate(order)}
    ranked_idents = sorted(order, key=lambda d: (-scores[d], order_index[d]))
    return [(first_item[d], scores[d]) for d in ranked_idents]
