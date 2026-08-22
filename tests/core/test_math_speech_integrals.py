"""Speaking the notation a calculus course is made of.

Helga teaches by VOICE. A turn containing LaTeX that `math_speech` cannot
render is a turn the student HEARS AS RAW MARKUP, and the domain benchmark
scores exactly that as `notation_rigour`.

THE BUG THIS PINS
-----------------
The fallback rule was `\\(sum|prod|int)\\b`. An underscore is a WORD character,
so `\\int_0^1` has no word boundary after "int" and the rule silently declined
to fire. The braced rule above it required `_{...}^{...}`, which `\\int_0^1`
also is not. So the ordinary way of writing a definite integral matched
neither, and every one of them was spoken as "\\int".

That is the most common symbol in a calculus course.
"""
import os
import sys

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "services/core"))

import pytest  # noqa: E402

from services.core.math_speech import speak, unspoken  # noqa: E402


@pytest.mark.parametrize("latex", [
    r"\int_0^1 x dx",
    r"\int_{0}^{1} x dx",
    r"\int x dx",
    r"\iint_D f",
    r"\iiint_V f",
    r"\oint_C F",
    r"\sum_{i=1}^{n} i",
    r"\sum_i a_i",
    r"\prod_{k=1}^{3} k",
    r"\land", r"\lor", r"\neg p", r"\vdots",
    r"\begin{matrix}a & b\end{matrix}",
    r"\lim _{h\to 0}\frac{f(x+h)-f(x)}{h}",
])
def test_nothing_a_calculus_course_needs_is_left_as_markup(latex):
    left = unspoken(speak(latex))
    assert not left, f"{latex} -> unspoken {left}"


def test_bare_limits_read_the_same_as_braced_ones():
    """`\\int_0^1` is at least as common as `\\int_{0}^{1}`."""
    assert speak(r"\int_0^1 x dx").split() == speak(r"\int_{0}^{1} x dx").split()


def test_a_definite_integral_says_its_limits():
    spoken = speak(r"\int_0^1 x dx")
    assert "integral" in spoken and "from 0" in spoken and "to 1" in spoken


def test_multiple_integrals_are_distinguished():
    assert "double" in speak(r"\iint_D f")
    assert "triple" in speak(r"\iiint_V f")
    assert "contour" in speak(r"\oint_C F")


def test_a_sum_is_not_read_as_an_integral():
    """The three share one rule; a lambda mixing them up is easy to write."""
    assert "sum" in speak(r"\sum_{i=1}^{n} i")
    assert "integral" not in speak(r"\sum_{i=1}^{n} i")
    assert "product" in speak(r"\prod_{k=1}^{3} k")


def test_words_starting_with_the_command_are_not_eaten():
    """The lookahead must exclude letters, or \\integral-like tokens break."""
    assert unspoken(speak(r"\intercal")) or True   # must not raise
    assert "the integral of ercal" not in speak(r"\intercal")


def test_speaking_never_raises():
    for junk in ("", None, "\\", "$$", r"\frac{", r"\begin{"):
        try:
            speak(junk)
        except Exception as exc:      # pragma: no cover
            raise AssertionError(f"{junk!r} raised {exc}")


def test_a_bare_limit_is_one_token_not_a_greedy_run():
    """In LaTeX `^1x` is `^{1}` then `x`. Matching \\w+ read the upper limit of
    `\\int_0^1x` as "1x" — a limit that does not exist."""
    spoken = speak(r"\int _0^1x")
    assert "to 1 " in spoken or spoken.rstrip().endswith("of x"), spoken
    assert "1x" not in spoken


def test_the_whole_pipeline_from_mathml_is_speakable():
    """MathML -> LaTeX -> speech, the path a maths course actually takes.

    Each stage is tested on its own elsewhere; this is the one that would
    catch a LaTeX dialect the converter emits and the speaker cannot read.
    """
    from bs4 import BeautifulSoup
    from services.domains.mathematics.mathml import to_latex

    cases = [
        "<msup><mn>3</mn><mn>2</mn></msup><mo>=</mo><mn>9</mn>",
        "<msqrt><mi>x</mi></msqrt>",
        "<mfrac><mn>1</mn><mn>3</mn></mfrac>",
        "<mroot><mi>x</mi><mn>3</mn></mroot>",
        "<munderover><mo>∑</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn>"
        "</mrow><mi>n</mi></munderover>",
        "<msubsup><mo>∫</mo><mn>0</mn><mn>1</mn></msubsup><mi>x</mi>",
        "<mfrac><mrow><mtext>Δ</mtext><mi>y</mi></mrow>"
        "<mrow><mtext>Δ</mtext><mi>x</mi></mrow></mfrac>",
    ]
    for fragment in cases:
        node = BeautifulSoup(f"<math>{fragment}</math>", "html.parser").find("math")
        latex = to_latex(node)
        left = unspoken(speak(latex))
        assert not left, f"{fragment} -> {latex} -> unspoken {left}"
