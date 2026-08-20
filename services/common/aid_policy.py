"""When should the tutor show a diagram?

Not every turn. That is the whole problem this module exists to solve.

A diagram on every turn is worse than none at all. Mayer's coherence principle
is blunt about it: extraneous visuals *reduce* learning rather than merely
failing to help. And a picture that arrives every turn stops being an event —
the learner's eye slides off it. The redundancy principle says the same thing
from the other side: a visual that restates what the sentence already said adds
cognitive load and subtracts nothing.

So the question is not "can we draw something?" but "is this the moment where a
picture carries something the prose cannot?"

THREE OUTCOMES, not two:

    none      most turns
    reuse     a diagram built at COURSE-CREATION time fits this moment
    generate  no precomputed aid fits; let the model draw one now

`reuse` is the preferred path and the reason this module is shaped the way it
is. Course build already knows the concept, its misconceptions and its worked
examples, and at build time a retry costs nothing — so the hard diagrams
(geometry above all) get drawn there, validated, and regenerated against a named
failure, exactly as the depth contract already works. Runtime then *selects*
rather than *authors*: no JSON reliability risk, no extra latency, no variance.

`generate` remains for the long tail a course build cannot anticipate — a
student's own numbers, an unplanned tangent. It is the lower-trust path and the
policy reaches for it last.

DETERMINISTIC ON PURPOSE. No LLM call. At ~30 s per call, asking the model
whether it would like to draw would cost more than the drawing saves — the same
reasoning that made `_detect_ignorance` rule-based and `classify_domains`
keyword-based. Everything here is a pure function of the moment, so it is
testable and its behaviour is inspectable in a log line.
"""
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# --- Budgets -----------------------------------------------------------------
# A concept is a few minutes of dialogue. Three diagrams across it is generous;
# a fourth is decoration. Younger learners get one more — the multimedia effect
# is strongest for novices, and weakest (sometimes negative) for the fluent.
MAX_AIDS_PER_CONCEPT = 3
MAX_AIDS_YOUNG = 4
YOUNG_BANDS = ("K-2", "3-5")

# Turns that must pass after a diagram before another may appear. This is the
# single most important number here: it is what stops a picture every turn.
COOLDOWN_TURNS = 2

# A whole SESSION cap, on top of the per-concept budget. A learner working
# through eight concepts could otherwise legitimately collect 24 diagrams and
# experience the session as a slideshow, with every individual decision correct.
# Restraint has to hold at both scales.
MAX_AIDS_PER_SESSION = 10

# Score at or above which a diagram is warranted. Tuned so that the ordinary
# back-and-forth of a correct-ish dialogue scores below it, and genuine trouble
# clears it comfortably.
THRESHOLD = 4

# Teaching moments a course build can anticipate and draw in advance.
SLOT_OPENING = "opening"                 # the object of inquiry, turn one
SLOT_STUCK = "stuck"                     # the learner is lost; prose has failed
SLOT_WORKED = "worked_example"           # a staged method
SLOT_SUMMARY = "summary"                 # consolidation at the end
SLOT_MISCONCEPTION = "misconception"     # keyed: "misconception:0"


