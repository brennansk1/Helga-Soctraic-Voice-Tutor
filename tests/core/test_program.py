"""Programs: an ordered set of courses with prerequisite edges.

A two-semester sequence is not a stretched course -- stretching one course to
twice the length is the spread-too-thin failure, while Linear Algebra II is a
different course with its own syllabus. Treating both as Programs means the
sequencing logic is written once.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core import build_scheduler as bs  # noqa: E402
from services.core.program import (  # noqa: E402
    ProgramError, TEMPLATES, next_course, plan_from_template, plan_sequence,
    scheduler_state, sequence_titles, validate)


class TestSequence(unittest.TestCase):
    def test_two_semester_sequence_is_two_courses_with_an_edge(self):
        p = plan_sequence("Linear Algebra", 2)
        assert [c["title"] for c in p["courses"]] == ["Linear Algebra I",
                                                      "Linear Algebra II"]
        assert p["courses"][1]["requires"] == ["Linear Algebra I"]
        assert p["courses"][0]["term"] == 1 and p["courses"][1]["term"] == 2

    def test_a_single_course_keeps_its_plain_name(self):
        assert sequence_titles("Linear Algebra", 1) == ["Linear Algebra"]

    def test_an_already_numbered_subject_is_not_double_numbered(self):
        assert sequence_titles("Linear Algebra I", 2) == ["Linear Algebra I",
                                                          "Linear Algebra II"]


class TestTemplates(unittest.TestCase):
    def test_degree_sizes_match_the_verified_figures(self):
        assert TEMPLATES["associate"]["courses"] == 20
        assert TEMPLATES["bachelors"]["courses"] == 40
        assert TEMPLATES["associate"]["terms"] == 4
        assert TEMPLATES["bachelors"]["terms"] == 8

    def test_slots_sum_to_the_course_count(self):
        for name, tpl in TEMPLATES.items():
            assert sum(tpl["slots"].values()) == tpl["courses"], name

    def test_a_sourceless_subject_produces_the_same_shape(self):
        """The D&D case. One subject has real syllabi to match and one does not,
        but neither changes the algorithm -- otherwise there is a custom-degree
        branch to write and maintain."""
        real = plan_from_template("Nursing", "associate")
        made_up = plan_from_template("Dungeons & Dragons", "associate")
        assert len(real["courses"]) == len(made_up["courses"]) == 20
        assert real["terms"] == made_up["terms"]

    def test_the_capstone_is_last(self):
        """Placing a capstone mid-programme is the kind of error nobody notices
        until a learner reaches it."""
        p = plan_from_template("Nursing", "bachelors")
        caps = [c for c in p["courses"] if c["slot"] == "capstone"]
        assert caps and all(c["term"] == p["terms"] for c in caps)

    def test_every_course_is_part_of_the_programme(self):
        """There is no pick-one-of-three. A programme is the full list of
        courses you complete; what the learner chooses is the ORDER, one
        course at a time, and prerequisites are what constrain it.

        The budget control that unchosen electives used to provide now comes
        from building one course at a time against the learner's actual next
        pick, not from leaving slots unfilled.
        """
        p = plan_from_template("Nursing", "associate")
        assert all(c["chosen"] for c in p["courses"])
        assert not any(c.get("completed") for c in p["courses"])


class TestValidation(unittest.TestCase):
    """An incoherent programme is invisible until a learner reaches a course
    they cannot follow, months in."""

    def test_a_missing_prerequisite_is_rejected(self):
        with self.assertRaises(ProgramError):
            validate([{"title": "B", "term": 1, "requires": ["A"]}])

    def test_a_prerequisite_in_the_same_term_is_rejected(self):
        with self.assertRaises(ProgramError):
            validate([{"title": "A", "term": 1, "requires": []},
                      {"title": "B", "term": 1, "requires": ["A"]}])

    def test_a_cycle_is_rejected(self):
        with self.assertRaises(ProgramError):
            validate([{"title": "A", "term": 1, "requires": ["B"]},
                      {"title": "B", "term": 2, "requires": ["A"]}])

    def test_duplicate_courses_are_rejected(self):
        """The same subject filling two slots under one name is padding."""
        with self.assertRaises(ProgramError):
            validate([{"title": "Ethics", "term": 1, "requires": []},
                      {"title": "ethics", "term": 2, "requires": []}])

    def test_a_valid_chain_passes(self):
        assert validate([{"title": "A", "term": 1, "requires": []},
                         {"title": "B", "term": 2, "requires": ["A"]},
                         {"title": "C", "term": 3, "requires": ["B"]}]) is True


class TestSchedulerIntegration(unittest.TestCase):
    """The programme is what finally lets the scheduler decide anything."""

    def test_an_idle_learner_with_an_unbuilt_next_course_triggers_a_build(self):
        p = plan_sequence("Linear Algebra", 2)
        p["courses"][0]["completed"] = True
        st = scheduler_state(p, progress=1.0, seconds_since_turn=10_000)
        assert bs.decide(st)["action"] == "start_build"

    def test_an_active_session_still_wins(self):
        p = plan_sequence("Linear Algebra", 2)
        st = scheduler_state(p, progress=1.0, seconds_since_turn=5)
        assert bs.decide(st)["action"] == "wait"

    def test_late_in_a_course_with_a_real_choice_it_asks_which_is_next(self):
        """A programme is a fixed list; the learner picks the ORDER, one
        course at a time. Late in the current course, with more than one
        course unlocked and none picked, the scheduler asks."""
        p = plan_from_template("Nursing", "associate")
        for c in p["courses"][:-3]:
            c["completed"] = True
        st = scheduler_state(p, progress=0.75, seconds_since_turn=10_000)
        assert st["available_count"] > 1
        assert bs.decide(st)["action"] == "prompt_next_course"

    def test_with_only_one_course_left_there_is_nothing_to_ask(self):
        """A choice of one is not a choice — prompting would be noise."""
        p = plan_from_template("Nursing", "associate")
        for c in p["courses"][:-1]:
            c["completed"] = True
        st = scheduler_state(p, progress=0.75, seconds_since_turn=10_000)
        assert st["available_count"] == 1
        assert bs.decide(st)["action"] != "prompt_next_course"

    def test_a_course_is_unavailable_until_its_prerequisites_are_done(self):
        from services.core.program import available_courses
        p = plan_from_template("Nursing", "associate")
        gated = next(c for c in p["courses"] if not c.get("requires"))
        other = next(c for c in p["courses"] if c is not gated)
        other["requires"] = [gated["title"]]
        assert other not in available_courses(p)
        gated["completed"] = True
        assert other in available_courses(p)

    def test_next_course_is_the_earliest_incomplete(self):
        p = plan_sequence("Calculus", 3)
        p["courses"][0]["completed"] = True
        assert next_course(p)["title"] == "Calculus II"

    def test_a_finished_programme_has_no_next_course(self):
        p = plan_sequence("Calculus", 2)
        for c in p["courses"]:
            c["completed"] = True
        assert next_course(p) is None


if __name__ == "__main__":
    unittest.main()


class TestSlotSubjectsAreProposed(unittest.TestCase):
    """A degree is the one place where naming the courses IS the design.

    `plan_from_template` accepted `slot_subjects` and NOTHING ever populated it,
    so an "Associate in Nursing" produced twenty courses called
    "Nursing: gen_ed 1" ... "Nursing: elective 3". Correct structure -- 20
    courses, 4 terms, a validated prerequisite graph -- wrapped around twenty
    empty names.
    """

    def _fake_llm(self, payload):
        return lambda **kw: payload

    def test_proposed_titles_replace_the_placeholders(self):
        from services.core.program import propose_slot_subjects
        payload = {"gen_ed": ["ENG 101"], "core": ["NUR 101"],
                   "elective": ["NUR 301"], "capstone": ["NUR 490"]}
        slots = propose_slot_subjects("Nursing", "associate",
                                      self._fake_llm(payload))
        p = plan_from_template("Nursing", "associate", slot_subjects=slots)
        titles = [c["title"] for c in p["courses"]]
        assert "ENG 101" in titles and "NUR 490" in titles

    def test_a_wrapped_list_is_unwrapped(self):
        """Shape drift again: the schema asks for an object and a list wrapping
        it comes back often enough that rejecting it has cost real builds."""
        from services.core.program import propose_slot_subjects
        payload = [{"gen_ed": ["ENG 101"], "core": ["NUR 101"],
                    "elective": ["X"], "capstone": ["Y"]}]
        slots = propose_slot_subjects("Nursing", "associate",
                                      self._fake_llm(payload))
        assert slots["gen_ed"] == ["ENG 101"]

    def test_duplicates_within_a_slot_are_dropped(self):
        """The same subject twice under one name is the padding validate()
        rejects, and it is cheaper to catch here."""
        from services.core.program import propose_slot_subjects
        payload = {"gen_ed": ["ENG 101", "eng 101", "BIO 101"], "core": ["N"],
                   "elective": ["E"], "capstone": ["C"]}
        slots = propose_slot_subjects("Nursing", "associate",
                                      self._fake_llm(payload))
        assert slots["gen_ed"] == ["ENG 101", "BIO 101"]

    def test_the_same_title_in_two_slots_does_not_abort_the_build(self):
        """The reproduction: "Statistics" is a natural answer under BOTH gen_ed
        and core, and the cross-slot duplicate reached `validate()`, which
        raises — a whole degree lost to a reasonable composition."""
        from services.core.program import propose_slot_subjects
        payload = {"gen_ed": ["Statistics", "Composition", "World History"],
                   "core": ["Statistics", "Pharmacology"],
                   "elective": ["Gerontology"], "capstone": ["Practicum"]}
        slots = propose_slot_subjects("Nursing", "associate",
                                      self._fake_llm(payload))
        seen = [t.lower() for titles in slots.values() for t in titles]
        assert len(seen) == len(set(seen)), f"duplicate across slots: {slots}"
        assert "Statistics" in slots["gen_ed"]
        assert "Statistics" not in slots["core"]
        # The point of catching it here: the plan builds instead of raising.
        p = plan_from_template("Nursing", "associate", slot_subjects=slots)
        assert len(p["courses"]) == 20

    def test_a_dropped_duplicate_leaves_a_placeholder_not_a_gap(self):
        """A slot short of titles is filled the way an unproposed slot is."""
        from services.core.program import propose_slot_subjects
        payload = {"gen_ed": ["Statistics"], "core": ["Statistics"],
                   "elective": ["E"], "capstone": ["C"]}
        slots = propose_slot_subjects("Nursing", "associate",
                                      self._fake_llm(payload))
        assert slots.get("core") is None or "Statistics" not in slots["core"]
        titles = [c["title"] for c in
                  plan_from_template("Nursing", "associate",
                                     slot_subjects=slots)["courses"]]
        assert "Statistics" in titles
        assert any(t.startswith("Nursing: core") for t in titles)

    def test_case_and_spacing_do_not_smuggle_a_duplicate_through(self):
        from services.core.program import propose_slot_subjects
        payload = {"gen_ed": ["Statistics"], "core": ["  statistics  ", "Anatomy"],
                   "elective": ["E"], "capstone": ["C"]}
        slots = propose_slot_subjects("Nursing", "associate",
                                      self._fake_llm(payload))
        assert [t.strip().lower() for t in slots["core"]] == ["anatomy"]

    def test_a_failed_proposal_degrades_to_placeholders(self):
        """A degree with placeholder names is poor; a crash is worse."""
        from services.core.program import propose_slot_subjects

        def boom(**kw):
            raise RuntimeError("model down")

        assert propose_slot_subjects("Nursing", "associate", boom) == {}
        p = plan_from_template("Nursing", "associate", slot_subjects={})
        assert len(p["courses"]) == 20

    def test_a_slot_is_never_overfilled(self):
        from services.core.program import propose_slot_subjects
        payload = {"gen_ed": [f"G{i}" for i in range(20)], "core": ["C"],
                   "elective": ["E"], "capstone": ["K"]}
        slots = propose_slot_subjects("Nursing", "associate",
                                      self._fake_llm(payload))
        assert len(slots["gen_ed"]) == TEMPLATES["associate"]["slots"]["gen_ed"]


class TestDegreeSourcingCascade(unittest.TestCase):
    """A published PROGRAMME is to a degree what a textbook is to a course.

    The course tier falls through four priorities before inventing anything. The
    degree tier jumped straight to "ask the model", which is the same mistake as
    generating a course skeleton without looking for a textbook first.
    """

    def test_a_transcribed_curriculum_is_preferred(self):
        from services.core.program import source_degree_slots
        r = source_degree_slots("Economics", "associate")
        assert r["source"] == "published curriculum"
        assert r["authoritative"] is True
        assert "Principles of Microeconomics" in r["slots"]["core"]

    def test_an_alias_finds_the_curriculum(self):
        from services.core.program import curated_degree
        assert curated_degree("econ", "associate") is not None

    def test_the_template_must_match(self):
        """An associate curriculum is not a bachelor's curriculum."""
        from services.core.program import curated_degree
        assert curated_degree("Economics", "bachelors") is None

    def test_an_unknown_subject_falls_through_and_says_so(self):
        from services.core.program import source_degree_slots
        r = source_degree_slots("Underwater Basket Weaving", "associate")
        assert r["source"] == "model-proposed"
        assert r["authoritative"] is False
        assert "still evidence-gated individually" in r["note"]


