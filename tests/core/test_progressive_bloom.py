"""Tests for the consolidated progressive_bloom helper (B1.1.4)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/core'))

from services.core.course_builder import progressive_bloom


def _old_formula(index, total, floor, ceiling):
    progress = index / max(total - 1, 1)
    return max(floor, min(ceiling, floor + round(progress * (ceiling - floor))))


def test_parity_with_old_inline_formula():
    for total in range(1, 12):
        for index in range(total):
            for floor in range(1, 7):
                for ceiling in range(floor, 7):
                    assert progressive_bloom(index, total, floor, ceiling) == \
                        _old_formula(index, total, floor, ceiling)


def test_ramps_floor_to_ceiling():
    # First module = floor, last module = ceiling.
    assert progressive_bloom(0, 5, 1, 5) == 1
    assert progressive_bloom(4, 5, 1, 5) == 5


def test_monotonic_non_decreasing():
    vals = [progressive_bloom(i, 8, 2, 6) for i in range(8)]
    assert vals == sorted(vals)
    assert all(2 <= v <= 6 for v in vals)


def test_single_module_uses_floor():
    # Matches the prior behavior (index 0 of 1 -> progress 0 -> floor).
    assert progressive_bloom(0, 1, 1, 5) == 1