# --- Subject routing ---------------------------------------------------------
# Keyword-based, deliberately not an LLM call — this runs on every turn.
# Its real job is NARROWING: handing a 9B model a menu of two or three plausible
# kinds instead of eleven measurably improves what it picks, and a mis-route is
# cheap because the model may still choose something else.
# COVERAGE, NOT FITTING. These lists were extended after tools/bench_domains.py
# showed concepts a diagram obviously serves getting none. The terms added are
# ones any course in the field uses -- "receptor", "call stack", "lattice" --
# NOT the exact strings in the benchmark fixtures. That distinction is the
# whole difference between fixing the policy and teaching it the answers.
_DOMAIN_KINDS = (
    (r"\b(triangle|angle|circle|polygon|perimeter|area|hypotenuse|parallel|"
     r"congruen|similar|geometr|pythagor|radius|diameter|lattice|crystal|"
     r"molecul|bond(ing|s)?|arrangement|packing|orbital)", ("geometry",)),
    (r"\b(fraction|numerator|denominator|equivalent|simplify.*fraction|"
     r"percent|decimal|ratio|proportion)", ("fraction", "number_line")),
    (r"\b(inequalit|negative number|integer|absolute value|round(ing)?|"
     r"number line|greater than|less than)", ("number_line",)),
    (r"\b(function|slope|gradient|quadratic|parabola|linear equation|"
     r"intercept|exponential|derivative|asymptot|"
     # Linear algebra was missing entirely, so "Eigenvalues" -- a concept a
     # plot obviously serves, since the whole idea is a vector that keeps its
     # direction -- scored below the threshold and never got a diagram.
     r"eigen|matri(x|ces)|vector|transformation|rate of change|"
     r"partial derivative|tangent line)", ("plot",)),
    (r"\b(data|survey|statistic|mean|median|mode|frequenc|distribution|"
     r"average|measurement|rainfall|population)", ("bars", "plot")),
    (r"\b(cycle|photosynth|respiration|water cycle|feedback|loop|"
     r"circulat|recurring|seasons|generation|evolv|selection pressure|"
     r"adapt(s|ation)?|receptor|pathway|regulat|homeostas|metabol|"
     r"transmission|reproduc)", ("cycle",)),
    # A timeline needs a SEQUENCE. A single year does not make one: the bare
    # `[12][0-9]{3}` trigger meant "The Battle of Hastings was fought on 14
    # October 1066" -- a date to be told, not drawn -- scored as a visual
    # subject and the policy asked for a timeline to teach it. Two or more
    # dates, or explicit sequence language, is the actual signal.
    (r"\b(century|revolution|dynasty|era|timeline|chronolog|ancient|medieval|"
     r"sequence of|ordered|followed by|led up to|in turn|escalat)",
     ("timeline",)),
    (r"[12][0-9]{3}\b(?:[^.]{0,200}?[12][0-9]{3}\b)", ("timeline",)),
    (r"\b(compare|contrast|versus|vs\.|difference between|distinguish|"
     r"unlike|whereas)", ("table", "venn")),
    (r"\b(classif|categor|taxonom|belongs to|subset|overlap|"
     r"both .* and)", ("venn", "graph")),
    (r"\b(system|network|relationship|causes|leads to|depends on|"
     r"food (web|chain)|flow of|process|call stack|recursi|hierarch|"
     r"precedent|binds|inherit(s|ance)?|reference(s)? itself|"
     r"prerequisite|upstream|downstream)", ("graph",)),
    (r"\b(step|method|procedure|algorithm|solve for|how to|"
     r"first .* then|derivation)", ("steps",)),
)


# --- Content a diagram cannot carry ------------------------------------------
# A convention, a name, a symbol or a bare date is ARBITRARY: true because
# somebody decided it, not because of a structure a picture could show. There
# is nothing to draw, and drawing anyway spends the learner's attention on
# decoration. This is a NEGATIVE signal because keyword affinity gets it
# exactly backwards: "Why the partial derivative uses a curly d" contains
# "partial derivative" and scores as a plot, when the subject is the SHAPE OF
# A SYMBOL.
_ARBITRARY = re.compile(
    r"\b(convention|notation|symbol for|is called|are called|the name|named "
    r"after|abbreviat|terminolog|nomenclature|by definition we write|"
    r"why (is|do) (it|we|they) (called|write|use)|stands for|"
    r"reference range|citation format|indexes? from)\b", re.I)


def is_arbitrary(*texts):
    """True when the concept is a convention, a name or a bare fact.

    Deliberately narrow. A false positive costs one diagram; a false negative
    costs a diagram of nothing.
    """
    blob = " ".join(t for t in texts if t).lower()[:4000]
    return bool(_ARBITRARY.search(blob))


def suggest_kinds(*texts):
    """Kinds plausibly useful for this subject matter, most specific first."""
    blob = " ".join(t for t in texts if t).lower()[:4000]
    if is_arbitrary(blob):
        return ()                     # nothing to draw; see _ARBITRARY
    out = []
    for pattern, kinds in _DOMAIN_KINDS:
        if re.search(pattern, blob):
            for k in kinds:
                if k not in out:
                    out.append(k)
    return tuple(out[:4])


