"""Notation the tutor actually writes, and could not say out loud.

Course 1 (causal inference) logged nine [MATH] warnings, five of them for
`\perp`. These are the sequences a statistics or causal-inference course uses
constantly and math_speech had no entry for, so a voice lesson read them as raw
markup.
"""
from services.core.math_speech import speak, unspoken


def _spoken(latex):
    out = speak(latex)
    assert unspoken(out) == [], f"{latex} -> {out!r} still contains markup"
    return out


def test_independence_is_not_read_as_two_perpendiculars():
    """\\perp\\!\\!\\!\\perp is "is independent of" -- the single-\\perp rule
    must not match it first and produce "perpendicular to perpendicular to"."""
    assert "independent of" in _spoken(r"A \perp\!\!\!\perp B")
    assert "perpendicular" not in _spoken(r"A \perp\!\!\!\perp B")


def test_conditional_independence_reads_as_a_sentence():
    assert _spoken(r"A \perp\!\!\!\perp B \mid C").split() == \
        "A is independent of B given C".split()


def test_a_single_perp_is_still_perpendicular():
    assert "perpendicular" in _spoken(r"A \perp B")


def test_estimators_read_after_their_symbol():
    """"beta hat", not "hat beta" -- how a statistician says it aloud."""
    assert _spoken(r"\hat{\beta}").split() == ["beta", "hat"]
    assert _spoken(r"\bar{x}").split() == ["x", "bar"]


def test_vectors_and_tildes():
    assert "vector" in _spoken(r"\vec{v}")
    assert "tilde" in _spoken(r"\tilde{y}")


def test_distribution_and_proportionality():
    assert "is distributed as" in _spoken(r"X \sim N")
    assert "is proportional to" in _spoken(r"y \propto x")


def test_the_greek_a_stats_course_uses():
    for latex, word in ((r"\chi", "chi"), (r"\psi", "psi"), (r"\eta", "eta"),
                        (r"\zeta", "zeta"), (r"\kappa", "kappa"),
                        (r"\xi", "xi"), (r"\nu", "nu")):
        assert word in _spoken(latex)


def test_previously_working_notation_still_works():
    """The additions must not have shadowed anything."""
    assert _spoken(r"x^2 + y^2 = r^2") == "x squared plus y squared equals r squared"
    assert "over" in _spoken(r"\frac{\partial f}{\partial x}")
    assert "the sum from" in _spoken(r"\sum_{i=1}^{n} x_i")
    assert "lambda" in _spoken(r"\lambda")
