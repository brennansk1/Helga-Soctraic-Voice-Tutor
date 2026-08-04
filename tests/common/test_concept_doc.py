"""Tests for concept_doc — the reader between hydration and the tutor.

The bug this module replaced was not a crash. Hydration ran, the fact check
ran, the depth contract ran, every test passed, and the model that teaches from
the result never saw the explanation. So these tests assert on WHAT REACHES THE
MODEL, not on whether a function returns a string.
"""

import re
import unittest

from services.common.concept_doc import (
    split_sections, section, tutor_context, index_text, DEFAULT_BUDGET,
)


# A mastery-4 document with the exact sections the hydrator emits, in the order
# it emits them (static header, LLM body, advanced sections, appended Sources).
DOC = """# The Pythagorean Theorem

## Metadata
- **Bloom Target**: 4 (Analyze)
- **Depth**: 4
- **Path**: Euclidean Geometry > Triangles > Right Triangles > Metric Relations
- **Complexity**: core
- **Source**: research+llm

## Learning Objectives
- State the theorem and its converse precisely
- Apply it to compute an unknown side

## Prerequisites
Prior concepts: Similar Triangles, Square Roots

## Mastery Criteria
Grade 3 requires: computes a missing side AND names the right-angle assumption.

## Core Explanation
In a right triangle the square on the hypotenuse equals the sum of the squares
on the legs: a2 + b2 = c2. The relation is metric and is equivalent to the
parallel postulate. The converse holds, which makes it a perpendicularity test.

## Key Facts
- Holds only in a Euclidean metric.
- The converse is true.
- (3,4,5) is the smallest Pythagorean triple.

## Real-World Examples
Step 1: run 3 m, rise 4 m. Step 2: c2 = 9 + 16 = 25. Step 3: c = 5 m.

## Misconceptions
- **Belief**: It applies to any triangle.
  **Correction**: It needs a right angle; otherwise use the law of cosines.

## Edge Cases & Limitations
- Fails on a sphere, where cos c = cos a cos b.

## Socratic Hooks
- Bloom 1-2: If the legs are 6 and 8, what is the hypotenuse?
- Bloom 3-4: A surveyor measures 3, 4 and 5.1 m. What follows?
- Bloom 5-6: What does its equivalence to the parallel postulate imply?

## Analogies
- **Simple**: Tiles from the two small squares repave the big one.
- **Technical**: It is the Pythagorean identity for orthogonal vectors.

## Governing Result
Euclid I.47. In a right-angled triangle the square on the side subtending the
right angle equals the sum of the squares on the sides containing it.

## Derivation
Drop the altitude to the hypotenuse, splitting c into p and q. The two
sub-triangles are similar to the whole, so a2 = cp and b2 = cq. Adding gives
a2 + b2 = c(p + q) = c2.

## Exercise
A 13 m ladder rests against a wall with its foot 5 m out. The foot is pulled
2 m further. By how much does the top slide down, and why is it not 2 m?

## Sources
- [Euclid's Elements](https://example.org/euclid) — reference (Tier 1)

*Source confidence: 0.71*
"""


def heads(text):
    return re.findall(r"^## (.+)$", text, re.M)


class TestSplitSections(unittest.TestCase):
    def test_parses_every_section(self):
        preamble, sections = split_sections(DOC)
        self.assertEqual(preamble, "# The Pythagorean Theorem")
        self.assertIn("Core Explanation", sections)
        self.assertIn("Derivation", sections)
        self.assertIn("Sources", sections)

    def test_empty_and_headerless_input(self):
        self.assertEqual(split_sections("")[1], {})
        pre, sections = split_sections("just prose, no headings")
        self.assertEqual(pre, "just prose, no headings")
        self.assertEqual(sections, {})

    def test_duplicate_heading_is_joined_not_overwritten(self):
        """_validate_markdown_structure appends a stub when a heading looks
        missing. If the model emitted it late there are two, and keeping only
        the last one keeps only the stub."""
        md = "## Key Facts\n- real fact\n\n## Key Facts\n- injected stub\n"
        _pre, sections = split_sections(md)
        self.assertIn("real fact", sections["Key Facts"])
        self.assertIn("injected stub", sections["Key Facts"])

    def test_legacy_names_alias_onto_current_ones(self):
        """A course built by an earlier version says 'Core Definition'. Without
        the alias it reads as a document with no explanation at all."""
        md = "## Core Definition\nA square is a rectangle.\n"
        self.assertEqual(section(md, "Core Explanation"), "A square is a rectangle.")

    def test_section_lookup_is_alias_aware_both_ways(self):
        md = "## Advanced Notes\nBreaks down at relativistic speeds.\n"
        self.assertIn("relativistic", section(md, "Edge Cases & Limitations"))
        self.assertIn("relativistic", section(md, "Advanced Notes"))

    def test_missing_section_returns_empty_string(self):
        self.assertEqual(section(DOC, "Nonexistent Section"), "")


