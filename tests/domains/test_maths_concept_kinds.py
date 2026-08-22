"""Classifying mathematical concepts, and the constraint every kind respects.

The kind decides HOW a concept is taught, so a wrong kind teaches it the wrong
way: a student asked to "derive" why the integral sign is an elongated S is
being asked to reason about a historical accident.
"""
import re

from services.domains.mathematics import concept_kind as mk

CLEAR = [
    ("Definition of a Limit", mk.DEFINITION),
    ("Sigma Notation", mk.NOTATION),
    ("Reading Interval Notation", mk.NOTATION),
    ("The Mean Value Theorem", mk.THEOREM),
    ("The Squeeze Theorem", mk.THEOREM),
    ("Proof of the Chain Rule", mk.PROOF),
    ("Where the Quadratic Formula comes from", mk.PROOF),
    ("Integration by Parts", mk.PROCEDURE),
    ("Solving Quadratic Equations", mk.PROCEDURE),
    ("Graphing Rational Functions", mk.REPRESENTATION),
    ("Modeling Population Growth", mk.APPLICATION),
    ("Estimating Square Roots", mk.ESTIMATION),
    ("Common Errors with Negative Exponents", mk.MISCONCEPTION),
]


def test_clear_titles_classify_correctly():
    wrong = [(t, mk.classify(t, "", None), e) for t, e in CLEAR
             if mk.classify(t, "", None) != e]
    assert not wrong, wrong


def test_proof_outranks_theorem():
    """"Proof of the Chain Rule" matches both — `rule\\b` is a theorem pattern
    — and it is a proof: the argument is the content."""
    assert mk.rank(mk.PROOF) < mk.rank(mk.THEOREM)


def test_a_bare_object_title_is_a_definition():
    """Real sections are named "Eigenvalues", not "Definition of an
    eigenvalue". Both fell to UNKNOWN, which costs all teaching guidance."""
    for title in ("Eigenvalues", "Partial derivatives", "Matrices",
                  "The derivative", "Polynomials"):
        assert mk.classify(title, "", None) == mk.DEFINITION, title


def test_the_object_fallback_never_overrides_a_real_match():
    """Tried as an ordinary pattern it was far too greedy: "The Mean Value
    Theorem" matched `mean` and "Graphing Rational Functions" matched
    `functions`, and DEFINITION outranks both. A title naming an object AND an
    action is about the action."""
    assert mk.classify("The Mean Value Theorem", "", None) == mk.THEOREM
    assert mk.classify("Graphing Rational Functions", "", None) == mk.REPRESENTATION
    assert mk.classify("Differentiating Polynomials", "", None) == mk.PROCEDURE


def test_a_long_title_does_not_get_the_object_fallback():
    """A long title has a verb in it somewhere; guessing DEFINITION would
    relabel genuinely ambiguous concepts instead of leaving them unknown."""
    assert mk.classify(
        "Some lengthy heading that merely happens to mention vectors "
        "somewhere in passing", "", None) == mk.UNKNOWN


def test_an_opaque_title_stays_unknown():
    assert mk.classify("Working with these", "", None) == mk.UNKNOWN


def test_junk_never_raises():
    for bad in (None, "", "   ", "\x00", "?" * 400):
        mk.classify(bad, "", None)


# --------------------------------------------------------------- guidance

def test_every_kind_has_distinct_guidance():
    kinds = [k for k in mk.RANK if k != mk.UNKNOWN]
    texts = [mk.guidance(k) for k in kinds]
    assert all(texts), "a kind with no guidance is a silent downgrade"
    assert len(set(texts)) == len(texts)


#: Phrases that hand computation to the learner. There is no marker here, so an
#: unchecked wrong answer that is praised teaches the error.
_ASKS_TO_SOLVE = re.compile(
    r"\b(ask (them|the (learner|student)) to (solve|compute|calculate|"
    r"evaluate|simplify|differentiate|integrate)"
    r"|have (them|the (learner|student)) (solve|compute|work out)"
    r"|now you try)", re.I)

_NEGATED_BEFORE = re.compile(
    r"(never|not|avoid|rather than|instead of|don'?t|do not|slide into|"
    r"tempting to)[^.]{0,40}$", re.I)