# --- The moment --------------------------------------------------------------
@dataclass
class AidMoment:
    """Everything the decision depends on. Pure data — no FSM, no I/O, so the
    policy can be exercised exhaustively in tests without a running system."""
    teaching_mode: str = "QUESTION"          # QUESTION | LECTURE
    is_concept_opening: bool = False
    last_grade: int = 0                      # 0 = no answer yet, else 1-4
    retry_count: int = 0
    miss_streak: int = 0
    correct_streak: int = 0
    bloom_level: int = 1
    question_count: int = 0
    grade_band: str = "6-8"
    concept_title: str = ""
    concept_text: str = ""
    turns_since_aid: int = 99                # large = none shown yet
    aids_shown_this_concept: int = 0
    kinds_shown: tuple = ()
    available_slots: tuple = ()              # precomputed slots on this concept
    active_misconception: int = None         # index into the concept's list
    # Session-scope, spanning concepts — the per-concept budget cannot see these.
    session_aids_shown: int = 0
    recent_kinds: tuple = ()                 # most recent first, across concepts
    enabled: bool = True


@dataclass
class AidDecision:
    action: str = "none"                     # none | reuse | generate
    slot: str = None                         # which precomputed aid to show
    reason: str = ""                         # one line, for the log
    score: int = 0
    suggested_kinds: tuple = ()
    avoid_kinds: tuple = ()
    urgency: str = "optional"                # optional | encouraged

    @property
    def wants_aid(self):
        return self.action in ("reuse", "generate")


def _budget(grade_band):
    return MAX_AIDS_YOUNG if grade_band in YOUNG_BANDS else MAX_AIDS_PER_CONCEPT


def _is_stuck(m):
    """Stuck overrides the cooldown. This is the one case where back-to-back
    diagrams are right: the learner has already failed to get there in words,
    so waiting two more turns to show a picture helps nobody."""
    return (m.teaching_mode == "LECTURE"
            or m.miss_streak >= 2
            or (m.last_grade and m.last_grade <= 1))


def decide(moment):
    """Return an AidDecision for this turn. Never raises."""
    try:
        return _decide(moment)
    except Exception as e:                    # a policy bug must not cost a turn
        logger.warning("Aid policy failed, defaulting to no diagram: %s", e)
        return AidDecision(action="none", reason=f"policy error: {e}")