class TestLectureContext(unittest.TestCase):
    """The regression that mattered: the lecturer could not see the explanation."""

    def setUp(self):
        self.ctx = tutor_context(DOC, "lecture")

    def test_core_explanation_reaches_the_lecturer(self):
        self.assertIn("Core Explanation", heads(self.ctx))
        self.assertIn("a2 + b2 = c2", self.ctx)

    def test_key_facts_reach_the_lecturer(self):
        self.assertIn("Key Facts", heads(self.ctx))

    def test_worked_example_reaches_the_lecturer(self):
        """A lecture is where a worked example belongs — it is not a spoiler
        when the model's job is to demonstrate."""
        self.assertIn("Real-World Examples", heads(self.ctx))

    def test_boilerplate_is_not_spent_on_budget(self):
        """Metadata's Path/Depth/Complexity lines taught the model nothing and
        occupied the front of every truncated context."""
        self.assertNotIn("Metadata", heads(self.ctx))
        self.assertNotIn("**Path**", self.ctx)
        self.assertNotIn("Sources", heads(self.ctx))

    def test_stays_within_budget(self):
        self.assertLessEqual(len(self.ctx), DEFAULT_BUDGET["lecture"])

    def test_beats_the_old_head_cut_on_substance(self):
        """The old path deleted the substantive sections, then took the first
        2000 characters of what was left. Compare like for like."""
        old = DOC
        for name in ("Core Explanation", "Key Facts", "Real-World Examples"):
            old = re.sub(rf"## {re.escape(name)}\s*\n.*?(?=\n## |\Z)", "",
                         old, flags=re.DOTALL)
        old = old.strip()[:2000]
        self.assertNotIn("a2 + b2 = c2", old)      # the old context: no theorem
        self.assertIn("a2 + b2 = c2", self.ctx)    # the new one: the theorem


class TestSocraticContext(unittest.TestCase):
    def setUp(self):
        self.ctx = tutor_context(DOC, "socratic")

    def test_worked_example_is_withheld(self):
        """The one section the original delete list was right about: a
        step-by-step solution is trivially easy to hand to the student."""
        self.assertNotIn("Real-World Examples", heads(self.ctx))
        self.assertNotIn("Step 3: c = 5 m", self.ctx)

    def test_ground_truth_is_present_so_the_tutor_cannot_teach_an_error(self):
        self.assertIn("Key Facts", heads(self.ctx))
        self.assertIn("Core Explanation", heads(self.ctx))

    def test_hooks_and_mastery_criteria_survive(self):
        self.assertIn("Socratic Hooks", heads(self.ctx))
        self.assertIn("Mastery Criteria", heads(self.ctx))

    def test_headers_stay_verbatim_for_the_prompt_regexes(self):
        """prompts.get_socratic_tutor_prompt greps for these exact strings; a
        renamed header silently drops the opening question."""
        self.assertRegex(self.ctx, r"## Socratic Hooks?\n")
        self.assertRegex(self.ctx, r"## Edge Cases & Limitations\n")

    def test_stays_within_budget(self):
        self.assertLessEqual(len(self.ctx), DEFAULT_BUDGET["socratic"])


