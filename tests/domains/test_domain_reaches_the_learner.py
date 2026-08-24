"""The chain from build to tutor, tested at every link.

`test_domain_reaches_the_tutor.py` asserts a domain writes the field the FSM
reads. That was necessary and not sufficient: the field was written, the FSM
copied it onto `current_lesson_node`, and `get_concept_details` — the function
in between — never returned it. Both ends were correct and the middle dropped
it, so `_domain_teaching()` returned (None, None) for every concept of every
domain while every unit test passed.

The reason it went unseen is written in `_domain_teaching`'s own docstring:
"the kinds were measured working only because the test harness called the
prompt function directly — which is not the path a learner takes." That was
true of the code underneath it too.

These tests take the learner's path.
"""
import os
import sys

import pytest

# `fsm_logic` imports its siblings flat, as the container lays them out.
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "services/core"))
sys.path.append(os.path.join(os.getcwd(), "services/common"))


CARRIED = ("concept_kind", "teaching_pair", "code_example")


def _concept(**extra):
    c = {"uid": "con_abc12345", "title": "Ohm's Law",
         "bloom_level": 2, "learning_objectives": ["state it"],
         "complexity_role": "", "depth_level": 1}
    c.update(extra)
    return c


class _Courses:
    def __init__(self, concept):
        self._c = concept

    def get_concept_by_uid(self, course_uid, uid):
        return self._c

    def get_concept_content(self, course_uid, uid):
        return "# Ohm's Law\n\nV = IR."

    def find_concept_across_courses(self, uid):
        return dict(self._c, course_uid="course_1")

    def get_course(self, uid):
        return {"uid": "course_1", "teaching_domain": "science"}


class _Storage:
    def __init__(self, concept):
        self.courses = _Courses(concept)


def _fsm(concept):
    """An FSM with storage stubbed, without running __init__."""
    from services.core.fsm_logic import MnemosyneFSM
    f = MnemosyneFSM.__new__(MnemosyneFSM)
    f.storage = _Storage(concept)
    f.active_course_uid = "course_1"
    f.current_lesson_node = None
    # The HD-consent gate runs before the return and would short-circuit it.
    f._hd_consent_blocked = lambda uid: False
    return f


def test_get_concept_details_carries_the_domain_fields():
    """The link that was broken."""
    fsm = _fsm(_concept(concept_kind="LAW",
                        teaching_pair={"kind": "PREDICT_OBSERVE",
                                       "first": "a", "second": "b"},
                        code_example={"code": "x"}))
    details = fsm.get_concept_details("con_abc12345")
    for field in CARRIED:
        assert field in details, f"{field} dropped by get_concept_details"
    assert details["concept_kind"] == "LAW"
    assert details["teaching_pair"]["kind"] == "PREDICT_OBSERVE"


def test_cross_course_fallback_carries_them_too():
    """A concept found by the fallback is taught by the same tutor."""
    fsm = _fsm(_concept(concept_kind="MECHANISM"))
    fsm.storage.courses.get_concept_by_uid = lambda *a: None
    details = fsm.get_concept_details("con_abc12345")
    assert details["concept_kind"] == "MECHANISM"


def test_absent_fields_are_none_not_missing():
    """A course built before the domain layer must not KeyError."""
    fsm = _fsm(_concept())
    details = fsm.get_concept_details("con_abc12345")
    for field in CARRIED:
        assert details.get(field) is None


def test_domain_teaching_sees_the_kind_from_a_real_node():
    """End of the chain: `_domain_teaching` reads what the build wrote."""
    fsm = _fsm(_concept(concept_kind="LAW"))
    details = fsm.get_concept_details("con_abc12345")
    # Exactly the copy NAVIGATE_TO_TOPIC performs.
    fsm.current_lesson_node = {
        "uid": details["uid"], "title": details["title"],
        "concept_kind": details.get("concept_kind"),
        "teaching_pair": details.get("teaching_pair"),
        "code_example": details.get("code_example"),
    }
    kind, pair = fsm._domain_teaching()
    assert kind is not None, "_domain_teaching saw nothing"
    assert kind[1] == "LAW" if isinstance(kind, tuple) else kind == "LAW"


def test_the_builder_writes_what_the_reader_reads():
    """Producer and consumer agree on the field NAME.

    Both ends being individually correct is what allowed the break: the test
    that a domain writes `teaching_pair` passed, and the test that the FSM
    reads `teaching_pair` passed, and nothing checked the function between.
    """
    from services.core.course_builder import SkeletonBuilder
    builder = SkeletonBuilder.__new__(SkeletonBuilder)
    builder.status_callback = None
    course = {"title": "Introduction to Physics", "modules": [{"units": [
        {"lessons": [{"concepts": [{"title": "Ohm's Law"},
                                   {"title": "The units of charge"}]}]}]}]}
    builder._classify_concepts_by_domain(course, "Physics")
    concepts = course["modules"][0]["units"][0]["lessons"][0]["concepts"]
    assert all("concept_kind" in c for c in concepts)
    # And that name is one `get_concept_details` forwards.
    assert "concept_kind" in CARRIED


def test_typed_topic_path_classifies_without_a_book():
    """The gap this closes: course_builder had no registry reference at all,
    so every domain ran only for uploaded books."""
    from services.core.course_builder import SkeletonBuilder
    builder = SkeletonBuilder.__new__(SkeletonBuilder)
    builder.status_callback = None
    course = {"title": "Introduction to Physics", "modules": [{"units": [
        {"lessons": [{"concepts": [
            {"title": "Ohm's Law"},
            {"title": "Why does the current continue to flow"},
            {"title": "Observing the colour change"}]}]}]}]}
    builder._classify_concepts_by_domain(course, "Physics")
    kinds = [c.get("concept_kind") for c in
             course["modules"][0]["units"][0]["lessons"][0]["concepts"]]
    assert kinds == ["LAW", "MECHANISM", "OBSERVATION"]
    assert course.get("teaching_domain") == "science"


def test_classification_never_raises_on_a_broken_course():
    from services.core.course_builder import SkeletonBuilder
    builder = SkeletonBuilder.__new__(SkeletonBuilder)
    builder.status_callback = None
    for bad in ({}, {"modules": None}, {"modules": [{"units": None}]},
                {"modules": [{"units": [{"lessons": [{"concepts": [{}]}]}]}]}):
        builder._classify_concepts_by_domain(bad, "Physics")


def test_a_maths_course_does_not_get_science_kinds():
    """Routing must hold on this path too."""
    from services.core.course_builder import SkeletonBuilder
    builder = SkeletonBuilder.__new__(SkeletonBuilder)
    builder.status_callback = None
    course = {"title": "Calculus", "modules": [{"units": [{"lessons": [
        {"concepts": [{"title": "The definition of a limit"}]}]}]}]}
    builder._classify_concepts_by_domain(course, "Calculus")
    assert course.get("teaching_domain") == "mathematics"