def _decide(m):
    if not m.enabled:
        return AidDecision(reason="visual aids disabled")

    # --- Hard gates. These are budget, not judgement, and come first so no
    # amount of pedagogical enthusiasm can talk past them. -------------------
    budget = _budget(m.grade_band)
    if m.aids_shown_this_concept >= budget:
        return AidDecision(reason=f"concept budget spent ({budget})")
    if m.session_aids_shown >= MAX_AIDS_PER_SESSION:
        return AidDecision(reason=f"session budget spent ({MAX_AIDS_PER_SESSION})")

    stuck = _is_stuck(m)
    if m.turns_since_aid < COOLDOWN_TURNS and not stuck:
        return AidDecision(
            reason=f"cooldown ({m.turns_since_aid}/{COOLDOWN_TURNS} turns since last)")

    # --- Score. Positives are moments where a picture carries what prose
    # cannot; negatives are moments where it would interrupt or patronise. ---
    score, why = 0, []

    if m.is_concept_opening:
        score += 3
        why.append("concept opening")

    if m.teaching_mode == "LECTURE":
        # The strongest signal available. LECTURE fires when the student said
        # "I don't know" or scored 1 — prose has already been tried and failed,
        # so switching representation is the obvious next move.
        score += 4
        why.append("lecture (prose already failed)")

    if m.miss_streak >= 2:
        score += 3
        why.append(f"missed {m.miss_streak} in a row")
    elif m.last_grade == 2 and m.retry_count >= 2:
        score += 2
        why.append("partial answers, stalled")

    kinds = suggest_kinds(m.concept_title, m.concept_text)
    if kinds:
        score += 2
        why.append(f"visual subject ({kinds[0]})")

    if m.grade_band in YOUNG_BANDS:
        score += 1
        why.append("young learner")

    # Expertise reversal: a diagram that helps a novice can actively hinder
    # someone already fluent, who must now reconcile it with the model they
    # have. High Bloom means analysis and evaluation, where the work is verbal.
    if m.bloom_level >= 4:
        score -= 3
        why.append(f"bloom {m.bloom_level} (abstract)")

    if m.correct_streak >= 2:
        score -= 2
        why.append("answering well — don't interrupt")

    if m.question_count > 6:
        score -= 2
        why.append("late in concept")

    # Don't repeat yourself. A second diagram of the same KIND in one concept is
    # usually the model redrawing what is already on screen.
    avoid = tuple(dict.fromkeys(m.kinds_shown))
    if len(m.kinds_shown) >= 2:
        score -= 1
        why.append("already illustrated twice")

    # Variety across CONCEPTS. Three number lines in a row are individually
    # justified and collectively monotonous, and monotony is how a diagram stops
    # being looked at. Nudge toward a kind the learner has not just seen.
    recent = tuple(m.recent_kinds[:2])
    if kinds and recent and all(k in recent for k in kinds[:1]):
        score -= 1
        why.append(f"just showed {recent[0]}")
    if recent:
        avoid = tuple(dict.fromkeys(avoid + recent))

    reason = "; ".join(why) or "nothing notable"
    if score < THRESHOLD:
        return AidDecision(action="none", score=score,
                           reason=f"score {score}/{THRESHOLD} — {reason}",
                           suggested_kinds=kinds, avoid_kinds=avoid)

    # --- Prefer a diagram that was built and checked at course-creation time.
    slot = _pick_slot(m)
    if slot:
        return AidDecision(action="reuse", slot=slot, score=score,
                           reason=f"score {score} — {reason}; reusing '{slot}'",
                           suggested_kinds=kinds, avoid_kinds=avoid,
                           urgency="encouraged" if stuck else "optional")

    return AidDecision(action="generate", score=score,
                       reason=f"score {score} — {reason}; no precomputed aid fits",
                       suggested_kinds=kinds, avoid_kinds=avoid,
                       urgency="encouraged" if stuck else "optional")


def _pick_slot(m):
    """Choose which precomputed aid fits this moment, or None.

    Ordered by how specific the match is: a diagram drawn for the exact
    misconception the learner just displayed beats a generic opener.
    """
    have = set(m.available_slots or ())
    if not have:
        return None

    if m.active_misconception is not None:
        keyed = f"{SLOT_MISCONCEPTION}:{m.active_misconception}"
        if keyed in have:
            return keyed

    order = []
    if m.is_concept_opening:
        order.append(SLOT_OPENING)
    if _is_stuck(m):
        order += [SLOT_STUCK, SLOT_WORKED]
    if m.question_count > 4:
        order.append(SLOT_SUMMARY)
    order += [SLOT_OPENING, SLOT_STUCK, SLOT_WORKED, SLOT_SUMMARY]

    for slot in order:
        if slot in have:
            return slot
    # Any unused misconception diagram is still better than authoring one.
    for slot in sorted(have):
        if slot.startswith(SLOT_MISCONCEPTION):
            return slot
    return None


def prompt_nudge(decision):
    """The line appended to the tutor prompt when a diagram is wanted.

    Only ever added on a `generate` decision. When the policy says no, the aid
    grammar is left OUT of the prompt entirely — which is the actual enforcement
    mechanism: a model that has not been told the syntax cannot use it, and the
    ~400 tokens of grammar are not spent on a turn that does not want a picture.
    """
    if decision.action != "generate":
        return ""
    bits = []
    if decision.urgency == "encouraged":
        bits.append("A diagram would genuinely help THIS turn — the student is "
                    "stuck and words have not worked. Draw one.")
    else:
        bits.append("You may draw ONE diagram this turn if it makes your question "
                    "askable in a way words cannot. If it would only decorate, do not.")
    if decision.suggested_kinds:
        bits.append("Most likely useful here: "
                    + ", ".join(decision.suggested_kinds) + ".")
    if decision.avoid_kinds:
        bits.append("Already shown this concept (do NOT repeat): "
                    + ", ".join(decision.avoid_kinds) + ".")
    return " ".join(bits)
