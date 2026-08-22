"""The tutor prompt's stable prefix must stay stable across turns.

WHY THIS TEST EXISTS
--------------------
Measured on this hardware: a prompt whose first ~1700 tokens are unchanged
between turns is served from Ollama's slot cache in 0.76s instead of 6.49s —
an 8.6x cut in time-to-first-token, and the single largest latency win in the
tutoring loop. Change the prefix and the cost comes straight back (measured:
3.66s the moment the prefix differed).

The prefix only stays reusable while every PER-TURN block sits AFTER the
concept document. That ordering is invisible: put `turn_state` back above
`context_text` and nothing errors, no test fails, and every lesson silently
costs about six seconds a turn again.

So the ordering is asserted here rather than trusted.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from services.common.prompts import (  # noqa: E402
    get_socratic_tutor_prompt, get_typed_socratic_prompt, _PREFIX_END,
)

CONTEXT = ("## Socratic Hooks\nWhy does a DAG need refs?\n\n## Notes\n"
           + "A dbt model is a select statement. " * 40)


def _system(msgs):
    return "\n\n".join(m["content"] for m in msgs if m["role"] == "system")


class TestPrefixSurvivesTurnChanges:

    def test_the_prefix_marker_is_present(self):
        s = _system(get_socratic_tutor_prompt(CONTEXT, []))
        assert _PREFIX_END in s, "the per-turn boundary marker is gone"

    def test_concept_doc_is_inside_the_reusable_prefix(self):
        """The concept doc is the largest block; if it lands after the
        boundary it is re-prefilled every turn and the win is lost."""
        s = _system(get_socratic_tutor_prompt(CONTEXT, []))
        prefix = s.split(_PREFIX_END)[0]
        assert "A dbt model is a select statement." in prefix

    def test_prefix_identical_across_turns(self):
        """Turn 0 and turn 5 must share a byte-identical prefix."""
        a = _system(get_socratic_tutor_prompt(CONTEXT, []))
        b = _system(get_socratic_tutor_prompt(
            CONTEXT, [("q1", "a1"), ("q2", "a2")],
            learner_behaviour="THIS LEARNER RIGHT NOW: terse.",
            bloom_level=4))
        assert a.split(_PREFIX_END)[0] == b.split(_PREFIX_END)[0]

    def test_per_turn_blocks_land_after_the_boundary(self):
        s = _system(get_socratic_tutor_prompt(
            CONTEXT, [], learner_behaviour="THIS LEARNER RIGHT NOW: bluffing."))
        prefix, rest = s.split(_PREFIX_END)
        assert "THIS LEARNER RIGHT NOW" not in prefix
        assert "THIS LEARNER RIGHT NOW" in rest

    def test_typed_prompt_keeps_the_same_guarantee(self):
        """`get_typed_socratic_prompt` is what the FSM calls."""
        a = _system(get_typed_socratic_prompt("why", CONTEXT, []))
        b = _system(get_typed_socratic_prompt(
            "why", CONTEXT, [("q", "a")],
            concept_kind=("computer_science", "MECHANISM")))
        assert a.split(_PREFIX_END)[0] == b.split(_PREFIX_END)[0]

    def test_prefix_is_large_enough_to_be_worth_caching(self):
        """A tiny prefix would pass the equality tests while saving nothing."""
        s = _system(get_socratic_tutor_prompt(CONTEXT, []))
        prefix = s.split(_PREFIX_END)[0]
        assert len(prefix) > 3000, f"prefix only {len(prefix)} chars"
