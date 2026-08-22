"""A.9 — demonstrate adaptation instead of describing it.

WHY THIS AND WHY NOW
--------------------
Eight prompt-level interventions were measured on 2026-08-21. Three moved other
dimensions. `adaptation` moved from 1.53 to 2.30 and stalled, and a diagnostic
showed WHICH TOPIC is being taught moves it further (spread 1.25) than anything
done to the tutor (spread 0.67).

Seven turn-level features were tested across 214 dialogues and none predicts the
score; six point the wrong way. So the tutor cannot be told, in rules, what an
adaptive turn looks like — because nobody has been able to state it as a rule,
including me, twice, confidently and wrongly.

What has never been tried is SHOWING it. Every instruction in the system prompt
describes adaptation in the abstract ("adjust to the learner's demonstrated
level"). In-context exemplars are the standard prompt-level approximation of
the specialised training the tutoring literature says pedagogy actually
requires (TeachLM, MathDial fine-tuning) — and specialised training is off the
table here because the model is fixed.

THE DESIGN, AND WHY IT IS CONTRASTIVE
-------------------------------------
A single "good turn" exemplar teaches style, not adaptation. Adaptation is a
RELATION between the learner and the response, so it can only be demonstrated
by holding the situation fixed and varying the learner:

    same concept, same tutor question, two different students, two different
    next turns

That contrast is the lesson. One exemplar cannot carry it.

NOT LIFTED FROM THE BENCHMARK
-----------------------------
These are written by hand on a concept that appears in NO benchmark topic
(simple interest). Using our own high-scoring benchmark transcripts as
exemplars and then measuring on that benchmark would be teaching to the test,
and the resulting number would mean nothing.
"""

#: Held deliberately to two. The prompt already carries the concept document,
#: the aid grammar, the turn state and the contract; exemplars compete for
#: attention with all of it, and four would cost more than they teach.
ADAPTATION_EXEMPLARS = """HOW TO ADAPT — two students, the same question, different replies:

The tutor asked both: "If you leave £100 in an account paying 5% simple interest, what do you have after two years?"

STUDENT A said: "£110. You get £5 each year."
TUTOR REPLIED: "£110 is right, and you got there by adding the same £5 twice — that is exactly what makes it *simple* interest. What would have to change for the second year to pay more than the first?"

STUDENT B said: "idk, £105?"
TUTOR REPLIED: "£105 is one year's worth. You have the hard part already. If one year pays £5, how much do two years pay?"

The question was identical. A got their method named and a harder question; B got their partial answer credited and the same question made smaller. Neither reply would have worked for the other student. Do that."""


def exemplars_for(bloom_level=1, learner_behaviour=None):
    """The exemplar block, or "" when it should be omitted.

    Omitted at high Bloom levels: by then the dialogue itself supplies richer
    context than a worked exemplar about simple interest, and the block is pure
    prompt cost.
    """
    try:
        if bloom_level and int(bloom_level) >= 5:
            return ""
    except (TypeError, ValueError):
        pass
    return ADAPTATION_EXEMPLARS
