"""When to draw, and what — a rule, not a model call.

THE GAP THIS FILLS
------------------
The renderer supports twelve aid kinds behind a large alias table, validated and
grammar-constrained. **Nothing decided when to use one**, which kind, or that
the learner had already seen three.

WHY A RULE
----------
The same reason mode selection is a rule: a per-turn model call costs ~18-30 s
at 30.1 tok/s and drifts. And because the decision is a rule, the engine can
pick the kind and hand the model **one schema** instead of a twelve-kind menu
plus aliases — which also sidesteps the fact that no JSON-schema minimum binds
in this pipeline (`minItems` is stripped for /v1).

WHAT THE EVIDENCE SUPPORTS, AND WHERE IT RUNS OUT
-------------------------------------------------
The seductive-details effect is real but small and specifically about
DECORATIVE, task-irrelevant graphics raising extraneous load: g = -0.16 over 177
effect sizes (comprehension -0.19, recall -0.17, transfer -0.12), g = -0.33 in
the earlier meta-analysis. Representational graphics help; decorative ones do
not. That supports a representational-only rule, which the asset `role` field
already encodes.

**There is no meta-analysis on visual density in Socratic dialogue.** The
one-aid-per-three-turns cap below is a reasoned default, NOT an evidenced
constant. It is a module-level knob so it can be tuned against measurement
rather than argued about.

WHEN TO DRAW
------------
Not on the first ask. Premature scaffolding removes the reasoning the question
was asked to produce, so the trigger is the SECOND consecutive miss — which
coincides with the existing "at 2 misses, change the explanation" rule, making
the diagram *be* the changed explanation rather than an addition to it.
"""

import logging

logger = logging.getLogger(__name__)

# Reasoned default, not an evidenced constant. Instrument before defending.
MIN_TURNS_BETWEEN_AIDS = 3
DRAW_ON_MISS_STREAK = 2

# Concept shape -> the kind that carries its structure. A lookup, not reasoning.
CONCEPT_AFFINITY = {
    "inequality": "number_line", "interval": "number_line",
    "magnitude": "number_line", "ordering": "number_line",
    "process": "cycle", "feedback": "cycle", "loop": "cycle",
    "lifecycle": "cycle", "equilibrium": "cycle", "cycle": "cycle",
    "comparison": "table", "classification": "table", "taxonomy": "table",
    "trend": "plot", "function": "plot", "relationship": "plot",
    "rate": "plot", "distribution": "plot",
    "part_whole": "fraction", "proportion": "fraction", "ratio": "fraction",
    "sequence": "steps", "procedure": "steps", "derivation": "steps",
    "algorithm": "steps", "worked_example": "steps",
    "count": "bars", "frequency": "bars", "quantity_comparison": "bars",
    "chronology": "timeline", "history": "timeline", "development": "timeline",
    "set": "venn", "overlap": "venn", "membership": "venn",
    "structure": "geometry", "shape": "geometry", "spatial": "geometry",
    "network": "graph", "hierarchy": "graph", "dependency": "graph",
    "causal": "graph", "system": "graph",
}

# The cognitive move the question is making. Overrides concept affinity, because
# it reflects what the learner is being asked to do RIGHT NOW.
QUESTION_AFFINITY = {
    "Contrast": "table",
    "Mechanism": "cycle",
    "Edge Case": "plot",
    "Synthesis": "graph",
    "Application": "steps",
    "Scenario": None,        # a scenario is verbal; a diagram pre-empts it
}


def affinity_for(concept_tags=None, concept_title="", question_type=None):
    """The aid kind this turn wants, or None.

    Question type wins where both have an opinion: the concept's shape is fixed,
    but the cognitive move changes turn to turn and is the better signal for
    what would help right now.
    """
    if question_type in QUESTION_AFFINITY:
        q = QUESTION_AFFINITY[question_type]
        if q:
            return q
        # An explicit None means this question type is deliberately verbal.
        if question_type == "Scenario":
            return None

    for tag in (concept_tags or []):
        k = CONCEPT_AFFINITY.get(str(tag).strip().lower())
        if k:
            return k

    blob = (concept_title or "").lower()
    for tag, kind in CONCEPT_AFFINITY.items():
        if tag.replace("_", " ") in blob:
            return kind
    return None


def decide(state, question_type=None, concept_tags=None, concept_title="",
           learner_asked=False):
    """Should this turn carry a visual, and of what kind?

    Returns {"draw": bool, "kind": str|None, "why": str}. Always explains
    itself — "why did it draw that" has to be answerable, and a rule that cannot
    say why is no better than a model call that cannot.
    """
    kind = affinity_for(concept_tags, concept_title, question_type)

    # A learner asking to see something outranks the cadence. Refusing a direct
    # request to protect a density heuristic is the wrong trade.
    if learner_asked and kind:
        return {"draw": True, "kind": kind, "why": "learner asked to see it"}

    if not kind:
        return {"draw": False, "kind": None,
                "why": "no aid kind carries this concept's structure"}

    since = getattr(state, "turns_since_aid", 99)
    if since < MIN_TURNS_BETWEEN_AIDS:
        return {"draw": False, "kind": kind,
                "why": f"density cap: {since} turn(s) since the last aid, "
                       f"minimum {MIN_TURNS_BETWEEN_AIDS}"}

    misses = getattr(state, "consecutive_misses", 0)
    if misses >= DRAW_ON_MISS_STREAK:
        # This IS the changed explanation the escalation rule calls for, not an
        # addition to it.
        return {"draw": True, "kind": kind,
                "why": f"{misses} consecutive misses — the diagram is the "
                       f"changed explanation"}

    partials = getattr(state, "consecutive_partials", 0)
    if partials >= DRAW_ON_MISS_STREAK:
        return {"draw": True, "kind": kind,
                "why": f"{partials} consecutive partial answers"}

    return {"draw": False, "kind": kind,
            "why": "learner is progressing — a diagram now would pre-empt the "
                   "reasoning the question is asking for"}


def note_turn(state, drew=False):
    """Advance the density counter. Call once per tutor turn."""
    if drew:
        state.turns_since_aid = 0
    else:
        state.turns_since_aid = getattr(state, "turns_since_aid", 0) + 1
    return state.turns_since_aid


def schema_for(kind):
    """The single kind's schema to constrain generation with.

    The whole point of deciding the kind here: the model is asked to fill ONE
    shape rather than choose from twelve plus an alias table. The aliases stay
    server-side as a safety net for a near-miss name and never appear in a
    prompt.
    """
    try:
        from services.common.visual_aids import KINDS
    except ImportError:
        try:
            from visual_aids import KINDS
        except ImportError:
            KINDS = ()
    if KINDS and kind not in KINDS:
        logger.warning(f"[AID] policy chose unknown kind {kind!r}")
        return None
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": [kind]},
            "title": {"type": "string"},
            "caption": {"type": "string"},
            "spec": {"type": "object"},
        },
        "required": ["kind", "spec"],
    }
