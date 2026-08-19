"""Safety that behaves like a teacher, not a filter.

No content classifier: only Llama Guard 3-1B-INT4 (440 MB) fits the budget and
it over-blocks 7.4% of benign clinical questions. At that rate a student asking
twenty legitimate questions in a WWII unit hits a wrongful refusal almost every
session. Over-blocking is a product failure, not a safe default.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.teaching_safety import (  # noqa: E402
    VISIBILITY_TIERS, band_for_age, crisis_response, scope_block,
    screen_disclosure, subject_of)


class TestSubjectScope(unittest.TestCase):
    def test_a_history_course_is_authorised_to_discuss_atrocity(self):
        b = scope_block("World War II History", learner_age=16)
        self.assertIn("EXPLICITLY AUTHORISED", b)
        self.assertIn("genocide", b)
        self.assertIn("Refusing legitimate", b)

    def test_biology_is_authorised_for_anatomy(self):
        self.assertIn("anatomy", scope_block("Human Biology", learner_age=15))

    def test_chemistry_is_authorised_for_hazards(self):
        self.assertIn("explosives", scope_block("Organic Chemistry", learner_age=17))

    def test_subject_detection_handles_aliases(self):
        self.assertEqual(subject_of("World War II and its Causes"), "history")
        self.assertEqual(subject_of("Reading the Modern Novel"), "literature")

    def test_an_unknown_subject_still_refuses_to_refuse(self):
        b = scope_block("Assorted Topics", learner_age=20)
        self.assertIn("Refusing legitimate academic material is a failure", b)

    def test_actionable_harm_stays_out_of_scope_in_every_subject(self):
        for title in ("Chemistry", "World History", "Biology"):
            self.assertIn("actionable instructions", scope_block(title, learner_age=16))


class TestAgeBands(unittest.TestCase):
    def test_bands_map_from_age(self):
        self.assertEqual(band_for_age(8), "primary")
        self.assertEqual(band_for_age(13), "lower_secondary")
        self.assertEqual(band_for_age(16), "upper_secondary")
        self.assertEqual(band_for_age(30), "adult")

    def test_unknown_age_is_careful_but_not_maximally_restrictive(self):
        """Assuming a primary-school learner would refuse legitimate material
        for every adult whose record is incomplete."""
        self.assertEqual(band_for_age(None), "lower_secondary")

    def test_the_band_gates_depth_not_topic(self):
        young = scope_block("World War II History", learner_age=8)
        older = scope_block("World War II History", learner_age=17)
        self.assertIn("genocide", young, "topic is licensed at every band")
        self.assertIn("age-appropriate", young)
        self.assertIn("academic register", older)


class TestDisclosure(unittest.TestCase):
    def test_self_harm_routes_to_a_guardian_and_does_not_assess(self):
        """An offline system with no escalation path must not attempt risk
        assessment — that is a task for trained professionals."""
        d = screen_disclosure("sometimes I want to die")
        self.assertEqual(d["kind"], "self_harm")
        self.assertTrue(d["notify_guardian"])
        self.assertFalse(d["assess_risk"])

    def test_abuse_routes_away_from_the_guardian(self):
        """The guardian may be the source of harm."""
        d = screen_disclosure("my dad hits me and I'm afraid to go home")
        self.assertEqual(d["kind"], "abuse")
        self.assertFalse(d["notify_guardian"])
        self.assertEqual(d["route"], "safeguarding")

    def test_ordinary_coursework_is_not_a_disclosure(self):
        """THE FALSE POSITIVE THAT MATTERS MOST.

        The first version matched the bare word "suicid" and flagged "Hamlet
        contemplates suicide in the soliloquy" — which would have fired a
        guardian notification on a literature lesson. First person is the
        signal; the topic is not.
        """
        for t in ("The soldiers died in the trenches.",
                  "Hamlet contemplates suicide in the soliloquy.",
                  "The Jonestown massacre involved mass suicide.",
                  "Durkheim's study of suicide founded modern sociology.",
                  "Soldiers who wanted to die in battle were rare.",
                  "The character says he is better off dead.",
                  "Cell death is called apoptosis."):
            self.assertIsNone(screen_disclosure(t)["kind"], t)

    def test_a_genuine_disclosure_is_never_missed(self):
        """The worse direction of error. First person plus a harm phrase, with
        words allowed between them — "I've been cutting myself" was missed when
        adjacency was required."""
        for t in ("sometimes I want to die",
                  "I've been cutting myself",
                  "I think about suicide a lot",
                  "I feel like I'm better off dead",
                  "I have no reason to live",
                  "I don't want to be here anymore"):
            self.assertEqual(screen_disclosure(t)["kind"], "self_harm", t)

    def test_the_response_acknowledges_and_stops(self):
        r = crisis_response("self_harm")
        self.assertIn("988", r)
        self.assertIn("trusted adult", r)
        # Never counsel, never assess, never discuss method.
        for forbidden in ("how are you planning", "on a scale", "promise me"):
            self.assertNotIn(forbidden, r.lower())

    def test_two_tier_visibility_is_defined(self):
        self.assertIn("learning", VISIBILITY_TIERS)
        self.assertIn("safety", VISIBILITY_TIERS)
        self.assertIn("Not surfaced", VISIBILITY_TIERS["learning"])
        self.assertIn("Always surfaced", VISIBILITY_TIERS["safety"])