class TestDegreeGapFilling(unittest.TestCase):
    """A partial curriculum must be completed, not discarded.

    Published curricula differ in how many electives they name. Falling back to a
    fully-invented list because of a partial gap throws away the authoritative
    part; filling only the gap keeps it.
    """

    def _curated_with_gap(self):
        import json
        import services.core.program as P
        orig = P.curated_degree

        def patched(subject, template):
            d = orig(subject, template)
            if d:
                d = json.loads(json.dumps(d))
                d["slots"]["elective"] = ["Money and Banking"]
            return d
        return P, orig, patched

    def test_a_gap_is_filled_and_the_transcribed_courses_survive(self):
        from services.core.program import source_degree_slots
        P, orig, patched = self._curated_with_gap()
        P.curated_degree = patched
        try:
            r = source_degree_slots(
                "Economics", "associate",
                llm_json_fn=lambda **kw: {"elective": ["International Economics",
                                                       "Public Finance"]})
        finally:
            P.curated_degree = orig
        assert r["slots"]["elective"][0] == "Money and Banking", \
            "the transcribed course must come first and survive"
        assert len(r["slots"]["elective"]) == 3
        assert r["gaps"] == []
        assert r["authoritative"] is True, \
            "a filled gap does not make a real curriculum unauthoritative"

    def test_a_gap_remains_when_nothing_fills_it(self):
        from services.core.program import source_degree_slots
        P, orig, patched = self._curated_with_gap()
        P.curated_degree = patched
        try:
            r = source_degree_slots("Economics", "associate",
                                    llm_json_fn=lambda **kw: {})
        finally:
            P.curated_degree = orig
        assert "elective" in r["gaps"]

    def test_duplicates_are_not_introduced_by_the_fill(self):
        from services.core.program import source_degree_slots
        P, orig, patched = self._curated_with_gap()
        P.curated_degree = patched
        try:
            r = source_degree_slots(
                "Economics", "associate",
                llm_json_fn=lambda **kw: {"elective": ["money and banking",
                                                       "Public Finance"]})
        finally:
            P.curated_degree = orig
        lowered = [t.lower() for t in r["slots"]["elective"]]
        assert len(lowered) == len(set(lowered))