class TestPacking(unittest.TestCase):
    def test_advanced_sections_are_not_the_first_casualties(self):
        """Governing Result / Derivation / Exercise are appended LAST and are
        the only thing mastery 4 and 5 buy. A head cut removed exactly them, so
        a more expensive course delivered less to the prompt."""
        ctx = tutor_context(DOC, "socratic")
        self.assertIn("Governing Result", heads(ctx))

    def test_a_tight_budget_keeps_priority_one_not_document_order(self):
        ctx = tutor_context(DOC, "lecture", budget=700)
        self.assertIn("Core Explanation", heads(ctx))
        self.assertLessEqual(len(ctx), 700)

    def test_an_oversized_section_cannot_starve_the_rest(self):
        md = DOC.replace("## Key Facts\n", "## Key Facts\n" + "- filler\n" * 400)
        ctx = tutor_context(md, "lecture")
        self.assertIn("Core Explanation", heads(ctx))
        self.assertIn("Misconceptions", heads(ctx))

    def test_trim_lands_on_a_line_boundary(self):
        """A section cut mid-sentence reads to the model as an error it should
        continue."""
        md = "## Core Explanation\n" + "\n".join(f"line {i}" for i in range(400))
        ctx = tutor_context(md, "lecture", budget=400)
        body = ctx.split("\n", 1)[1]
        self.assertTrue(body.rstrip().endswith("…"))
        self.assertNotRegex(body, r"lin$|li$|l$")

    def test_a_prepended_caution_survives_the_defensive_repack(self):
        """The FSM prepends a GROUNDING caution above the first heading, and
        get_micro_lecture_prompt packs again on the way in. Dropping the
        preamble would delete that caution on exactly the turns that need it —
        and silently, since the prompt would still look complete."""
        note = "GROUNDING: thin sourcing, do not assert specific figures."
        ctx = tutor_context(f"{note}\n\n{DOC}", "lecture")
        self.assertTrue(ctx.startswith(note))
        self.assertIn(note, tutor_context(ctx, "lecture"))

    def test_the_title_line_is_kept(self):
        self.assertIn("# The Pythagorean Theorem", tutor_context(DOC, "lecture"))

    def test_a_runaway_preamble_cannot_spend_the_whole_budget(self):
        md = ("junk " * 500) + "\n\n" + DOC
        ctx = tutor_context(md, "lecture")
        self.assertIn("Core Explanation", heads(ctx))
        self.assertLessEqual(len(ctx), DEFAULT_BUDGET["lecture"])

    def test_a_tight_budget_is_not_spent_entirely_on_preamble(self):
        md = ("junk " * 500) + "\n\n" + DOC
        ctx = tutor_context(md, "lecture", budget=600)
        self.assertLessEqual(len(ctx), 600)
        self.assertIn("Core Explanation", heads(ctx))

    def test_the_budget_holds_at_every_boundary(self):
        """`budget` is a hard ceiling, and the squeeze path computes the room
        left before `_trim` appends its two-character ellipsis. A single
        fixture only ever lands on a few boundaries, so sweep them.

        The second document forces the squeeze — one oversized section followed
        by another that has to be trimmed into whatever is left — which the
        realistic DOC never triggers.
        """
        squeezer = ("## Core Explanation\n" + "aaaa bbbb\n" * 40
                    + "\n## Key Facts\n" + "- fact line here\n" * 60)
        for doc in (DOC, squeezer):
            for mode in ("lecture", "socratic"):
                for budget in range(200, 4000, 17):
                    ctx = tutor_context(doc, mode, budget=budget)
                    self.assertLessEqual(len(ctx), budget, f"{mode} @ {budget}")

    def test_is_idempotent(self):
        """get_micro_lecture_prompt packs defensively, and the FSM packs before
        calling it. Packing twice must not erode the context."""
        once = tutor_context(DOC, "lecture")
        twice = tutor_context(once, "lecture")
        self.assertEqual(once, twice)

    def test_unknown_mode_passes_content_through_unchanged(self):
        self.assertEqual(tutor_context(DOC, "grading"), DOC)

    def test_empty_input_does_not_raise(self):
        self.assertEqual(tutor_context("", "lecture"), "")
        self.assertEqual(tutor_context(None, "socratic"), "")

    def test_unstructured_content_is_passed_through_within_budget(self):
        """A hydration stub or a hand-written file has no headings; returning
        nothing would leave the tutor with no context at all."""
        raw = "plain text with no headings at all. " * 200
        ctx = tutor_context(raw, "lecture", budget=500)
        self.assertTrue(ctx.startswith("plain text"))
        self.assertLessEqual(len(ctx), 500)


class TestIndexText(unittest.TestCase):
    def test_embedding_input_contains_the_explanation(self):
        """`title + body[:512]` reached Metadata, Learning Objectives and half
        of Prerequisites — not one word of the explanation."""
        text = index_text(DOC, "The Pythagorean Theorem")
        self.assertIn("a2 + b2 = c2", text)

    def test_embedding_input_excludes_the_path_boilerplate(self):
        """Every concept in a course shares its `**Path**:` line, so including
        it collapsed the whole course toward one vector."""
        text = index_text(DOC, "The Pythagorean Theorem")
        self.assertNotIn("**Path**", text)
        self.assertNotIn("Bloom Target", text)

    def test_title_is_included(self):
        self.assertTrue(index_text(DOC, "Pythagoras").startswith("Pythagoras"))

    def test_respects_the_limit(self):
        self.assertLessEqual(len(index_text(DOC, "T", limit=200)), 200)

    def test_falls_back_to_raw_body_when_nothing_parses(self):
        """A stub concept must still be findable by something."""
        text = index_text("no headings, just a sentence about mitosis", "Mitosis")
        self.assertIn("mitosis", text.lower())


if __name__ == "__main__":
    unittest.main()
