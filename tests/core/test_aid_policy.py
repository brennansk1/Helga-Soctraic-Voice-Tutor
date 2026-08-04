"""Tests for WHEN a diagram is shown (B13.11) and where images come from (B13.5).

The policy's job is restraint. A diagram every turn is worse than none —
Mayer's coherence principle is blunt that extraneous visuals reduce learning —
so most of these tests assert that NOTHING happens.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.aid_policy import (      # noqa: E402
    COOLDOWN_TURNS, MAX_AIDS_PER_CONCEPT, AidMoment, decide, prompt_nudge, suggest_kinds,
)


class TestRestraint(unittest.TestCase):
    """Most turns must produce no diagram at all."""

    def test_a_healthy_dialogue_stays_quiet(self):
        for m in [AidMoment(last_grade=3, correct_streak=1, question_count=2,
                            concept_title="Photosynthesis"),
                  AidMoment(last_grade=4, correct_streak=3, question_count=4,
                            concept_title="Photosynthesis"),
                  AidMoment(last_grade=3, question_count=8, concept_title="Fractions")]:
            self.assertEqual(decide(m).action, "none", decide(m).reason)

    def test_expertise_reversal_suppresses_at_high_bloom(self):
        """A diagram that helps a novice can hinder someone already fluent."""
        low = decide(AidMoment(is_concept_opening=True, bloom_level=1,
                               concept_title="The Pythagorean theorem"))
        high = decide(AidMoment(is_concept_opening=True, bloom_level=5,
                                concept_title="The Pythagorean theorem"))
        self.assertTrue(low.wants_aid)
        self.assertFalse(high.wants_aid)

    def test_disabled_produces_nothing(self):
        self.assertEqual(decide(AidMoment(enabled=False, teaching_mode="LECTURE")).action, "none")

    def test_policy_never_raises(self):
        self.assertEqual(decide(None).action, "none")
        self.assertEqual(decide(AidMoment(kinds_shown=None, available_slots=None)).action, "none")


class TestTriggers(unittest.TestCase):
    def test_lecture_is_the_strongest_trigger(self):
        """LECTURE fires when the student said 'I don't know' — prose has
        already been tried and failed, so switching representation is right."""
        self.assertTrue(decide(AidMoment(teaching_mode="LECTURE", last_grade=1,
                                         concept_title="Comparing fractions")).wants_aid)

    def test_concept_opening_on_a_visual_subject(self):
        self.assertTrue(decide(AidMoment(is_concept_opening=True,
                                         concept_title="The Pythagorean theorem")).wants_aid)

    def test_repeated_misses_trigger(self):
        self.assertTrue(decide(AidMoment(miss_streak=2, last_grade=1,
                                         concept_title="The water cycle")).wants_aid)

    def test_a_non_visual_subject_needs_more_than_an_opening(self):
        """An opening alone should not be enough for an abstract topic."""
        self.assertFalse(decide(AidMoment(is_concept_opening=True,
                                          concept_title="Ethics of consent")).wants_aid)


class TestBudgetAndCooldown(unittest.TestCase):
    """The two mechanisms that stop 'a picture every turn'."""

    def test_cooldown_blocks_a_back_to_back_diagram(self):
        d = decide(AidMoment(is_concept_opening=True, turns_since_aid=1,
                             concept_title="The Pythagorean theorem"))
        self.assertEqual(d.action, "none")
        self.assertIn("cooldown", d.reason)

    def test_being_stuck_overrides_the_cooldown(self):
        """The one case where back-to-back is right: they already failed in words."""
        d = decide(AidMoment(teaching_mode="LECTURE", last_grade=1, turns_since_aid=0,
                             concept_title="The Pythagorean theorem"))
        self.assertTrue(d.wants_aid)

    def test_per_concept_budget_is_absolute(self):
        d = decide(AidMoment(teaching_mode="LECTURE", miss_streak=3,
                             aids_shown_this_concept=MAX_AIDS_PER_CONCEPT,
                             concept_title="The Pythagorean theorem"))
        self.assertEqual(d.action, "none")
        self.assertIn("budget", d.reason)

    def test_young_learners_get_a_larger_budget(self):
        m = dict(teaching_mode="LECTURE", miss_streak=3, turns_since_aid=9,
                 aids_shown_this_concept=MAX_AIDS_PER_CONCEPT,
                 concept_title="Counting on a number line")
        self.assertTrue(decide(AidMoment(grade_band="K-2", **m)).wants_aid)
        self.assertFalse(decide(AidMoment(grade_band="9-12", **m)).wants_aid)

    def test_already_shown_kinds_are_flagged_to_avoid(self):
        d = decide(AidMoment(teaching_mode="LECTURE", last_grade=1,
                             kinds_shown=("bars",), concept_title="Rainfall data"))
        self.assertIn("bars", d.avoid_kinds)
        self.assertIn("do NOT repeat", prompt_nudge(d))


class TestReusePreferred(unittest.TestCase):
    """Course-built diagrams beat freshly generated ones: they were validated
    at build time where a retry costs nothing."""

    def test_precomputed_slot_is_reused_not_regenerated(self):
        d = decide(AidMoment(is_concept_opening=True,
                             concept_title="The Pythagorean theorem",
                             available_slots=("opening",)))
        self.assertEqual(d.action, "reuse")
        self.assertEqual(d.slot, "opening")

    def test_the_exact_misconception_beats_a_generic_opener(self):
        d = decide(AidMoment(teaching_mode="LECTURE", last_grade=1,
                             active_misconception=1, concept_title="Comparing fractions",
                             available_slots=("opening", "misconception:1")))
        self.assertEqual(d.slot, "misconception:1")

    def test_generate_only_when_nothing_precomputed_fits(self):
        d = decide(AidMoment(teaching_mode="LECTURE", last_grade=1,
                             concept_title="Comparing fractions", available_slots=()))
        self.assertEqual(d.action, "generate")

    def test_nudge_is_empty_unless_generating(self):
        self.assertEqual(prompt_nudge(decide(AidMoment(last_grade=4, correct_streak=3))), "")
        reuse = decide(AidMoment(is_concept_opening=True, concept_title="Triangles",
                                 available_slots=("opening",)))
        self.assertEqual(prompt_nudge(reuse), "")


class TestSubjectRouting(unittest.TestCase):
    """Narrowing the menu from eleven kinds to two or three measurably helps a
    9B model choose. A mis-route is cheap — the model may still pick otherwise."""

    def test_routes_to_plausible_kinds(self):
        for text, expected in [("The Pythagorean theorem", "geometry"),
                               ("Comparing fractions", "fraction"),
                               ("Solving inequalities", "number_line"),
                               ("Graphing quadratic functions", "plot"),
                               ("The water cycle", "cycle"),
                               ("Causes of the French Revolution in 1789", "timeline"),
                               ("Mitosis versus meiosis", "table")]:
            self.assertIn(expected, suggest_kinds(text), f"{text} -> {suggest_kinds(text)}")

    def test_abstract_subjects_route_nowhere(self):
        self.assertEqual(suggest_kinds("The nature of moral obligation"), ())


class TestPromptGating(unittest.TestCase):
    """Enforcement is at prompt construction, not output rejection: a model that
    was never taught the syntax cannot emit a diagram."""

    def test_grammar_only_present_on_a_generate_turn(self):
        from services.common.prompts import get_socratic_tutor_prompt
        quiet = decide(AidMoment(last_grade=4, correct_streak=3))
        loud = decide(AidMoment(teaching_mode="LECTURE", last_grade=1,
                                concept_title="Comparing fractions"))
        p_quiet = get_socratic_tutor_prompt("ctx", [("a", "b")], aid_policy=quiet)[0]["content"]
        p_loud = get_socratic_tutor_prompt("ctx", [("a", "b")], aid_policy=loud)[0]["content"]
        self.assertNotIn("```aid", p_quiet)
        self.assertIn("```aid", p_loud)
        self.assertLess(len(p_quiet), len(p_loud))

    def test_reuse_turn_does_not_spend_grammar_tokens(self):
        from services.common.prompts import get_socratic_tutor_prompt
        reuse = decide(AidMoment(is_concept_opening=True, concept_title="Triangles",
                                 available_slots=("opening",)))
        p = get_socratic_tutor_prompt("ctx", [("a", "b")], aid_policy=reuse)[0]["content"]
        self.assertNotIn("```aid", p)

    def test_no_policy_keeps_legacy_always_on_behaviour(self):
        from services.common.prompts import get_socratic_tutor_prompt
        p = get_socratic_tutor_prompt("ctx", [("a", "b")])[0]["content"]
        self.assertIn("```aid", p)


class TestLicenceFilter(unittest.TestCase):
    """Fail closed. This is material shown to schoolchildren and printed onto
    worksheets; 'probably fine' is not a licence."""

    def test_open_licences_accepted(self):
        from services.research.image_sources import licence_ok
        for good in ["Public domain", "CC0", "CC BY 4.0", "CC BY-SA 3.0",
                     "No known copyright restrictions", "United States Government Work"]:
            self.assertTrue(licence_ok(good), good)

    def test_restrictive_and_unknown_refused(self):
        from services.research.image_sources import licence_ok
        for bad in ["CC BY-NC 4.0", "CC BY-ND", "All rights reserved", "Fair use",
                    "Educational use only", "", None, "unknown", "In copyright"]:
            self.assertFalse(licence_ok(bad), repr(bad))


class TestMediaCache(unittest.TestCase):
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    META = {"source": "Wikimedia Commons", "license": "CC BY-SA 4.0", "author": "A. Person"}

    def setUp(self):
        self._old = os.environ.get("DATA_ROOT")
        os.environ["DATA_ROOT"] = tempfile.mkdtemp(prefix="helga_media_test_")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DATA_ROOT", None)
        else:
            os.environ["DATA_ROOT"] = self._old

    def _resp(self, body, length=None):
        r = MagicMock()
        r.headers = {"Content-Length": str(len(body) if length is None else length)}
        r.raise_for_status = MagicMock()
        r.iter_content = lambda n: [body]
        return r

    def test_cached_image_gets_a_same_origin_src(self):
        """The image aid kind refuses remote hosts by design, so the cache MUST
        hand back a same-origin path or the two halves do not fit together."""
        from services.common.media_cache import cache_image
        with patch("requests.get", return_value=self._resp(self.PNG)):
            rec = cache_image("https://example.org/a.png", self.META)
        self.assertTrue(rec["src"].startswith("/api/media/"))
        from services.common.visual_aids import normalize_aid
        aid, err = normalize_aid({"kind": "image", "spec": {"src": rec["src"]}})
        self.assertIsNone(err, "cached src must satisfy the image aid's source rule")

    def test_identical_url_is_not_downloaded_twice(self):
        from services.common.media_cache import cache_image
        with patch("requests.get", return_value=self._resp(self.PNG)):
            cache_image("https://example.org/a.png", self.META)
        with patch("requests.get", side_effect=AssertionError("re-downloaded")):
            again = cache_image("https://example.org/a.png", self.META)
        self.assertTrue(again["already_cached"])

    def test_attribution_is_captured_at_fetch_time(self):
        from services.common.media_cache import attribution, cache_image
        with patch("requests.get", return_value=self._resp(self.PNG)):
            rec = cache_image("https://example.org/a.png", self.META)
        self.assertEqual(attribution(rec["name"])["license"], "CC BY-SA 4.0")

    def test_bytes_are_trusted_over_headers(self):
        from services.common.media_cache import cache_image
        with patch("requests.get", return_value=self._resp(b"<html>nope</html>")):
            self.assertIsNone(cache_image("https://example.org/b.png", self.META))

    def test_oversize_caught_even_when_content_length_lies(self):
        from services.common.media_cache import MAX_BYTES, cache_image
        huge = b"\xff\xd8\xff" + b"x" * (MAX_BYTES + 10)
        with patch("requests.get", return_value=self._resp(huge, length=10)):
            self.assertIsNone(cache_image("https://example.org/c.jpg", self.META))

    def test_network_failure_degrades_and_never_raises(self):
        from services.common.media_cache import cache_image
        with patch("requests.get", side_effect=Exception("connection reset")):
            self.assertIsNone(cache_image("https://example.org/d.png", self.META))

    def test_non_http_schemes_refused(self):
        from services.common.media_cache import cache_image
        for bad in ["ftp://x/y.png", "javascript:alert(1)", "", None, "/etc/passwd"]:
            self.assertIsNone(cache_image(bad, self.META))

    def test_media_names_are_whitelisted_against_traversal(self):
        """The serving route takes this name from a URL."""
        from services.common.media_cache import safe_media_name
        for bad in ["../../helga.db", "x.png", "abcdef0123456789.svg",
                    "abcdef0123456789.jpg/../x", "", None]:
            self.assertFalse(safe_media_name(bad), repr(bad))
        self.assertTrue(safe_media_name("abcdef0123456789.jpg"))


if __name__ == "__main__":
    unittest.main()