class TestPrerequisiteInference(unittest.TestCase):
    """A degree had right names and NO order: plan_from_template set
    `requires: []` for every course, so validate() passed trivially and
    "Medical-Surgical Nursing II" could sit in term 1 ahead of "Foundations".
    A programme that teaches II before I is not a programme.
    """

    def _courses(self, *titles, slot="core"):
        return [{"title": t, "slot": slot, "term": 1, "requires": [],
                 "built": False, "chosen": True} for t in titles]

    def test_catalogue_levels_become_edges(self):
        from services.core.program import infer_prerequisites
        cs = self._courses("NUR 101: Intro", "NUR 201: Med-Surg I",
                           "NUR 301: Community Health")
        infer_prerequisites(cs)
        assert cs[1]["requires"] == ["NUR 101: Intro"]
        assert cs[2]["requires"] == ["NUR 201: Med-Surg I"]

    def test_numbered_series_become_edges(self):
        from services.core.program import infer_prerequisites
        cs = self._courses("Calculus I", "Calculus II", "Calculus III")
        infer_prerequisites(cs)
        assert cs[1]["requires"] == ["Calculus I"]
        assert cs[2]["requires"] == ["Calculus II"]

    def test_only_the_immediately_preceding_level_is_required(self):
        """Requiring every earlier course makes the graph dense and the term
        assignment impossible, for no pedagogical gain."""
        from services.core.program import infer_prerequisites
        cs = self._courses("NUR 101: A", "NUR 201: B", "NUR 301: C")
        infer_prerequisites(cs)
        assert "NUR 101: A" not in cs[2]["requires"]

    def test_unrelated_subjects_are_not_linked(self):
        from services.core.program import infer_prerequisites
        cs = self._courses("NUR 101: Nursing", "ENG 101: Composition")
        infer_prerequisites(cs)
        assert all(not c["requires"] for c in cs)

    def test_a_proposed_edge_that_would_cycle_is_rejected(self):
        """A confident wrong answer must not make the programme unteachable."""
        from services.core.program import infer_prerequisites
        cs = self._courses("Calculus I", "Calculus II")
        infer_prerequisites(cs, propose_fn=lambda titles: [
            {"course": "Calculus I", "requires": "Calculus II"}])
        assert "Calculus II" not in cs[0]["requires"]


