"""What the mathematics move miner accepts, and what it refuses.

A move goes into the tutor's prompt as an IMPERATIVE. A bad one is therefore
not a missed opportunity — it is a wasted turn built on material that teaches
nothing, and once it is in the prompt the tutor cannot tell the difference.

In mathematics the refusals matter more than in code. An invented "common
error" is frequently not an error at all, and a "worked example" whose solution
is *See Answer Key* has the tutor promise a solution it cannot show.
"""
from services.domains.mathematics import teaching_moves as tm

WORKED = {
    "problem": r"Find the derivative of $f(x)=x^2-2x$.",
    "solution": (r"$f'(x)=\lim_{h\to 0}\frac{f(x+h)-f(x)}{h}$ "
                 r"Step 1. Substitute to get $\frac{(x+h)^2-2(x+h)-(x^2-2x)}{h}$. "
                 r"Step 2. Expand and cancel to get $2x-2$."),
    "steps": ["Substitute", "Expand and cancel"],
}

FLAGGED_ERROR = (
    r"Common mistake: students often write $\sqrt{a+b}=\sqrt{a}+\sqrt{b}$. "
    r"This is incorrect. In fact $\sqrt{9+16}=5$, not $3+4=7$.")

PROSE_ONLY = ("Common mistake: students often confuse the two ideas. "
              "This is incorrect and in fact they are different.")


def test_a_worked_example_becomes_a_worked_step_move():
    moves = tm.from_examples([WORKED])
    m = tm.best_move(moves)
    assert m and m["kind"] == tm.WORKED_STEP
    assert "2x-2" in m["second"]


def test_a_flagged_error_outranks_a_worked_example():
    """An error is the scarcest material and the strongest move."""
    moves = tm.from_examples([WORKED], notes=[FLAGGED_ERROR])
    assert moves[0]["kind"] == tm.ERROR_HUNT


def test_an_unflagged_note_is_not_an_error_hunt():
    """Only a note the BOOK flags as wrong counts. Inventing errors is how a
    'misconception' turns out to be correct mathematics."""
    fine = r"Note: the derivative of $x^2$ is $2x$."
    assert tm.from_examples([], notes=[fine]) == []


def test_a_flagged_note_with_no_mathematics_is_refused():
    """Prose about an error cannot be shown as the error."""
    assert tm.from_examples([], notes=[PROSE_ONLY]) == []


def test_a_solution_that_is_a_pointer_is_refused():
    """'See Answer Key' promises a solution the tutor cannot show."""
    stub = {"problem": r"Find $\int x\,dx$.", "solution": "See Answer Key."}
    assert tm.from_examples([stub]) == []


def test_two_unrelated_examples_are_not_a_comparison():
    a = {"problem": r"Integrate $\int x^2 dx$ by power rule.",
         "solution": r"$\frac{x^3}{3}+C$ using the power rule directly."}
    b = {"problem": r"Find the limit $\lim_{x\to 0}\frac{\sin x}{x}$.",
         "solution": r"$=1$ by the squeeze theorem applied carefully."}
    moves = tm.from_examples([a, b])
    assert not [m for m in moves if m["kind"] == tm.COMPARE]


def test_comparison_is_withheld_from_a_first_encounter():
    """Comparison depends on prior knowledge (Rittle-Johnson & Star), so it is
    wrong as a learner's first contact with a concept."""
    a = {"problem": r"Solve $2x+6=14$ by isolating $x$ step by step.",
         "solution": r"Subtract 6 then divide by 2 to get $x=4$."}
    b = {"problem": r"Solve $2x+6=14$ by dividing through first instead.",
         "solution": r"Divide by 2 then subtract 3 to get $x=4$."}
    moves = tm.from_examples([a, b])
    assert any(m["kind"] == tm.COMPARE for m in moves), "should be minable"
    assert tm.best_move(moves)["kind"] != tm.COMPARE, "but not offered first"
    assert tm.best_move(moves, allow_compare=True) is not None


def test_junk_never_raises():
    for bad in (None, [], [None], [{}], [{"problem": None}]):
        tm.from_examples(bad)


# ------------------------------------------------------------- prompt blocks

