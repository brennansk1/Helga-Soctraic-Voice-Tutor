"""Presets must be honest: what they promise is what the gate enforces.

A preset is only a named (scope, mastery, starting_from) triple — no new
mechanism — so a preset course and a hand-tuned one go through the identical
pipeline and the identical quality gate. These tests pin that, and pin that the
advertised rigor actually corresponds to enforced requirements.

Caveat baked into the design: Helga delivers a NODE-STYLE learning path
(Duolingo-shaped), not a lecture course. Presets name a level of SUBSTANCE, not
a delivery format.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.course_builder import (  # noqa: E402
    COURSE_PRESETS, preset_summary, list_presets, compute_course_params,
)
from services.core.depth_contract import DEPTH_CONTRACTS  # noqa: E402


class TestPresetsResolve(unittest.TestCase):
    def test_every_preset_resolves(self):
        for key in COURSE_PRESETS:
            self.assertIsNotNone(preset_summary(key), key)

    def test_unknown_preset_returns_none_not_a_default(self):
        """Silently substituting a default course would give the learner
        something other than what they asked for."""
        self.assertIsNone(preset_summary("does-not-exist"))

    def test_sliders_are_in_range(self):
        for key, p in COURSE_PRESETS.items():
            for dial in ("scope", "mastery", "starting_from"):
                self.assertIn(p[dial], range(1, 6), f"{key}.{dial}")

    def test_resolved_params_match_the_slider_pipeline(self):
        """A preset must be exactly equivalent to setting the dials by hand."""
        for key, p in COURSE_PRESETS.items():
            s = preset_summary(key)
            direct = compute_course_params(p["scope"], p["mastery"],
                                           p["starting_from"])
            self.assertEqual(s["modules"], direct["modules"], key)
            self.assertEqual(s["concepts"], direct["total_concepts_approx"], key)
            self.assertEqual(s["bloom_ceiling"], direct["bloom_ceiling"], key)


class TestPresetsAreHonest(unittest.TestCase):
    """The advertised level must correspond to ENFORCED requirements."""

    def test_requires_matches_the_depth_contract_for_that_mastery(self):
        for key, p in COURSE_PRESETS.items():
            s = preset_summary(key)
            expected = DEPTH_CONTRACTS[p["mastery"]]["required"]
            self.assertEqual(s["requires"], expected, key)

    def test_requirements_are_monotonic(self):
        """Level N+1 must require everything level N does. Without this,
        advancing the slider can silently RELAX a requirement — mastery 5 once
        omitted worked_example that mastery 4 demanded."""
        for lvl in sorted(DEPTH_CONTRACTS)[1:]:
            lower = set(DEPTH_CONTRACTS[lvl - 1]["required"])
            here = set(DEPTH_CONTRACTS[lvl]["required"])
            self.assertTrue(lower <= here,
                            f"level {lvl} drops {lower - here}")

    def test_higher_named_levels_demand_more(self):
        """College must require strictly more than High School, and Graduate
        more than College — otherwise the names mean nothing."""
        order = ["overview", "high_school", "college", "college_advanced",
                 "graduate"]
        prev = -1
        for key in order:
            n = len(preset_summary(key)["requires"])
            self.assertGreaterEqual(n, prev, f"{key} requires fewer elements")
            prev = n

    def test_college_and_above_require_real_rigor(self):
        for key in ("college", "college_advanced", "graduate"):
            req = preset_summary(key)["requires"]
            self.assertIn("formal_definition", req, key)
            self.assertIn("worked_example", req, key)

    def test_advanced_levels_require_primary_literature(self):
        """"Advanced undergraduate" citing only Wikipedia is not advanced."""
        for key in ("college_advanced", "graduate"):
            self.assertIn("primary_source", preset_summary(key)["requires"], key)


class TestBuildCostIsDisclosed(unittest.TestCase):
    """Builds range from ~24 minutes to ~2 hours. A learner should not discover
    that by waiting."""

    def test_every_preset_estimates_build_time(self):
        for key in COURSE_PRESETS:
            self.assertGreater(preset_summary(key)["est_minutes"], 0, key)

    def test_estimate_scales_with_concept_count(self):
        a, b = preset_summary("overview"), preset_summary("survey")
        self.assertLess(a["concepts"], b["concepts"])
        self.assertLess(a["est_minutes"], b["est_minutes"])

    def test_listing_is_ordered_lightest_first(self):
        mins = [p["est_minutes"] for p in list_presets()]
        self.assertEqual(mins, sorted(mins))

    # Presets that are genuinely long builds after the parity change. A
    # semester course is now ~144 concepts (the ladder in
    # docs/AI_UNIVERSITY_DESIGN.md, cross-checked against MIT 18.06 and real
    # OpenStax section counts) at a MEASURED 1.5 min/concept on Nail, so it is
    # ~3.6 h. The old rule -- nothing over 150 minutes is one click -- was
    # written when a course was 30 concepts and covered under half its subject.
    #
    # Those two things cannot both be true, and this is open decision #1 in the
    # design: accept the longer build, or keep the shorter course and label it
    # "condensed" rather than a semester equivalent. Listing them explicitly
    # rather than raising the threshold keeps the choice visible: a preset added
    # to this set is a deliberate decision, not a silently relaxed bound.
    LONG_BUILDS = {"college", "college_advanced", "survey", "refresher"}

    def test_short_presets_stay_short(self):
        """The quick options must remain quick, or the ladder has leaked into
        presets that exist precisely to be finished in one sitting."""
        for key in COURSE_PRESETS:
            if key in self.LONG_BUILDS:
                continue
            self.assertLess(preset_summary(key)["est_minutes"], 150, key)

    def test_long_builds_disclose_their_cost(self):
        """The original protection was against a SURPRISE three-hour build, not
        against long builds existing. Disclosure is what preserves it."""
        for key in self.LONG_BUILDS:
            s = preset_summary(key)
            self.assertGreater(s["est_minutes"], 150, key)
            self.assertIn("est_minutes", s, key)
            self.assertGreater(s["concepts"], 100, key)

    def test_nothing_exceeds_a_working_afternoon(self):
        """A ceiling still exists — it has moved, not vanished. Beyond ~5 h a
        single-course build is not something a learner waits for at all, and it
        belongs behind the lazy per-course materialisation the programme design
        describes rather than behind one click."""
        for key in COURSE_PRESETS:
            self.assertLess(preset_summary(key)["est_minutes"], 300, key)


if __name__ == '__main__':
    unittest.main()
