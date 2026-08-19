"""The taught-concepts ledger.

Regression cover for the failure the ledger exists to close: five entirely
distinct, entirely reasonable lesson titles that all open by explaining the same
thing. Every duplicate control that predates this — `_is_duplicate`,
`check_filler`, `check_uniformity` — compares TITLES and is blind to it.
"""

import os
import sqlite3
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core.taught_ledger import (  # noqa: E402
    check_redundancy, correction_hint, ensure_schema, extract_claims,
    format_context, jaccard, neighbours, record_concept, shingles)

DICE = """## Core Explanation
A d20 roll is uniform over 1 to 20.

## Key Facts
- A twenty-sided die produces a uniform distribution across all twenty faces.
- The expected value of a single d20 roll is 10.5.
- Advantage rolls two dice and keeps the higher result.

## Misconceptions
- **Belief**: Rolling with advantage adds five to the result.
  **Correction**: Advantage takes the maximum of two independent rolls, it does not add a modifier.
"""

# A different title, different lesson, same underlying teaching.
DICE_AGAIN = """## Core Explanation
Before designing encounters we must understand the dice.

## Key Facts
- A twenty-sided die produces a uniform distribution across all twenty faces.
- The expected value of a single d20 roll is 10.5.
- Encounter difficulty scales with the party level.
"""

NEW_MATERIAL = """## Key Facts
- Terrain modifiers alter movement speed during tactical combat.
- Cover grants a bonus to armour class against ranged attacks.
"""


class TestClaimExtraction(unittest.TestCase):
    def test_key_facts_are_claims(self):
        claims = extract_claims(DICE)
        self.assertTrue(any("uniform distribution" in c for c in claims))
        self.assertTrue(any("10.5" in c for c in claims))

    def test_a_correction_is_a_claim(self):
        """A correction asserts what IS true, so it belongs on the ledger."""
        self.assertTrue(any("does not add a modifier" in c
                            for c in extract_claims(DICE)))

    def test_the_false_belief_is_not_a_claim(self):
        """Recording the misconception would be actively wrong — a later concept
        would then be told not to contradict a falsehood."""
        self.assertFalse(any("adds five" in c for c in extract_claims(DICE)))

    def test_a_bullet_marker_does_not_eat_the_correction_label(self):
        """`lstrip("-*• ")` strips the leading ** of **Correction** and silently
        drops every correction. Measured on the first run of this module."""
        md = "## Misconceptions\n- **Correction**: Water boils at 100 C at sea level.\n"
        self.assertTrue(any("100 C" in c for c in extract_claims(md)))

    def test_core_explanation_is_the_fallback(self):
        md = ("## Core Explanation\nThe derivative measures an instantaneous "
              "rate of change. It is defined as a limit of difference quotients.\n")
        self.assertTrue(extract_claims(md), "a file with no Key Facts still counts")

    def test_claims_are_deduped_within_one_concept(self):
        md = ("## Key Facts\n- The mitochondrion is the powerhouse of the cell.\n"
              "- The mitochondrion is the powerhouse of the cell.\n")
        self.assertEqual(len(extract_claims(md)), 1)


class TestRedundancyGate(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        ensure_schema(self.conn)
        record_concept(self.conn, "c", "con_1", "Probability in Combat", DICE, 0)

    def test_a_differently_titled_repeat_is_caught(self):
        """The measured failure. Titles share nothing; claims share everything."""
        r = check_redundancy(self.conn, "c", "con_2", DICE_AGAIN, 1)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["reintroduced"]), 2)

    def test_genuinely_new_material_passes(self):
        r = check_redundancy(self.conn, "c", "con_3", NEW_MATERIAL, 1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["reintroduced"], [])

    def test_the_gate_is_a_share_not_a_count(self):
        """One repeated claim inside a concept that otherwise advances the
        subject is the spiral working, not a defect."""
        md = ("## Key Facts\n"
              "- The expected value of a single d20 roll is 10.5.\n"
              "- Legendary resistance lets a monster auto-succeed three saves.\n"
              "- Lair actions occur on initiative count twenty.\n"
              "- Bounded accuracy keeps modifiers small across all tiers.\n")
        r = check_redundancy(self.conn, "c", "con_4", md, 1)
        self.assertEqual(len(r["reintroduced"]), 1)
        self.assertTrue(r["ok"], "1 of 4 repeated is reinforcement, not padding")

    def test_a_concept_never_flags_against_itself(self):
        record_concept(self.conn, "c", "con_9", "Dup", DICE, 9)
        r = check_redundancy(self.conn, "c", "con_9", DICE, 9)
        self.assertNotIn("con_9", [d["concept_uid"] for d in r["near_duplicate_of"]])

    def test_only_prior_concepts_count(self):
        """A concept cannot be redundant with something taught AFTER it, which
        is what keeps the gate correct when hydration runs in parallel and
        completion order stops matching teaching order."""
        record_concept(self.conn, "c", "con_late", "Later", DICE_AGAIN, 50)
        r = check_redundancy(self.conn, "c", "con_early", DICE_AGAIN, 1)
        self.assertNotIn("con_late",
                         [x["already_in"] for x in r["reintroduced"]])

    def test_the_instrument_has_no_model_in_it(self):
        r = check_redundancy(self.conn, "c", "con_2", DICE_AGAIN, 1)
        self.assertIn("no model", r["instrument"])


