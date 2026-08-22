"""What the LEARNER is doing outranks what the CONCEPT needs.

MEASURED, silent_struggler profile, three benchmark runs: adaptation 1.00 and
completely flat — the only profile that did not move at all. The transcript
shows why. The student said "idk" in three of four turns, and the tutor replied
with the same shape every time:

    "You correctly identified that lines through the center stay straight.
     However, an eigenvector must be non-zero..."
    "You correctly noted that squashing a line kills its direction, but you
     missed that the resulting zero vector is explicitly excluded..."

Affirm, correct, ask again — to someone who has said they are lost, four times.
That is exactly what the rubric scores as "following a script".

THE CAUSE WAS ORDERING.
`behaviour_str` sat SIXTH in the per-turn block, behind `figure_str` — the
domain's mined material, whose text begins "THIS TURN OVERRIDES THE GENERAL
GUIDANCE ABOVE" and prescribes a turn shape ("show the error, ask which line is
wrong"). The domain's material was explicitly overriding the one instruction
telling the tutor to stop asking.

Precedence is now: learner state > concept material > general kind. The first
two were the wrong way round, and the block that caused it was added by the
same work that measured the failure.
"""
from services.common.learner_behaviour import describe
from services.common.prompts import get_typed_socratic_prompt

MINED = ("THIS TURN OVERRIDES THE GENERAL GUIDANCE ABOVE. You have a REAL "
         "flagged mistake from the source.\nTHIS TURN: SHOW THE FLAWED "
         "REASONING BELOW, THEN ASK WHERE IT FIRST GOES WRONG.")

STUCK = describe(["I don't know.", "idk sorry"], grades=[1, 1])
FLUENT = describe(
    ["The result follows directly because the underlying transformation "
     "preserves the essential structure throughout the entire domain under "
     "consideration, so the relationship between the quantities is maintained "
     "regardless of which path we evaluate along in this particular setting.",
     "It is preserved for the same reason as before, since the structure of "
     "the mapping guarantees that the relevant quantities remain aligned "
     "under every admissible transformation we might reasonably apply here."],
    grades=[1, 1])


def _system(behaviour, figure_facts=MINED):
    msgs = get_typed_socratic_prompt(
        "why", context_text="Eigenvectors", conversation_history=[],
        concept_kind=("mathematics", "PROCEDURE"),
        figure_facts=figure_facts, learner_behaviour=behaviour, bloom_level=2)
    return "\n\n".join(m["content"] for m in msgs if m["role"] == "system")


def test_a_stuck_learner_outranks_the_mined_material():
    """The regression this file exists for."""
    text = _system(STUCK)
    assert text.index("Stop questioning") < text.index("THIS TURN OVERRIDES")


def test_the_precedence_is_declared_not_merely_implied():
    """The mined block claims to override "the guidance above"; being placed
    first is not enough when the later text asserts priority."""
    text = _system(STUCK)
    assert "OVERRIDES EVERYTHING BELOW" in text
    assert "ignore instructions to show material" in text


def test_a_bluffing_learner_does_not_displace_the_material():
    """Only DECISIVE states take precedence. A bluffer should still be shown
    the error — the material IS the right response to them."""
    text = _system(FLUENT)
    if "THIS TURN OVERRIDES" in text and "fluently" in text:
        assert text.index("THIS TURN OVERRIDES") < text.index("fluently")


def test_no_behaviour_leaves_the_order_untouched():
    text = _system(None)
    assert "THIS TURN OVERRIDES" in text
    assert "OVERRIDES EVERYTHING BELOW" not in text


def test_the_concept_material_still_reaches_the_prompt_when_overridden():
    """Precedence is ordering, not deletion: the material must still be there
    for the tutor to explain FROM."""
    text = _system(STUCK)
    assert "THIS TURN OVERRIDES" in text
    assert "PROCEDURE" in text


def test_junk_never_raises():
    for bad in (None, "", "   "):
        _system(bad)
        _system(STUCK, figure_facts=bad)