def test_prompt_block_is_imperative_and_inlines_the_material():
    """Measured on the CS domain: DESCRIBED material was used in 0 of 4 turns,
    INSTRUCTED material in 4 of 4."""
    block = tm.prompt_block(tm.best_move(tm.from_examples([WORKED])))
    assert "THIS TURN" in block
    assert "2x-2" in block, "the material must be inline, not referenced"
    assert "PRINCIPLE" in block


def test_worked_step_forbids_asking_the_learner_to_fill_in():
    block = tm.prompt_block(tm.best_move(tm.from_examples([WORKED])))
    assert "do not ask the learner to fill any in" in block.lower()


def test_error_hunt_signposts_for_a_beginner_and_not_otherwise():
    """Low-prior-knowledge learners do better when the error is signposted and
    worse when it is hidden; the reverse holds once they know the topic.

    But SIGNPOSTING IS POINTING, NOT DIAGNOSING. The first version of this told
    the tutor to "say which part contains the mistake", and it duly opened
    "The mistake is in the very first line... The rule broken is that a
    negative exponent indicates a reciprocal" — location and rule, which is the
    whole exercise. For a one-line statement, "which part" IS the answer.
    """
    move = tm.from_examples([], notes=[FLAGGED_ERROR])[0]
    novice = tm.prompt_block(move, beginner=True)
    expert = tm.prompt_block(move, beginner=False)
    assert novice != expert
    # The beginner gets a place to look...
    assert "point their attention" in novice
    # ...and is still not handed the verdict.
    assert "WITHOUT saying what is wrong" in novice
    assert "does not diagnose" in novice
    assert "Do not say where the mistake is" in expert


def test_error_hunt_forbids_giving_the_answer_away():
    """Measured: with the prohibition at the END of the block, the tutor
    opened "The mistake lies in how the negative sign interacts with the
    exponent" — the diagnosis, in its first sentence. The learner then has
    nothing left to find, which is the whole exercise."""
    move = tm.from_examples([], notes=[FLAGGED_ERROR])[0]
    block = tm.prompt_block(move)
    assert "FORBIDDEN THIS TURN" in block
    assert "explaining why it is wrong" in block
    # The shape must be stated BEFORE the material, or it is read too late.
    assert block.index("YOUR ENTIRE TURN") < block.index("THE FLAWED STATEMENT")


def test_predict_never_asks_for_a_number():
    block = tm.prompt_block({"kind": tm.PREDICT, "first": r"$y=x^2$",
                             "second": ""})
    assert "Never ask for a numeric value" in block


def test_prompt_block_of_none_is_empty():
    assert tm.prompt_block(None) == ""


# ------------------------------------------------------- speakability

UNSPEAKABLE = {
    "problem": r"Evaluate $\nexists\owns\wp{x}$ for the given case.",
    "solution": r"The value follows from $\nexists\owns\wp{x} = 1$ directly here.",
}


def test_speakable_material_is_preferred_over_unspeakable():
    """Helga teaches by VOICE: a move whose notation math_speech cannot render
    is one the learner hears as raw markup. Before math_speech learned \\int,
    every integration example in a calculus chapter was unspeakable, and
    nothing stopped one being chosen over a clean alternative beside it."""
    moves = tm.from_examples([UNSPEAKABLE, WORKED])
    same_kind = [m for m in moves if m["kind"] == tm.WORKED_STEP]
    assert len(same_kind) == 2
    assert tm.is_speakable(same_kind[0]), "the speakable one must come first"


def test_speakability_is_a_tie_break_not_a_filter():
    """Dropping every example with one exotic command costs more teaching than
    it saves, and the KIND ranking must still dominate."""
    moves = tm.from_examples([UNSPEAKABLE], notes=[FLAGGED_ERROR])
    assert moves[0]["kind"] == tm.ERROR_HUNT, "kind still outranks speech"
    assert any(m["kind"] == tm.WORKED_STEP for m in moves), "not filtered out"


def test_is_speakable_never_raises():
    for bad in (None, {}, {"first": None}, {"first": "\\"}):
        tm.is_speakable(bad or {})