class TestTermAssignment(unittest.TestCase):
    def _chain(self, n, terms=4):
        from services.core.program import assign_terms, infer_prerequisites
        cs = [{"title": f"NUR {100 * (i + 1)}: C{i}", "slot": "core", "term": 1,
               "requires": [], "built": False, "chosen": True} for i in range(n)]
        infer_prerequisites(cs)
        assign_terms(cs, terms)
        return cs

    def test_every_prerequisite_lands_strictly_earlier(self):
        cs = self._chain(4)
        index = {c["title"]: c for c in cs}
        for c in cs:
            for r in c["requires"]:
                assert index[r]["term"] < c["term"], f"{c['title']} <- {r}"

    def test_a_chain_deeper_than_the_programme_is_shortened_not_clamped(self):
        """Clamping put a course in the same term as its prerequisite. The chain
        has to be SHORTENED until it fits, never clamped afterwards."""
        from services.core.program import validate
        cs = self._chain(6, terms=3)
        validate(cs)                      # raises if any prereq is not earlier
        assert max(c["term"] for c in cs) <= 3

    def test_the_capstone_is_last(self):
        from services.core.program import assign_terms
        cs = [{"title": "A", "slot": "core", "term": 1, "requires": []},
              {"title": "Capstone", "slot": "capstone", "term": 1, "requires": []}]
        assign_terms(cs, 4)
        assert cs[1]["term"] == 4

    def test_spilling_for_capacity_never_overtakes_a_dependent(self):
        """Pushing a course later without checking its dependents let a
        prerequisite overtake the course that needed it -- caught by validate()
        three separate times before the check went both ways."""
        from services.core.program import validate
        cs = self._chain(8, terms=4)
        validate(cs)


