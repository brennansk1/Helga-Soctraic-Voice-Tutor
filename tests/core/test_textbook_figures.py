"""Textbook figure extraction — caption binding is the whole job.

Every threshold here was set by inspecting a real 1486-page OpenStax/CNX
biology export, not by assumption. That book broke three starting assumptions in
a row, which is why the extractor detects a format profile instead of hard-coding
one.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.research import textbook_figures as tf  # noqa: E402


class TestCaptionDiscrimination(unittest.TestCase):
    """The line between a caption and the body text under a figure."""

    def test_a_real_caption_is_accepted(self):
        for t in ("The nuclear envelope is a double-membrane structure that "
                  "constitutes the outermost portion of the nucleus.",
                  "(a) The lattice structure of ice makes it less dense than "
                  "liquid water.",
                  "Unlike Archaea, bacteria have a cell wall made of peptidoglycan."):
            self.assertTrue(tf._plausible_caption(t), t[:40])

    def test_a_mid_sentence_fragment_is_refused(self):
        """Body text split by a column break — "A fat molecule, such as a
        triglyceride, consists of" — reads like a caption until you notice it
        never finishes."""
        self.assertFalse(tf._plausible_caption(
            "A fat molecule, such as a triglyceride, consists of"))
        self.assertFalse(tf._plausible_caption(
            "The chemical nature of the R group determines the"))

    def test_a_discourse_connective_is_refused(self):
        for t in ("However, structures that are more complex are made using carbon.",
                  "These cohesive forces are also related to adhesion.",
                  "Therefore, the reaction proceeds to completion."):
            self.assertFalse(tf._plausible_caption(t), t[:40])

    def test_a_question_is_not_a_caption(self):
        """OpenStax sets Visual Connection prompts under figures. One
        keyword-matched a Golgi figure to a concept called "The Plasma
        Membrane" purely because the question contained those words."""
        self.assertFalse(tf._plausible_caption(
            "Why does the cis face of the Golgi not face the plasma membrane?"))
        self.assertFalse(tf._plausible_caption(
            "What structures does a plant cell have that an animal cell does not?"))

    def test_a_named_sidebar_is_refused(self):
        self.assertFalse(tf._plausible_caption(
            "Careers in Action Registered Dietitian Obesity is a worldwide "
            "health concern and many diseases are linked to it."))

    def test_a_continuation_starting_lowercase_is_refused(self):
        self.assertFalse(tf._plausible_caption(
            "and lower pH, whereas bases provide hydroxide ions."))

    def test_length_bounds(self):
        self.assertFalse(tf._plausible_caption("Too short."))
        self.assertFalse(tf._plausible_caption("A" * 800))


class TestConceptMatching(unittest.TestCase):
    CONCEPTS = [
        {"uid": "con_car", "title": "Carbohydrates",
         "objectives": ["monosaccharides disaccharides dehydration"]},
        {"uid": "con_chl", "title": "Chloroplasts and Photosynthesis",
         "objectives": ["chlorophyll pigment captures light"]},
    ]

    def test_a_clear_match_is_made(self):
        figs = [{"caption": "The chloroplasts contain a green pigment called "
                            "chlorophyll, which captures light energy.",
                 "section": "Photosynthesis"}]
        m = tf.match_to_concepts(figs, self.CONCEPTS)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["concept_uid"], "con_chl")
        self.assertEqual(m[0]["role"], "illustrates")

    def test_an_unrelated_figure_is_left_unattached(self):
        """A weak match is the decorative case. The role field exists precisely
        so it cannot be attached."""
        figs = [{"caption": "A tight junction is a watertight seal between two "
                            "adjacent animal cells.", "section": "Junctions"}]
        self.assertEqual(tf.match_to_concepts(figs, self.CONCEPTS), [])

    def test_a_figure_with_no_caption_or_section_is_skipped(self):
        self.assertEqual(
            tf.match_to_concepts([{"caption": None, "section": None}],
                                 self.CONCEPTS), [])

    def test_every_match_carries_a_role(self):
        figs = [{"caption": "Monosaccharides and disaccharides form through "
                            "dehydration synthesis reactions.", "section": "Sugars"}]
        for m in tf.match_to_concepts(figs, self.CONCEPTS):
            self.assertIn(m["role"], ("illustrates",))


class TestSummarise(unittest.TestCase):
    def test_counts_and_rate(self):
        figs = [{"caption": "x", "section": "s", "page": 1},
                {"caption": None, "section": "s", "page": 2},
                {"caption": "y", "section": None, "page": 2}]
        s = tf.summarise(figs)
        self.assertEqual(s["figures"], 3)
        self.assertEqual(s["captioned"], 2)
        self.assertEqual(s["pages_spanned"], 2)

    def test_empty_is_zero_not_a_crash(self):
        self.assertEqual(tf.summarise([])["caption_rate"], 0.0)

    def test_captioned_filters(self):
        self.assertEqual(len(tf.captioned([{"caption": "a"}, {"caption": None}])), 1)


class TestIngestRefusals(unittest.TestCase):
    def test_an_unlicensed_book_is_refused_before_any_work(self):
        r = tf.ingest("/nonexistent.pdf", None, "c1", license=None, source="X")
        self.assertEqual(r["ingested"], 0)
        self.assertEqual(r["reason"], "no licence")
