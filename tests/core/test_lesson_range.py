"""The lesson budget is a range, not a number.

A fixed count and over-stretch detection pull against each other: if the evidence
supports 36 concepts and the ladder demands 144, the builder pads -- the exact
hollow-content failure scope_fit exists to prevent.

Real courses vary anyway. MIT 18.06 runs 34 lectures against a nominal 15x3 = 45
calendar, and it is a well-regarded course rather than a deficient one.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.course_builder import (  # noqa: E402
    COURSE_PRESETS, LESSON_TOLERANCE, compute_course_params)


class TestTheRangeExists(unittest.TestCase):
    def test_every_preset_reports_a_range(self):
        for key, v in COURSE_PRESETS.items():
            p = compute_course_params(v["scope"], v["mastery"], v["starting_from"])
            assert p["lessons_min"] <= p["lessons_total"] <= p["lessons_max"], key
            assert p["lessons_min"] < p["lessons_max"], f"{key} has no range"

    def test_the_range_admits_a_real_courses_actual_length(self):
        """MIT 18.06's 34 lectures must sit INSIDE a College Course range whose
        nominal is 45, or the instrument calls a real semester course short."""
        p = compute_course_params(3, 3, 2)
        assert p["lessons_min"] <= 34 <= p["lessons_max"], \
            f"34 lectures falls outside {p['lessons_min']}-{p['lessons_max']}"

    def test_the_tolerance_covers_the_observed_variation(self):
        """34 against a nominal 45 is -24%, so a tighter tolerance would flag it."""
        assert LESSON_TOLERANCE >= 0.24


class TestItRemainsAnchoredNotArbitrary(unittest.TestCase):
    def test_the_target_is_still_the_calendar(self):
        """A range is not permission to drift: the centre is 15 weeks x 3."""
        assert compute_course_params(3, 3, 2)["lessons_total"] == 45

    def test_a_short_preset_gets_a_proportionally_short_range(self):
        deep = compute_course_params(1, 5, 3)          # Deep Dive
        college = compute_course_params(3, 3, 2)
        assert deep["lessons_max"] < college["lessons_min"], \
            "a deep dive must not overlap a semester course"

    def test_per_module_minimum_is_below_the_per_module_target(self):
        """The schema floor uses the minimum; if it equalled the target the range
        would be decorative and a thin module could never settle low."""
        p = compute_course_params(3, 3, 2)
        assert p["lessons_per_module_min"] <= p["lessons_per_module"]


if __name__ == "__main__":
    unittest.main()
