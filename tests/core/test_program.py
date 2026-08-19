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