class TestPlanDegreeEntryPoint(unittest.TestCase):
    """The pieces existed and nothing joined them, so the whole degree tier was
    orphaned: real course names, a real prerequisite graph, a real term layout,
    and no way for a caller to ask for any of it."""

    def test_a_known_degree_uses_its_published_curriculum(self):
        from services.core.program import plan_degree
        p = plan_degree("Economics", "associate")
        assert p["curriculum_source"] == "published curriculum"
        assert p["authoritative"] is True
        assert len(p["courses"]) == TEMPLATES["associate"]["courses"]

    def test_an_unknown_degree_still_produces_a_valid_plan(self):
        """The D&D case: same shape, same validation, labelled differently."""
        from services.core.program import plan_degree
        p = plan_degree("Underwater Basket Weaving", "associate")
        assert p["authoritative"] is False
        assert len(p["courses"]) == TEMPLATES["associate"]["courses"]
        assert p["terms"] == TEMPLATES["associate"]["terms"]

    def test_the_plan_is_always_validated(self):
        """plan_degree calls validate(), so anything it returns is teachable --
        every prerequisite earlier, no cycles, no duplicates."""
        from services.core.program import plan_degree, validate
        for subject in ("Economics", "Basket Weaving"):
            p = plan_degree(subject, "associate")
            validate(p["courses"])          # raises if not

    def test_prerequisites_are_inferred_without_a_model(self):
        """Catalogue conventions are deterministic, so a plan built with no LLM
        still gets its levelling."""
        from services.core.program import plan_degree
        p = plan_degree("Economics", "associate", llm_json_fn=None)
        assert p["prerequisite_edges"] > 0

    def test_an_unknown_template_is_rejected(self):
        from services.core.program import ProgramError, plan_degree
        with self.assertRaises(ProgramError):
            plan_degree("Economics", "doctorate")

    def test_provenance_reaches_the_caller(self):
        """Whether a curriculum was transcribed or proposed is the single most
        important thing about a degree plan, so it must survive to the caller."""
        from services.core.program import plan_degree
        p = plan_degree("Economics", "associate")
        assert "reference" in p
        q = plan_degree("Basket Weaving", "associate")
        assert "note" in q and "still evidence-gated" in q["note"]


