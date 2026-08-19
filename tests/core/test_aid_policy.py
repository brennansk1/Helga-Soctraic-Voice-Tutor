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
