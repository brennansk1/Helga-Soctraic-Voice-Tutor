"""Parameter-fidelity tests for the 3-slider course-creation system (Task #11).

Verifies that scope/mastery/starting_from map to a sensible course shape and that
the Bloom floor/ceiling invariant holds across the whole 5x5x5 space.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/core'))
from services.core.course_builder import compute_course_params as ccp

ALL = [(s, m, t) for s in range(1, 6) for m in range(1, 6) for t in range(1, 6)]


def test_bloom_floor_never_exceeds_ceiling():
    # The Task #11 fix: contradictory picks (low mastery + advanced start) must
    # not yield floor > ceiling.
    for s, m, t in ALL:
        p = ccp(s, m, t)
        assert p['bloom_floor'] <= p['bloom_ceiling'], (s, m, t, p['bloom_floor'], p['bloom_ceiling'])


def test_bloom_levels_in_range():
    for s, m, t in ALL:
        p = ccp(s, m, t)
        assert 1 <= p['bloom_floor'] <= 6
        assert 1 <= p['bloom_ceiling'] <= 6


def test_modules_at_least_two():
    for s, m, t in ALL:
        assert ccp(s, m, t)['modules'] >= 2


def test_scope_increases_modules():
    # Holding mastery/start fixed, broader scope => at least as many modules.
    for m in range(1, 6):
        for t in range(1, 6):
            mods = [ccp(s, m, t)['modules'] for s in range(1, 6)]
            assert mods == sorted(mods), (m, t, mods)


def test_mastery_increases_depth():
    # Higher mastery => more concepts/module and a higher Bloom ceiling.
    for s in range(1, 6):
        for t in range(1, 6):
            cpm = [ccp(s, m, t)['concepts_per_module'] for m in range(1, 6)]
            ceil = [ccp(s, m, t)['bloom_ceiling'] for m in range(1, 6)]
            assert cpm == sorted(cpm), (s, t, cpm)
            assert ceil == sorted(ceil), (s, t, ceil)


def test_starting_from_raises_floor():
    # A more advanced starting point => Bloom floor is non-decreasing.
    for s in range(1, 6):
        for m in range(1, 6):
            floors = [ccp(s, m, t)['bloom_floor'] for t in range(1, 6)]
            assert floors == sorted(floors), (s, m, floors)


def test_known_anchor_values():
    # Beginner-comprehensive vs expert anchors.
    beginner = ccp(scope=1, mastery=1, starting_from=1)
    assert beginner['concepts_per_module'] == 3 and beginner['bloom_ceiling'] == 2
    expert = ccp(scope=5, mastery=5, starting_from=1)
    assert expert['concepts_per_module'] == 10 and expert['bloom_ceiling'] == 6
    assert expert['modules'] == 11