class TestCourseTitleHygiene(unittest.TestCase):
    """Invented catalogue codes and runaway numbering.

    Both were measured on a model-proposed Dungeon Mastering associate: an
    entire "DMT" department numbered 101-499, in a programme with no department
    at all.
    """

    def test_an_invented_code_is_stripped(self):
        from services.core.program import strip_catalogue_code as s
        self.assertEqual(s("DMT 101: Introduction to Tabletop Roleplaying Games"),
                         "Introduction to Tabletop Roleplaying Games")
        self.assertEqual(s("MATH-221 Discrete Structures"), "Discrete Structures")
        self.assertEqual(s("ENG 201. Technical Writing"), "Technical Writing")

    def test_a_real_sequence_marker_survives(self):
        """Calculus II is a genuine part number, not a catalogue code."""
        from services.core.program import strip_catalogue_code as s
        for t in ("Calculus II", "Linear Algebra I", "Principles of Microeconomics"):
            self.assertEqual(s(t), t)

    def test_a_number_inside_a_name_is_not_a_code(self):
        from services.core.program import strip_catalogue_code as s
        self.assertEqual(s("World History to 1500"), "World History to 1500")
        self.assertEqual(s("Physics 2 Lab"), "Physics 2 Lab")

    def test_a_title_that_is_only_a_code_is_kept_not_emptied(self):
        """Returning "" would silently drop the slot."""
        from services.core.program import strip_catalogue_code as s
        self.assertEqual(s("DMT 499"), "DMT 499")

    def test_a_sequence_is_capped_at_three(self):
        from services.core.program import cap_sequences
        kept, overflow = cap_sequences(
            ["DM I", "DM II", "DM III", "DM IV", "DM V"])
        self.assertEqual(kept, ["DM I", "DM II", "DM III"])
        self.assertEqual(overflow, ["DM IV", "DM V"])

    def test_overflow_is_returned_for_resplitting_not_discarded(self):
        """A fourth part means the subject divides by topic after all, so the
        material still belongs in the programme — under a name that says what
        it is. Dropping it would shrink the degree to fix a labelling defect."""
        from services.core.program import cap_sequences
        _, overflow = cap_sequences(["X I", "X II", "X III", "X IV"])
        self.assertEqual(overflow, ["X IV"], "the fourth part must survive")

    def test_distinct_titles_and_short_sequences_are_untouched(self):
        from services.core.program import cap_sequences
        titles = ["Adventure Writing", "Encounter Design",
                  "Calculus I", "Calculus II"]
        kept, overflow = cap_sequences(titles)
        self.assertEqual(kept, titles)
        self.assertEqual(overflow, [])

    def test_separate_sequences_count_separately(self):
        from services.core.program import cap_sequences
        kept, overflow = cap_sequences(
            ["A I", "A II", "A III", "B I", "B II", "B III"])
        self.assertEqual(len(kept), 6, "two full sequences are both legal")
        self.assertEqual(overflow, [])