class TestRetrieval(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        ensure_schema(self.conn)
        record_concept(self.conn, "c", "con_1", "Probability in Combat", DICE, 0)
        record_concept(self.conn, "c", "con_2", "Terrain and Cover", NEW_MATERIAL, 1)

    def test_neighbours_return_claims_not_bodies(self):
        """The whole point of a ledger over stuffing the course into the window
        is that what travels is small."""
        n = neighbours(self.conn, "c", "Advantage and Disadvantage", "dice", k=2)
        self.assertTrue(n)
        self.assertTrue(n[0]["claims"])
        self.assertLessEqual(len(n[0]["claims"]), 2)

    def test_only_earlier_concepts_are_offered(self):
        n = neighbours(self.conn, "c", "X", "", before_ordinal=1)
        self.assertEqual([x["concept_uid"] for x in n], ["con_1"])

    def test_an_empty_ledger_returns_nothing_rather_than_failing(self):
        self.assertEqual(neighbours(self.conn, "other_course", "X", ""), [])
        self.assertEqual(format_context([]), "")

    def test_the_context_block_says_what_to_do_instead(self):
        n = neighbours(self.conn, "c", "Advantage", "dice", k=1)
        ctx = format_context(n)
        self.assertIn("ALREADY TAUGHT", ctx)
        self.assertIn("do NOT explain them again", ctx)


class TestCorrectionHint(unittest.TestCase):
    def test_it_names_the_specific_offender(self):
        """Prompt-only instruction changed nothing 5/5 in this pipeline; a
        correction naming the exact offending item worked 5/5."""
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        record_concept(conn, "c", "con_1", "Probability in Combat", DICE, 0)
        hint = correction_hint(check_redundancy(conn, "c", "con_2", DICE_AGAIN, 1))
        self.assertIn("uniform distribution", hint)
        self.assertIn("already established it", hint)

    def test_a_clean_result_produces_no_hint(self):
        self.assertEqual(correction_hint({"ok": True}), "")
        self.assertEqual(correction_hint(None), "")


class TestShingles(unittest.TestCase):
    def test_identical_text_is_a_perfect_match(self):
        self.assertEqual(jaccard(shingles(DICE), shingles(DICE)), 1.0)

    def test_unrelated_text_barely_overlaps(self):
        self.assertLess(jaccard(shingles(DICE), shingles(NEW_MATERIAL)), 0.1)

    def test_short_text_does_not_crash(self):
        self.assertEqual(jaccard(shingles("a"), shingles("")), 0.0)


if __name__ == "__main__":
    unittest.main()


class TestRedundancyCorrectionRound(unittest.TestCase):
    """One regeneration naming the offender — the enforcement shape that works.

    A prompt instruction not to repeat changed nothing 5/5 in this pipeline;
    a correction naming the exact duplicated claim worked 5/5.
    """

    DICE = ("## Key Facts\n"
            "- A twenty-sided die is uniform across all twenty faces.\n"
            "- The expected value of a d20 is 10.5.\n")
    BETTER = ("## Key Facts\n"
              "- Encounter difficulty scales with party level and action economy.\n"
              "- Legendary resistance lets a monster auto-succeed three saves per day.\n"
              "- Lair actions fire on initiative twenty.\n")

    def _hydrator(self):
        import tempfile
        from services.common.storage import StorageManager
        from services.core.course_builder import ContentHydrator
        h = ContentHydrator(storage=StorageManager(
            tempfile.mkdtemp(prefix="corr_test_")), mastery=3)
        record_concept(h._ledger_conn(), "c1", "con_a",
                       "Probability in Combat", self.DICE, 0)
        return h

    def _run(self, h, draft, retry_value, uid="con_b"):
        from unittest.mock import patch
        from services.core.course_builder import ContentHydrator
        with patch.object(ContentHydrator, "_condense_and_structure_content",
                          return_value=retry_value):
            return h._correct_redundancy(draft, "c1", uid, "T", 1, "DM", "", "",
                                         {}, [], 0.0, "", 3, [], [], {}, "raw")

    def test_a_genuine_fix_is_accepted(self):
        h = self._hydrator()
        self.assertEqual(self._run(h, self.DICE, self.BETTER), self.BETTER)

    def test_a_retry_that_repeats_as_much_is_rejected(self):
        h = self._hydrator()
        self.assertEqual(self._run(h, self.DICE, self.DICE), self.DICE)

    def test_a_retry_that_passes_by_saying_less_is_rejected(self):
        """The cheapest way to stop repeating is to stop saying anything.
        Without a length floor the gate would reward exactly that."""
        h = self._hydrator()
        self.assertEqual(self._run(h, self.DICE, "## Key Facts\n- Dice.\n"),
                         self.DICE)

    def test_a_clean_concept_costs_no_llm_call(self):
        from unittest.mock import patch
        from services.core.course_builder import ContentHydrator
        h = self._hydrator()
        calls = []
        with patch.object(ContentHydrator, "_condense_and_structure_content",
                          side_effect=lambda *a, **k: calls.append(1) or ""):
            h._correct_redundancy(self.BETTER, "c1", "con_e", "Clean", 1, "DM",
                                  "", "", {}, [], 0.0, "", 3, [], [], {}, "raw")
        self.assertEqual(calls, [], "no repeat means no regeneration")

    def test_a_failed_retry_leaves_the_original(self):
        h = self._hydrator()
        self.assertEqual(self._run(h, self.DICE, "[Hydration failed]"), self.DICE)


class TestSourceRetention(unittest.TestCase):
    """Retained passages, and the supplementary share measured in CLAIMS.

    The research reviewing the supplementary policy caught the unit being wrong:
    one weak book can dominate a course's content while being a small minority
    of the source LIST, so a cap counted per source bounds nothing that matters.
    """

    MD = ("## Key Facts\n"
          "- A d20 is uniform across twenty faces.\n"
          "- The expected value is 10.5.\n")
    SUPP = ["Introduction to Sociology"]

    def _hydrator(self):
        import tempfile
        from services.common.storage import StorageManager
        from services.core.course_builder import ContentHydrator
        return ContentHydrator(storage=StorageManager(
            tempfile.mkdtemp(prefix="src_test_")), mastery=3)

    def test_claims_on_a_weak_source_alone_are_marked_supplementary(self):
        h = self._hydrator()
        h._retain_sources("c1", "con_a", self.MD,
                          [{"title": "Introduction to Sociology", "url": "u1",
                            "snippet": "t"}], supplementary_books=self.SUPP)
        self.assertEqual(h.supplementary_claim_share("c1")["share"], 1.0)

    def test_the_share_is_of_claims_not_of_sources(self):
        h = self._hydrator()
        h._retain_sources("c1", "con_a", self.MD,
                          [{"title": "Introduction to Sociology", "url": "u1",
                            "snippet": "t"}], supplementary_books=self.SUPP)
        h._retain_sources("c1", "con_b", self.MD,
                          [{"title": "Strang Linear Algebra", "url": "u2",
                            "snippet": "t"}], supplementary_books=self.SUPP)
        share = h.supplementary_claim_share("c1")
        self.assertEqual(share["claims"], 4)
        self.assertEqual(share["share"], 0.5)
        self.assertFalse(share["within_cap"], "50% must breach a 20% cap")

    def test_a_degraded_lookup_is_distinguishable_from_an_empty_one(self):
        """A retained row with no text is a source we fetched and got nothing
        from; a missing row is a source we never fetched."""
        h = self._hydrator()
        h._retain_sources("c1", "con_c", self.MD,
                          [{"title": "X", "url": "u3", "snippet": "",
                            "search_degraded": True}])
        row = h._ledger_conn().execute(
            "SELECT degraded, passage FROM sources WHERE concept_uid='con_c'"
        ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "")

    def test_no_sources_means_unknown_rather_than_zero(self):
        h = self._hydrator()
        self.assertIsNone(h.supplementary_claim_share("never_built"))

    def test_re_retaining_replaces_rather_than_duplicates(self):
        h = self._hydrator()
        for _ in range(3):
            h._retain_sources("c1", "con_a", self.MD,
                              [{"title": "B", "url": "u", "snippet": "t"}])
        n = h._ledger_conn().execute(
            "SELECT COUNT(*) FROM sources WHERE concept_uid='con_a'").fetchone()[0]
        self.assertEqual(n, 1, "a re-hydration must not accumulate rows")
