"""MathML to LaTeX: the component that decides whether the maths is TRUE.

Every other test in this domain is about teaching quality. This one is about
correctness, and it is the only place in the mathematics pipeline where a bug
produces a confident false statement rather than a visible mess.

MEASURED, against OpenStax Calculus Volume 1, using the generic `get_text()`
extraction every HTML reader in this repository uses:

    3² = 9        came out as   "3 2 = 9"     (thirty-two equals nine)
    f(x) = √x     came out as   "f ( x ) = x" (the root vanished)
    x = ⅓(y+1)²   came out as   "x = 1 3 ..." (a third became thirteen)

Those are not rendering complaints. They are wrong mathematics, they read as
well-formed text, and no structural check downstream would flag them. The
regression cases below are exactly those three.
"""
from bs4 import BeautifulSoup

from services.domains.mathematics.mathml import to_latex, replace_math


def _m(xml):
    return BeautifulSoup(xml, "html.parser").find("math")


def tex(xml):
    return to_latex(_m(xml))


# ------------------------------------------------- the false-statement cases

def test_exponent_is_not_flattened_into_a_two_digit_number():
    """`3 2 = 9` was the measured output. It is a false statement."""
    out = tex("<math><msup><mn>3</mn><mn>2</mn></msup><mo>=</mo><mn>9</mn></math>")
    assert out == "3^2=9", out
    assert "3 2" not in out


def test_a_square_root_does_not_disappear():
    """The root was dropped entirely, turning √x into x."""
    out = tex("<math><mi>f</mi><mo>=</mo><msqrt><mi>x</mi></msqrt></math>")
    assert r"\sqrt{x}" in out, out


def test_a_fraction_does_not_become_a_two_digit_integer():
    """1/3 came out as `1 3`, which reads as thirteen."""
    out = tex("<math><mfrac><mn>1</mn><mn>3</mn></mfrac></math>")
    assert out == r"\frac{1}{3}", out


# ---------------------------------------------------------------- structure

def test_nested_fraction_keeps_its_grouping():
    out = tex("<math><mfrac><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>"
              "<mn>2</mn></mfrac></math>")
    assert out == r"\frac{a+b}{2}", out


def test_multi_character_exponent_is_braced():
    """x^n+1 and x^{n+1} are different expressions."""
    out = tex("<math><msup><mi>x</mi><mrow><mi>n</mi><mo>+</mo><mn>1</mn>"
              "</mrow></msup></math>")
    assert out == "x^{n+1}", out


def test_sum_limits_become_sub_and_superscripts():
    out = tex("<math><munderover><mo>∑</mo>"
              "<mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>"
              "<mi>n</mi></munderover></math>")
    assert r"\sum" in out and "_{i=1}" in out and "^n" in out, out


def test_definite_integral_carries_both_limits():
    out = tex("<math><msubsup><mo>∫</mo><mn>0</mn><mn>1</mn></msubsup>"
              "<mi>x</mi></math>")
    assert r"\int" in out and "_0" in out and "^1" in out, out


def test_nth_root_uses_the_optional_argument():
    out = tex("<math><mroot><mi>x</mi><mn>3</mn></mroot></math>")
    assert out == r"\sqrt[3]{x}", out


def test_function_names_are_operators_not_products_of_letters():
    """`sin` as three italic variables is a different expression."""
    out = tex("<math><mi>sin</mi><mi>x</mi></math>")
    assert r"\sin" in out and "s i n" not in out, out


def test_lim_is_an_operator_not_a_text_run():
    """OpenStax puts `lim` in <mtext>; \\text{lim} loses limit placement."""
    out = tex("<math><munder><mtext>lim</mtext>"
              "<mrow><mi>h</mi><mo>→</mo><mn>0</mn></mrow></munder></math>")
    assert r"\lim" in out and r"\text{lim}" not in out, out


def test_greek_in_mtext_becomes_a_symbol():
    out = tex("<math><mfrac><mrow><mtext>Δ</mtext><mi>y</mi></mrow>"
              "<mrow><mtext>Δ</mtext><mi>x</mi></mrow></mfrac></math>")
    assert out == r"\frac{\Delta y}{\Delta x}", out


def test_prime_is_not_braced_into_an_exponent():
    """f^{'} is legal but f' is what a reader and a model expect."""
    out = tex("<math><msup><mi>f</mi><mo>′</mo></msup></math>")
    assert out == "f^'", out


def test_minus_does_not_gain_a_space():
    out = tex("<math><mn>2</mn><mi>x</mi><mo>−</mo><mn>2</mn></math>")
    assert out == "2x-2", out


def test_backslash_command_keeps_its_separator():
    """\\times2 would be read as a control word named times2."""
    out = tex("<math><mn>3</mn><mo>×</mo><mn>2</mn></math>")
    assert out == r"3\times 2", out


# ------------------------------------------------------------- robustness

def test_an_unknown_element_keeps_its_text_rather_than_vanishing():
    out = tex("<math><mglyph>q</mglyph></math>")
    assert "q" in out


def test_junk_never_raises():
    assert to_latex(None) == ""
    assert tex("<math></math>") == ""


def test_replace_math_strips_the_content_mathml_duplicate():
    """OpenStax <semantics> carries presentation AND content MathML.

    Reading both produced the doubled 'f(x)=4-2x+5. f(x)=4-2x+5.'
    """
    soup = BeautifulSoup(
        "<p><math><semantics><mrow><mi>A</mi></mrow>"
        "<annotation-xml encoding='MathML-Content'><mi>A</mi>"
        "</annotation-xml></semantics></math></p>", "html.parser")
    n = replace_math(soup)
    assert n == 1
    assert soup.get_text() == "$A$", soup.get_text()


def test_replace_math_is_in_place_and_counts():
    soup = BeautifulSoup("<p><math><mn>1</mn></math> and "
                         "<math><mn>2</mn></math></p>", "html.parser")
    assert replace_math(soup) == 2
    assert "$1$" in soup.get_text() and "$2$" in soup.get_text()
