"""When to draw, and what — a rule, not a model call.

The seductive-details effect is real but small and specifically about
DECORATIVE graphics (g = -0.16 over 177 effect sizes). There is NO meta-analysis
on visual density in Socratic dialogue, so the density cap here is a reasoned
default, not an evidenced constant.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.aid_policy import (  # noqa: E402
    MIN_TURNS_BETWEEN_AIDS, affinity_for, decide, note_turn, schema_for)
from services.core.session_state import SessionState  # noqa: E402


def _state(misses=0, partials=0, since=99):
    s = SessionState("c1", "con_a")
    s.consecutive_misses = misses
    s.consecutive_partials = partials
    s.turns_since_aid = since
    return s


class TestAffinity(unittest.TestCase):
    def test_question_type_beats_concept_shape(self):
        """The concept's shape is fixed; the cognitive move changes turn to turn
        and is the better signal for what would help right now."""
        self.assertEqual(
            affinity_for(concept_tags=["process"], question_type="Contrast"),
            "table")

    def test_concept_tags_are_used_when_the_question_is_neutral(self):
        self.assertEqual(affinity_for(concept_tags=["inequality"]), "number_line")

    def test_the_title_is_a_fallback(self):
        self.assertEqual(affinity_for(concept_title="The carbon cycle"), "cycle")

    def test_a_scenario_question_is_deliberately_verbal(self):
        """A diagram pre-empts the reasoning a scenario is asking for."""
        self.assertIsNone(affinity_for(concept_tags=["process"],
                                       question_type="Scenario"))

    def test_an_unmappable_concept_gets_nothing(self):
        self.assertIsNone(affinity_for(concept_title="Assorted trivia"))


class TestDecide(unittest.TestCase):
    def test_no_diagram_while_the_learner_is_progressing(self):
        """Premature scaffolding removes the reasoning the question asked for."""
        d = decide(_state(), question_type="Mechanism", concept_title="carbon cycle")
        self.assertFalse(d["draw"])
        self.assertIn("pre-empt", d["why"])

    def test_the_second_miss_draws(self):
        d = decide(_state(misses=2), question_type="Mechanism",
                   concept_title="carbon cycle")
        self.assertTrue(d["draw"])
        self.assertEqual(d["kind"], "cycle")
        self.assertIn("changed explanation", d["why"])

    def test_two_partials_also_draw(self):
        self.assertTrue(decide(_state(partials=2), question_type="Mechanism",
                               concept_title="carbon cycle")["draw"])

    def test_the_density_cap_holds(self):
        d = decide(_state(misses=2, since=1), question_type="Mechanism",
                   concept_title="carbon cycle")
        self.assertFalse(d["draw"])
        self.assertIn("density cap", d["why"])

    def test_a_learner_asking_outranks_the_cadence(self):
        """Refusing a direct request to protect a density heuristic is the wrong
        trade."""
        d = decide(_state(since=0), question_type="Mechanism",
                   concept_title="carbon cycle", learner_asked=True)
        self.assertTrue(d["draw"])

    def test_no_kind_means_no_diagram_however_many_misses(self):
        self.assertFalse(decide(_state(misses=5),
                                concept_title="assorted trivia")["draw"])

    def test_every_decision_explains_itself(self):
        for d in (decide(_state()), decide(_state(misses=2),
                                           concept_title="carbon cycle")):
            self.assertTrue(d["why"])


class TestCadence(unittest.TestCase):
    def test_drawing_resets_the_counter(self):
        s = _state(since=9)
        note_turn(s, drew=True)
        self.assertEqual(s.turns_since_aid, 0)

    def test_the_counter_advances_and_re_enables(self):
        s = _state(since=0)
        for _ in range(MIN_TURNS_BETWEEN_AIDS):
            note_turn(s, drew=False)
        d = decide(s, question_type="Mechanism", concept_title="carbon cycle")
        s.consecutive_misses = 2
        self.assertTrue(decide(s, question_type="Mechanism",
                               concept_title="carbon cycle")["draw"])


class TestSchema(unittest.TestCase):
    def test_generation_is_constrained_to_one_kind(self):
        """The whole point of deciding the kind here: the model fills ONE shape
        rather than choosing from twelve plus an alias table."""
        sch = schema_for("cycle")
        self.assertEqual(sch["properties"]["kind"]["enum"], ["cycle"])

    def test_an_unknown_kind_is_refused(self):
        self.assertIsNone(schema_for("hologram"))


# ---------------------------------------------- B.1: request, not permission
#
# `visual_policy` sat at 1.00 in six of seven domains, which the scorer defines
# as "the policy asked for a figure and none was produced". The cause was in
# the prompt, not the policy: on the production path the aid grammar is emitted
# ONLY on a `generate` decision, yet it opened "optional — most turns need
# none" and closed "No diagram is better than a pointless one". The model read
# the discouragement in exactly the case where the cost had already been
# weighed in the diagram's favour, and it took the hint.

def _generate(urgency="allowed", kinds=("plot",)):
    from services.common import aid_policy as ap
    return ap.AidDecision(action="generate", urgency=urgency,
                          suggested_kinds=list(kinds))


def test_a_generate_nudge_instructs_rather_than_permits():
    from services.common.aid_policy import prompt_nudge
    nudge = prompt_nudge(_generate())
    assert "Draw ONE diagram" in nudge
    assert "You may" not in nudge, "permission invites a refusal"
    assert "if it would only decorate" not in nudge


def test_the_generate_nudge_still_scopes_the_choice():
    """Removing the veto must not remove the guidance about WHAT to draw."""
    from services.common.aid_policy import prompt_nudge
    nudge = prompt_nudge(_generate(kinds=("plot", "number_line")))
    assert "plot" in nudge and "number_line" in nudge


def test_a_non_generate_decision_still_yields_no_nudge():
    from services.common import aid_policy as ap
    for action in ("none", "reuse"):
        d = ap.AidDecision(action=action)
        assert ap.prompt_nudge(d) == ""


def test_the_requested_framing_drops_the_optional_header():
    from services.common import prompts
    requested = prompts.aid_rules(requested=True)
    optional = prompts.aid_rules(requested=False)
    assert "most turns need none" in optional
    assert "most turns need none" not in requested
    assert "this turn needs one" in requested.lower()


def test_both_framings_carry_the_identical_grammar():
    """Only the posture may differ. A kind available in one and not the other
    would be a silent capability difference."""
    from services.common import prompts
    from services.common.visual_aids import KINDS
    for requested in (True, False):
        text = prompts.aid_rules(requested=requested)
        for kind in KINDS:
            if kind == "image":          # build-time only, never model-authored
                continue
            assert kind in text, f"{kind} missing from requested={requested}"
        assert '```aid' in text and '"stage"' in text


def test_neither_framing_lets_the_figure_give_the_answer():
    """The restraint that must survive B.1: staging is not negotiable."""
    from services.common import prompts
    for requested in (True, False):
        text = prompts.aid_rules(requested=requested)
        assert "never draw the result you are asking them to find" in text
        assert "hands over the answer" in text


def test_the_production_path_uses_the_requested_framing():
    """The whole point: reaching the grammar MEANS the policy said generate."""
    from services.common import prompts
    block = prompts._aid_prompt_block(_generate())
    assert "most turns need none" not in block
    assert "this turn needs one" in block.lower()


def test_a_caller_with_no_policy_keeps_the_cautious_framing():
    """aid_probe and older tests have nothing weighing the call for them."""
    from services.common import prompts
    block = prompts._aid_prompt_block(None)
    if block:                                  # empty when aids are disabled
        assert "most turns need none" in block


# ------------------------------------------- the dead branches in _ARBITRARY
#
# `abbreviat` and `terminolog` are prefixes, and they sat inside a group closed
# by `\b`. `terminolog\b` demands a non-word character straight after the "g",
# which no real word has, so both branches matched NOTHING for as long as they
# existed. Silent under-detection: the concepts they were meant to catch were
# taught as if they could be derived.

def test_every_branch_of_the_arbitrary_pattern_can_actually_match():
    """A branch that cannot match anything is a lie in the source."""
    from services.common.aid_policy import is_arbitrary
    for word in ("terminology", "terminologies", "abbreviated", "abbreviation",
                 "convention", "nomenclature", "stands for",
                 "is called", "named after", "citation format"):
        assert is_arbitrary(f"this section explains the {word} used here"), (
            f"{word!r} is in _ARBITRARY but matches nothing")


def test_the_arbitrary_pattern_does_not_fire_on_derivable_text():
    from services.common.aid_policy import is_arbitrary
    for text in ("the vector is scaled by the eigenvalue",
                 "pressure falls as the fluid speeds up",
                 "determine the terminal velocity of the falling object",
                 "he was aware of the warranty",
                 "binary search halves the interval each step"):
        assert not is_arbitrary(text), f"false positive: {text!r}"


# ------------------------------------------------- B.2: prefer the built asset
#
# `reuse` and `generate` were gated on the same score, which priced them as if
# they cost the same. A reuse spends no model call, adds no latency, carries
# provenance, and was checked at build time. Courses ship 44 assets across 24
# concepts; the learner was shown almost none of them.

def _moment(**kw):
    from services.common.aid_policy import AidMoment, SLOT_OPENING
    base = dict(concept_title="Eigenvalues", concept_text="a plot of vectors",
                available_slots=(SLOT_OPENING,))
    base.update(kw)
    return AidMoment(**base)


def test_a_prebuilt_slot_is_shown_where_a_fresh_one_would_not_be():
    """The whole of B.2, in one assertion."""
    from services.common import aid_policy as ap
    m = _moment()                              # visual subject only: score 2
    assert ap.THRESHOLD > ap.REUSE_THRESHOLD, "the bars must differ"
    with_slot = ap.decide(m)
    without = ap.decide(_moment(available_slots=()))
    assert with_slot.action == "reuse", with_slot.reason
    assert without.action == "none", (
        "the same moment with nothing prebuilt must NOT author one")


def test_reuse_still_loses_to_a_spent_budget():
    """B.2 lowers the pedagogical bar, never the hard caps."""
    from services.common import aid_policy as ap
    spent = ap.decide(_moment(aids_shown_this_concept=99))
    assert spent.action == "none" and "budget" in spent.reason
    sess = ap.decide(_moment(session_aids_shown=99))
    assert sess.action == "none" and "budget" in sess.reason


def test_reuse_still_respects_the_cooldown():
    """A picture every turn is the failure the cooldown exists to prevent."""
    from services.common import aid_policy as ap
    d = ap.decide(_moment(turns_since_aid=0))
    assert d.action == "none" and "cooldown" in d.reason


def test_a_moment_with_no_reason_at_all_still_shows_nothing():
    """REUSE_THRESHOLD is lower, not zero."""
    from services.common import aid_policy as ap
    from services.common.aid_policy import SLOT_OPENING
    d = ap.decide(ap.AidMoment(concept_title="Consideration in contract law",
                               concept_text="the doctrine is called consideration",
                               available_slots=(SLOT_OPENING,)))
    assert d.action == "none", (
        f"arbitrary content has nothing to draw; got {d.action}: {d.reason}")


def test_reuse_is_still_preferred_over_generate_when_both_qualify():
    from services.common import aid_policy as ap
    hot = dict(is_concept_opening=True, teaching_mode="LECTURE")
    assert ap.decide(_moment(**hot)).action == "reuse"
    assert ap.decide(_moment(available_slots=(), **hot)).action == "generate"


# --------------------------------------- the code aid was built and never asked for
#
# Found 2026-08-21: NO pattern in `_DOMAIN_KINDS` yielded the `code` kind. The
# renderer worked (verified in a browser: blanked line, staged reveal, the
# answer not leaking at stage 0), the prompt grammar advertised `code`, and the
# policy never once suggested it — so every code listing the product can draw
# was dark. "Debugging a syntax error in this function" suggested `plot`.
#
# `executable_precision` is the weakest computer-science dimension at 2.87,
# which is what a tutor that hand-waves instead of showing code looks like.

def test_the_code_kind_is_reachable_at_all():
    """A kind nothing can suggest is a feature that does not exist."""
    from services.common.aid_policy import _DOMAIN_KINDS
    assert any("code" in kinds for _, kinds in _DOMAIN_KINDS), (
        "no pattern yields `code`; the renderer is unreachable")


def test_programming_subjects_suggest_code():
    from services.common.aid_policy import suggest_kinds
    for text in ("debugging a syntax error in this function",
                 "writing a for loop over an array",
                 "why python lists index from zero, an offset from a base address",
                 "a recursive call suspends the current frame on the call stack",
                 "reading a traceback to find the failing line"):
        assert "code" in suggest_kinds(text), text


def test_mathematics_does_NOT_suggest_code():
    """'function' and 'variable' are ambiguous and already route to `plot`.
    A quadratic function must never be drawn as a code listing."""
    from services.common.aid_policy import suggest_kinds
    for text in ("a quadratic function has a parabola shaped graph",
                 "the derivative measures the rate of change of a function",
                 "an eigenvector keeps its direction under the transformation"):
        assert "code" not in suggest_kinds(text), text


def test_other_domains_do_not_suggest_code():
    from services.common.aid_policy import suggest_kinds
    for text in ("natural selection changes allele frequencies",
                 "the causes of the first world war in 1914 and 1918",
                 "how a metaphor creates meaning in a sonnet"):
        assert "code" not in suggest_kinds(text), text


def test_code_is_a_renderable_kind():
    """Suggesting a kind the browser cannot draw would be worse than silence."""
    from services.common.visual_aids import KINDS
    assert "code" in KINDS


def test_sql_is_reachable_because_sql_is_a_language():
    """SQL's whole surface is keywords, and "sql query" matched none of them.

    Six for six — SELECT/WHERE, INNER JOIN, GROUP BY/HAVING, LEFT JOIN, CTE,
    window function — the policy suggested nothing at all, so it scored below
    threshold and drew nothing. A tutor that cannot show a query cannot teach
    SQL, which is the language a learner is most likely to be shown code for.
    """
    from services.common.aid_policy import suggest_kinds
    for text in ("a SELECT statement filters rows with a WHERE clause",
                 "an INNER JOIN matches rows from two tables on a key",
                 "GROUP BY aggregates rows and HAVING filters the groups",
                 "a LEFT JOIN keeps unmatched rows with NULLs",
                 "a common table expression names a subquery for reuse",
                 "a window function computes a running total over a partition",
                 "reading a query plan to see why an index was not used"):
        assert "code" in suggest_kinds(text), text


def test_ambiguous_words_do_not_drag_other_subjects_into_code():
    """A sonnet has an argument; a normal distribution has a parameter.

    Both matched `code` on the first attempt at this pattern.
    """
    from services.common.aid_policy import suggest_kinds
    for text in ("the sonnet volta turns the argument at line nine",
                 "the parameter of a normal distribution is its mean",
                 "the argument of the poem shifts in the sestet"):
        assert "code" not in suggest_kinds(text), text


def test_every_advertised_kind_is_reachable_from_some_pattern():
    """The defect underneath both failures: `code` was advertised to the model
    and no pattern could ever suggest it. This is the guard that generalises —
    a classifier that silently stopped emitting a kind would be just as
    invisible as the keyword gap was.
    """
    from services.common.aid_policy import _DOMAIN_KINDS
    from services.common.visual_aids import KINDS
    reachable = {k for _, kinds in _DOMAIN_KINDS for k in kinds}
    # `image` is build-time only; `fraction`/`venn`/`table` may be reached via
    # affinity_for tags rather than text patterns.
    never = {"code"} - reachable
    assert not never, f"advertised but unreachable: {sorted(never)}"


def test_notation_alone_no_longer_means_arbitrary():
    """`notation` was a bare branch, and it caught DERIVABLE concepts.

    "Big-O notation" and "sigma notation" are ideas a learner can be walked
    to; their content is the concept, not the squiggle. Routing them to
    arbitrary told the tutor to simply STATE Big-O rather than build it, and
    suppressed the diagram for it at the same time — `suggest_kinds` returns
    nothing at all for arbitrary content.
    """
    from services.common.aid_policy import is_arbitrary, suggest_kinds
    for text in ("Big-O notation and time complexity",
                 "sigma notation for summing a series",
                 "musical notation and time signatures"):
        assert not is_arbitrary(text), text
    assert "code" in suggest_kinds("Big-O notation and time complexity")


def test_notation_WITH_convention_context_is_still_arbitrary():
    """The real case must keep working: a symbol chosen by fiat."""
    from services.common.aid_policy import is_arbitrary
    for text in ("The symbol used for partial derivatives is a stylised d, "
                 "written as a curly d. It is a NOTATIONAL CONVENTION.",
                 "the notation for this was chosen historically",
                 "zero-based indexing is a CONVENTION inherited from C"):
        assert is_arbitrary(text), text
