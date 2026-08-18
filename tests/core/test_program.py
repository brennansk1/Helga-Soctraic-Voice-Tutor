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
