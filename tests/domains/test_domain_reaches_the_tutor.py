"""The domain layer must reach the PROMPT a learner actually receives.

THE FAILURE THIS PREVENTS
-------------------------
This repository has now produced the same defect six times: a component is
built, tested in isolation, measured working — and the production path never
calls it. Concept kinds and mined code pairs were the sixth. Both were computed
correctly at build time and stored on the concept, and `fsm_logic` passed
neither to `get_typed_socratic_prompt`, so every CS course was taught
generically. Twenty-seven tutor turns "proved" the guidance worked, because the
harness called the prompt function directly — which is not the path a learner
takes.

Nothing failed. Nothing could fail: unused data is silent.

So these tests assert the WIRING, not the components. They check the concept's
build-time data survives into the argument list, and that a non-CS course is
left exactly as it was.
"""
import inspect
import os
import sys

# fsm_logic uses container-relative imports (`from fsrs_engine import ...`),
# so services/core has to be on the path, exactly as tests/core does it.
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "services/core"))

from services.core import fsm_logic  # noqa: E402


PAIR = {"kind": "ERROR_FIX", "first": "dbt0101: no viable alternative",
        "second": "select 1", "lang": "sql"}


class _Courses:
    def __init__(self, course):
        self._c = course

    def get_course(self, uid):
        return self._c


class _Storage:
    def __init__(self, course):
        self.courses = _Courses(course)


def _tutor(course, node):
    """An FSM with just enough state for _domain_teaching, no __init__ call."""
    t = fsm_logic.MnemosyneFSM.__new__(fsm_logic.MnemosyneFSM)
    t.storage = _Storage(course)
    t.active_course_uid = "c1"
    t.current_lesson_node = node
    return t


CS_COURSE = {"uid": "c1", "teaching_domain": "computer_science"}


def test_concept_kind_reaches_the_prompt_arguments():
    kind, block = _tutor(CS_COURSE, {"concept_kind": "MECHANISM"})._domain_teaching()
    assert kind == ("computer_science", "MECHANISM")


def test_a_mined_pair_becomes_an_instruction_block():
    kind, block = _tutor(CS_COURSE, {"teaching_pair": PAIR})._domain_teaching()
    assert block and "dbt0101" in block, "the pair must arrive with its material"
    assert "THIS TURN" in block, "and as an instruction, not a description"


def test_a_non_cs_course_is_untouched():
    """Most courses have no domain. They must be left exactly as before."""
    plain = {"uid": "c1"}
    assert _tutor(plain, {"concept_kind": "MECHANISM"})._domain_teaching() == (None, None)


def test_a_concept_with_no_domain_data_costs_nothing():
    assert _tutor(CS_COURSE, {"title": "x"})._domain_teaching() == (None, None)
    assert _tutor(CS_COURSE, {})._domain_teaching() == (None, None)
    assert _tutor(CS_COURSE, None)._domain_teaching() == (None, None)


def test_a_broken_storage_lookup_does_not_break_the_turn():
    """Guidance is an enhancement. Losing it must never cost the lesson."""
    class Boom:
        @property
        def courses(self):
            raise RuntimeError("storage down")

    t = fsm_logic.MnemosyneFSM.__new__(fsm_logic.MnemosyneFSM)
    t.storage = Boom()
    t.active_course_uid = "c1"
    t.current_lesson_node = {"concept_kind": "SYNTAX", "teaching_pair": PAIR}
    assert t._domain_teaching() == (None, None)


def test_the_prompt_call_site_actually_passes_them():
    """The assertion that would have caught this defect at the source.

    Reads fsm_logic's own text: the socratic prompt call must name both
    arguments. A future edit that drops them fails here rather than silently
    reverting every CS course to generic teaching.
    """
    src = inspect.getsource(fsm_logic)
    i = src.index("get_typed_socratic_prompt(", src.index("else:"))
    call = src[i:i + 4000]
    assert "concept_kind=" in call, "the prompt call dropped concept_kind"
    assert "figure_facts=" in call, "the prompt call dropped the mined pair"


def test_the_concept_node_carries_the_build_time_fields():
    """current_lesson_node is all the tutor sees; these must survive into it."""
    src = inspect.getsource(fsm_logic)
    i = src.index('"complexity_role": concept_details.get')
    block = src[i - 900:i + 500]
    assert 'concept_details.get("concept_kind")' in block
    assert 'concept_details.get("teaching_pair")' in block


# ---------------------------------------------------------------------------
# The code aid: instance SEVEN of the same defect.
#
# `code_examples.attach_to_course` mines one vetted, deduplicated example per
# code-shaped concept and stores it on the concept. Nothing in production read
# it: the aid loader parses a concept's MARKDOWN, and a domain example lives in
# structure.json, so every mined example was invisible to every learner.
#
# It now fills the `worked_example` slot, which the aid policy also reaches for
# when a learner is stuck — the moment prose has failed and showing code is the
# entire point of having mined it.
# ---------------------------------------------------------------------------

EXAMPLE = {"kind": "code", "lang": "sql", "code": "select * from users",
           "title": "A first model", "blanks": []}


def _aid_fsm(node):
    t = fsm_logic.MnemosyneFSM.__new__(fsm_logic.MnemosyneFSM)
    t._concept_aids = {}
    t.current_lesson_node = node
    return t


def test_the_mined_code_example_becomes_a_worked_example_aid():
    t = _aid_fsm({"uid": "con_1", "code_example": dict(EXAMPLE)})
    t._load_domain_code_aid()
    aid = t._concept_aids.get("worked_example")
    assert aid, "the mined example never reached the aid the learner sees"
    assert aid["kind"] == "code"
    assert aid["slot"] == "worked_example"
    assert aid.get("id"), "an aid needs an id for cooldown and repeat-tracking"


def test_an_authored_aid_outranks_a_mined_one():
    """A diagram written for this concept was a deliberate choice."""
    t = _aid_fsm({"uid": "con_1", "code_example": dict(EXAMPLE)})
    t._concept_aids["worked_example"] = {"kind": "diagram", "id": "authored"}
    t._load_domain_code_aid()
    assert t._concept_aids["worked_example"]["id"] == "authored"


def test_no_example_costs_nothing():
    for node in ({"uid": "c"}, {}, None, "not a dict"):
        t = _aid_fsm(node)
        t._load_domain_code_aid()          # must not raise
        assert t._concept_aids == {}


def test_a_malformed_example_is_dropped_not_raised():
    """Build-time data can be wrong. It must never cost the lesson."""
    t = _aid_fsm({"uid": "c", "code_example": {"kind": "not_a_real_kind"}})
    t._load_domain_code_aid()
    assert t._concept_aids == {}


def test_the_aid_loader_calls_it():
    """The wiring assertion — the reason this whole file exists."""
    src = inspect.getsource(fsm_logic.MnemosyneFSM._reset_aid_budget)
    assert "_load_domain_code_aid" in src, (
        "the aid loader stopped calling the domain code aid, which silently "
        "hides every mined example again")
