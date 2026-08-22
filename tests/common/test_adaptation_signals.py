"""Script-tells in a transcript — diagnostics, and their honest limits.

These began as an attempt to replace the judged `adaptation` dimension with a
computed one, on the theory that determinism buys stability. It does not, when
the INPUT is stochastic: across four benchmark runs the tutor's turns and the
simulated student's turns each repeat at a mean text similarity of 0.062. Every
run is a different conversation, so a perfectly deterministic scorer still had
a run-to-run spread of 0.48 — WORSE than the judge's 0.40 — and anti-correlated
with it besides.

What survived are the two signals that found a real defect. They are for
reading a transcript, never for gating on.
"""
from services.common.adaptation_signals import (
    responded_to_stuck, repeated_openings,
)

#: The measured failure, close to verbatim: the student says "idk" and the
#: tutor asks again instead of explaining.
SCRIPTED = [
    {"role": "tutor", "text": "An eigenvector is a special direction. "
                              "Which lines stay straight?"},
    {"role": "student", "text": "maybe the lines through the center? idk."},
    {"role": "tutor", "text": "You correctly identified that lines through "
                              "the center stay straight. However, an "
                              "eigenvector must be non-zero. Why?"},
    {"role": "student", "text": "idk, maybe direction matters?"},
    {"role": "tutor", "text": "You correctly noted that direction matters. "
                              "But you missed the zero vector. Can you say "
                              "why?"},
]

ADAPTED = [
    {"role": "tutor", "text": "Which lines stay straight?"},
    {"role": "student", "text": "idk"},
    {"role": "tutor", "text": "That is fine — here is the whole idea. An "
                              "eigenvector is a direction the matrix only "
                              "stretches, never turns. The zero vector is "
                              "excluded because a point has no direction at "
                              "all, so nothing could be stretched. Does that "
                              "distinction make sense?"},
]


def test_a_scripted_reply_to_a_stuck_learner_is_caught():
    handled, total = responded_to_stuck(SCRIPTED)
    assert total == 2, "both admissions should be counted"
    assert handled == 0, "neither reply stopped questioning"


def test_an_adapted_reply_is_credited():
    handled, total = responded_to_stuck(ADAPTED)
    assert (handled, total) == (1, 1)


def test_only_the_IMMEDIATELY_following_turn_counts():
    """Explaining two turns later is not responding to it."""
    late = [
        {"role": "student", "text": "idk"},
        {"role": "tutor", "text": "Why might that be? What do you think?"},
        {"role": "student", "text": "still no idea"},
        {"role": "tutor", "text": "Here is the full explanation, at length, "
                                  "with all of the detail spelled out for you "
                                  "so that nothing is left implicit at all."},
    ]
    handled, total = responded_to_stuck(late)
    assert total == 2 and handled == 1


def test_repeated_openings_catches_the_same_move_twice():
    repeats, comparisons = repeated_openings(SCRIPTED)
    assert comparisons == 2
    assert repeats >= 1, "'You correctly identified' / 'You correctly noted'"


def test_varied_openings_are_not_flagged():
    varied = [
        {"role": "tutor", "text": "Which lines stay straight under this map?"},
        {"role": "tutor", "text": "Here is the whole argument, start to end."},
        {"role": "tutor", "text": "Suppose instead the matrix were singular."},
    ]
    repeats, comparisons = repeated_openings(varied)
    assert comparisons == 2 and repeats == 0


def test_the_sender_key_is_accepted_too():
    """fsm_logic transcripts use `sender: helga`, not `role: tutor`."""
    fsm_style = [
        {"sender": "user", "text": "idk"},
        {"sender": "helga", "text": "No problem at all, here is the whole "
                                    "idea explained plainly for you, without "
                                    "any further questions from me for the "
                                    "moment, so that you can simply read it."},
    ]
    assert responded_to_stuck(fsm_style) == (1, 1)


def test_junk_never_raises():
    for bad in (None, [], [None], ["x"], [{}], [{"role": "tutor"}]):
        responded_to_stuck(bad)
        repeated_openings(bad)


def test_no_composite_score_is_exposed():
    """It was measured LESS stable than the judge and anti-correlated with it.
    Re-adding one would put a misleading number in front of a release gate."""
    import services.common.adaptation_signals as mod
    assert not hasattr(mod, "score")
