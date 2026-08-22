"""Detecting a confident bluffer — the profile the tutor adapts to worst.

MEASURED, mathematics domain benchmark, 2026-08-22, n=15:

    confident_bluffer     adaptation 1.00   (lowest of any profile)
    silent_struggler      adaptation 1.33
    misconception_holder  adaptation 3.00

`adaptation` is the release gate (target 3.5), and the mechanism meant to
handle a bluffer never fired once across three topics.

THE DEFECT
----------
`BLUFFING` required `not _MECHANISM.search(last)` — "they did not state a
mechanism". `_MECHANISM` matches connective words: "because", "since",
"which is why". Fluent use of exactly those words is the DEFINING TRAIT of a
bluffer, so the test was defeated by the thing it exists to catch:

    "The slope remains identical BECAUSE the mixed partial derivatives are
     commutative, meaning the order does not matter."

That states no mechanism whatever, and was graded 2 of 5 — yet "because"
blocked detection. A third case escaped on `grades[-1] < 3` when the final
grade landed on exactly 3.

The grade already carries the judgement the connective was standing in for: a
real mechanism does not get graded low.
"""
from services.common.learner_behaviour import (
    classify, BLUFFING, HEDGING, GIVING_UP, AHEAD,
)

#: VERBATIM from the benchmark transcripts, `confident_bluffer`, topic
#: "Partial derivatives". Graded 3, 1, 3. Note "because" and "since" doing the
#: work of a mechanism that is never actually stated.
BLUFF = [
    "It’s clearly a partial derivative because the other coordinates are "
    "fixed, isolating the slope in the East direction. The total change would "
    "require accounting for any simultaneous shifts in North, but since "
    "you’re only moving East, the partial captures the entire relevant "
    "variation along that specific path.",
    "The slope remains identical because the mixed partial derivatives are "
    "commutative, meaning the order of differentiating with respect to North "
    "and East does not alter the resulting curvature value. As long as the "
    "surface is sufficiently smooth to satisfy the conditions for Clairaut's "
    "Theorem, the partial derivative with respect to North while holding East "
    "constant is invariant under the sequence of operations.",
]

#: A learner reasoning honestly at the same length. The grade is what separates
#: them, which is the whole point of the fix.
REAL = [
    "I think the derivative is the slope of the tangent line at a point, "
    "because it is the limit of the average rate of change as the interval "
    "shrinks toward zero, which is what makes it instantaneous rather than "
    "an average over some stretch of the curve that we picked arbitrarily.",
    "So for a curve that is getting steeper, the derivative would be "
    "increasing, since each successive tangent line tilts further upward as "
    "we move along, and that increase is exactly what the second derivative "
    "is measuring when people talk about concavity of the whole curve.",
]


def test_a_bluffer_is_detected_despite_saying_because():
    """The regression this file exists for."""
    assert classify(BLUFF, grades=[2, 2]) == BLUFFING


def test_a_bluffer_is_detected_despite_saying_since():
    answers = [BLUFF[0], "Since the eigenspace is orthogonal, the alignment "
                         "property is preserved under any scalar multiple "
                         "applied to the transformation in question here."]
    assert classify(answers, grades=[2, 1]) == BLUFFING


def test_a_final_grade_of_exactly_three_does_not_grant_escape():
    """One topic escaped on `grades[-1] < 3` with a last grade of 3, after
    two wrong answers. A recent low grade is the evidence, not the last one."""
    assert classify(BLUFF, grades=[1, 3]) == BLUFFING


def test_a_long_CORRECT_answer_is_not_a_bluff():
    """The guard that stops this firing on every articulate learner."""
    assert classify(REAL, grades=[5, 5]) != BLUFFING
    assert classify(BLUFF, grades=[5, 5]) != BLUFFING


def test_hedging_still_outranks_bluffing():
    """Unsure and wrong is not the same as confident and wrong, and they need
    opposite turns."""
    hedged = [BLUFF[0],
              "I'm not really sure, but maybe the eigenvalue is the stretch?"]
    assert classify(hedged, grades=[2, 2]) == HEDGING


def test_saying_you_do_not_know_still_outranks_everything():
    said = [BLUFF[0], "I don't know."]
    assert classify(said, grades=[2, 2]) == GIVING_UP


def test_no_grades_means_no_claim():
    """A behaviour that depends on being wrong is not claimed without grades."""
    assert classify(BLUFF, grades=None) != BLUFFING
    assert classify(BLUFF, grades=[]) != BLUFFING


def test_a_terse_learner_is_not_a_bluffer():
    assert classify(["Vector v.", "Because it stays on its line."],
                    grades=[2, 2]) != BLUFFING


def test_one_answer_is_never_enough():
    assert classify([BLUFF[0]], grades=[1]) is None


def test_junk_never_raises():
    for bad in (None, [], [""], [None, ""], ["ok"]):
        classify(bad, grades=[1, 1])


def test_the_instruction_says_what_to_DO():
    """Measured at 5/5 for instruction against 0/5 for description."""
    from services.common.learner_behaviour import describe
    note = describe(BLUFF, grades=[2, 2])
    assert "Do not accept it" in note
    assert "Do not praise the fluency" in note