#: The negation can FOLLOW the phrase. PROCEDURE's guidance reads "most likely
#: to slide into 'now you try', and you must not" — the prohibition arrives
#: after the thing prohibited, and a backwards-only check reports the rule as
#: its own breach.
_NEGATED_AFTER = re.compile(
    r"^[^.]{0,60}(must not|never do|do not|don'?t|is forbidden|you must "
    r"resist)", re.I)


def test_no_kind_asks_the_learner_to_solve():
    """The subject's central constraint, checked against every kind.

    Negation-aware in BOTH directions. PROCEDURE's own guidance says "the kind
    most likely to slide into 'now you try', and you must not" — the
    prohibition arrives AFTER the phrase, so a backwards-only check reports the
    rule as its own breach. The computer-science version of this test failed
    the same way, on a negation that preceded.
    """
    offenders = []
    for kind, text in mk.GUIDANCE.items():
        for m in _ASKS_TO_SOLVE.finditer(text or ""):
            if _NEGATED_BEFORE.search(text[max(0, m.start() - 60):m.start()]):
                continue
            if _NEGATED_AFTER.match(text[m.end():m.end() + 70]):
                continue
            offenders.append(f"{kind}: {m.group(0)!r}")
    assert not offenders, offenders


def test_the_rule_variant_is_selectable_and_defaults_to_long():
    """Which variant ships is a MEASUREMENT question, not a preference.

    The long form spends most of its words justifying the rule, and 90 words
    on top of ~106 of kind guidance roughly doubles the block in a prompt that
    already carries context, history, aid policy, turn state and
    misconceptions. The switch exists so the two can be compared without
    editing code; the default stays the measured-good one until a comparison
    says otherwise.
    """
    import importlib
    import os

    original = os.environ.get("HELGA_MATHS_RULE_VARIANT")
    try:
        for value, expect in (("short", mk.NEVER_SOLVE_SHORT),
                              ("SHORT", mk.NEVER_SOLVE_SHORT),
                              ("", mk.NEVER_SOLVE)):
            os.environ["HELGA_MATHS_RULE_VARIANT"] = value
            importlib.reload(mk)
            assert expect in mk.prompt_line("PROCEDURE"), value

        os.environ["HELGA_MATHS_RULE_VARIANT"] = "none"
        importlib.reload(mk)
        line = mk.prompt_line("PROCEDURE")
        assert mk.NEVER_SOLVE not in line and mk.NEVER_SOLVE_SHORT not in line
        assert "HOW TO TEACH" in line, "the kind guidance must survive"
        assert mk.prompt_line(mk.UNKNOWN) == "", "no rule and no kind = nothing"
    finally:
        if original is None:
            os.environ.pop("HELGA_MATHS_RULE_VARIANT", None)
        else:
            os.environ["HELGA_MATHS_RULE_VARIANT"] = original
        importlib.reload(mk)


def test_the_short_rule_keeps_the_operative_content():
    """Shorter must not mean weaker: the prohibition and the replacement both
    have to survive, or the variant is not a fair comparison."""
    short = mk.NEVER_SOLVE_SHORT
    assert len(short.split()) < len(mk.NEVER_SOLVE.split()) / 1.5
    for must in ("never ask", "compute", "solve", "reason"):
        assert must in short.lower(), must


def test_the_standing_rule_rides_every_turn():
    """Per-kind guidance was not enough: 2 of 24 turns asked the learner to
    compute, because neither kind's guidance happened to name that failure."""
    for kind in list(mk.RANK):
        line = mk.prompt_line(kind)
        assert mk.NEVER_SOLVE in line, kind


def test_the_standing_rule_applies_even_when_the_kind_is_unknown():
    """An unclassified mathematics concept is still mathematics, and is
    exactly the case where per-kind guidance cannot help."""
    assert mk.NEVER_SOLVE in mk.prompt_line(mk.UNKNOWN)
    assert mk.NEVER_SOLVE in mk.prompt_line(None)


def test_guidance_stands_down_when_mined_material_is_present():
    """Two imperatives for one turn produced a turn that followed neither."""
    line = mk.prompt_line(mk.PROCEDURE, has_pair=True)
    assert "background only" in line
    assert mk.NEVER_SOLVE in line, "the absolute rule still applies"
