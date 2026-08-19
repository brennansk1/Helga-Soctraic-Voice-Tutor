"""LaTeX to speech, so the TTS path never meets a formula."""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.math_speech import (  # noqa: E402
    extract, speak, speech_for, unspoken)


class TestSpeak(unittest.TestCase):
    def test_common_forms(self):
        for latex, want in (
                (r"\frac{a}{b}", "a over b"),
                (r"x^2", "x squared"),
                (r"x^3", "x cubed"),
                (r"\sqrt{x}", "the square root of x"),
                (r"\sqrt[3]{8}", "the cube root of 8"),
                (r"a \leq b", "a is less than or equal to b")):
            self.assertEqual(speak(latex), want, latex)

    def test_the_pythagorean_theorem(self):
        self.assertEqual(speak(r"a^2 + b^2 = c^2"),
                         "a squared plus b squared equals c squared")

    def test_a_limit_reads_as_a_condition_not_an_index(self):
        """`\\lim_{x \\to 0}` is "as x approaches 0", not "sub x to 0" — the
        generic subscript rule reads it wrongly."""
        self.assertIn("the limit as x approaches 0", speak(r"\lim_{x \to 0}"))

    def test_a_leading_negative_is_not_a_subtraction(self):
        self.assertTrue(speak(r"\frac{-b}{2a}").startswith("negative b"))

    def test_empty_input(self):
        self.assertEqual(speak(""), "")
        self.assertEqual(speak(None), "")


class TestUnspoken(unittest.TestCase):
    """The check that matters: a converter passing markup through returns a
    non-empty string and satisfies every "did it produce output" test while
    being useless to a listener."""

    def test_real_formulas_leave_nothing_behind(self):
        for latex in (r"\frac{-b \pm \sqrt{b^2-4ac}}{2a}",
                      r"\sum_{i=1}^{n} i^2",
                      r"\int_{0}^{1} x^2 dx",
                      r"\lim_{x \to \infty} \frac{1}{x}",
                      r"A_{ij} \times B_{jk}"):
            self.assertEqual(unspoken(speak(latex)), [], latex)

    def test_it_reports_rather_than_hides_what_it_cannot_say(self):
        self.assertTrue(unspoken(r"\begin{bmatrix} 1 \end{bmatrix}"))


class TestExtract(unittest.TestCase):
    def test_display_math_is_not_shredded_into_inline_spans(self):
        spans = extract("Text $$\\frac{a}{b}$$ more.")
        self.assertEqual(spans, ["\\frac{a}{b}"])

    def test_inline_and_display_together(self):
        spans = extract("First $x^2$ then $$y^2$$.")
        self.assertIn("x^2", spans)
        self.assertIn("y^2", spans)

    def test_duplicates_collapse(self):
        self.assertEqual(extract("$x^2$ and again $x^2$"), ["x^2"])

    def test_prose_without_maths(self):
        self.assertEqual(extract("No formulas here at all."), [])

    def test_speech_for_returns_triples(self):
        out = speech_for("The identity $a^2 + b^2 = c^2$ holds.")
        self.assertEqual(len(out), 1)
        latex, spoken, left = out[0]
        self.assertIn("squared", spoken)
        self.assertEqual(left, [])
