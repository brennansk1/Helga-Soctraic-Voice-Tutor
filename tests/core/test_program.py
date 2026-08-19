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

    def test_electives_start_unchosen(self):
        """Unchosen slots are never built, so the registration mechanic is also
        the budget control."""
        p = plan_from_template("Nursing", "associate")
        assert any(not c["chosen"] for c in p["courses"] if c["slot"] == "elective")


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

    def test_an_unchosen_elective_late_in_a_course_prompts(self):
        p = plan_from_template("Nursing", "associate")
        for c in p["courses"]:
            c["completed"] = c["slot"] != "elective"
        st = scheduler_state(p, progress=0.75, seconds_since_turn=10_000)
        assert bs.decide(st)["action"] == "prompt_elective"

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