class TestProgrammeCompletion(unittest.TestCase):
    """Completing a course is what moves a programme forward.

    `program_courses` shipped with no `completed` column at all, so
    available_courses() — which decides what a learner may start next by
    asking which prerequisites are done — could never advance, and the
    degree page's "N of M courses complete" could only ever read zero.
    """

    def _store(self):
        import tempfile
        from services.common.storage import StorageManager
        return StorageManager(data_dir=tempfile.mkdtemp())

    def test_completing_a_prerequisite_unlocks_what_required_it(self):
        from services.core.program import available_courses
        sm = self._store()
        plan = plan_from_template("Economics", "associate")
        gate, gated = plan["courses"][0], plan["courses"][1]
        gated["requires"] = [gate["title"]]
        sm.programs.create("prog_x", plan)

        before = [c["title"] for c in available_courses(sm.programs.get("prog_x"))]
        self.assertNotIn(gated["title"], before)
        self.assertIn(gate["title"], before)

        self.assertTrue(sm.programs.mark_completed("prog_x", gate["title"]))
        after = [c["title"] for c in available_courses(sm.programs.get("prog_x"))]
        self.assertIn(gated["title"], after,
                      "completing the prerequisite must unlock the course")
        self.assertNotIn(gate["title"], after,
                         "a completed course is no longer available to start")

    def test_completion_survives_a_round_trip(self):
        sm = self._store()
        plan = plan_from_template("Economics", "associate")
        sm.programs.create("prog_y", plan)
        title = plan["courses"][0]["title"]
        sm.programs.mark_completed("prog_y", title)
        got = sm.programs.get("prog_y")
        row = next(c for c in got["courses"] if c["title"] == title)
        self.assertTrue(row["completed"])
        self.assertTrue(row["completed_at"])
        self.assertEqual(sm.programs.list()[0]["completed"], 1)

    def test_marking_a_course_that_is_not_in_the_programme_reports_it(self):
        sm = self._store()
        sm.programs.create("prog_z", plan_from_template("Economics", "associate"))
        self.assertFalse(sm.programs.mark_completed("prog_z", "Not A Real Course"))


class TestOnlyTeachableCourses(unittest.TestCase):
    """A degree may not contain a course this tutor cannot deliver.

    Found in a real generated programme: "Natural Science with Laboratory"
    was offered as available-now. Helga teaches by conversation — there is no
    bench, no kiln, no ward, no ensemble, nobody to sign a timesheet — so that
    course cannot exist here. A degree built from undeliverable courses is the
    same failure as a course of stub concepts marked ready: the structure
    looks right and the content cannot follow.
    """

    UNTEACHABLE = [
        "Natural Science with Laboratory", "General Chemistry I with Lab",
        "Nursing Clinical II", "Studio Art: Ceramics", "Marching Band",
        "Student Teaching Seminar", "Internship in Public Policy",
        "Physics Practicum", "Field Experience in Education",
    ]

    # The reason this filter needs word boundaries. A bare "lab" substring
    # rejects Labor Economics; a bare "band" rejects Bandwidth. This repo has
    # already shipped that exact bug twice — "energy is lost as heat" graded as
    # a student saying "lost", and a concept teaching "insignificant
    # placeholders" flagged as a stub.
    TEACHABLE = [
        "Labor Economics", "Labour History of Britain", "Collaborative Writing",
        "Bandwidth and Signal Theory", "Elaborate Structures in Poetry",
        "Organic Chemistry", "Principles of Macroeconomics",
        "Introduction to Astronomy", "Urban Studies",
    ]

    def test_hands_on_courses_are_rejected(self):
        from services.core.program import teachable
        for t in self.UNTEACHABLE:
            self.assertFalse(teachable(t), f"{t!r} needs a room or equipment")

    def test_ordinary_courses_survive_the_filter(self):
        from services.core.program import teachable
        for t in self.TEACHABLE:
            self.assertTrue(teachable(t), f"{t!r} is teachable and must survive")

    def test_a_generated_programme_contains_nothing_unteachable(self):
        from services.core.program import teachable
        p = plan_from_template("Biology", "associate")
        bad = [c["title"] for c in p["courses"] if not teachable(c["title"])]
        self.assertEqual(bad, [], f"programme offers undeliverable courses: {bad}")

    def test_the_filter_reports_what_it_dropped(self):
        from services.core.program import drop_unteachable
        seen = []
        kept = drop_unteachable(
            ["Organic Chemistry", "Organic Chemistry Laboratory", "Labor Economics"],
            on_drop=seen.append)
        self.assertEqual(kept, ["Organic Chemistry", "Labor Economics"])
        self.assertEqual(seen, [["Organic Chemistry Laboratory"]])
