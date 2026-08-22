"""The tutor must be held to the figure it drew — in the PRODUCT, not just the bench.

THE FAILURE
-----------
Measured 2026-08-21, mathematics, `misconception_holder`, partial derivatives.
The tutor drew a surface labelling Peak (0,0) z=10 and Point A (2,0) z=6, then
argued in prose that moving toward A increased z. A learner asked to reason
from a figure the tutor itself contradicts learns the opposite of the concept.

`services/common/figure_facts.py` was written for exactly this — and only ever
ran inside `tools/helgabench.py`. It recovered aids by parsing JSON fences out
of transcript text; the product stores specs in `AidStore` and puts only a slim
descriptor in the transcript, so the parse found nothing and the product was
never protected. The benchmark measured a safeguard learners did not have.

This is instance 8 of "the component works, the path never fires".
"""
import os
import sys

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "services/core"))

from services.common.figure_facts import facts_from, facts_from_aids  # noqa: E402
from services.core import fsm_logic                                   # noqa: E402

SURFACE = {
    "id": "aid1", "kind": "chart", "title": "Surface",
    "points": [{"label": "Peak", "x": 0, "y": 0, "z": 10},
               {"label": "Point A", "x": 2, "y": 0, "z": 6}],
}


class _Store:
    def __init__(self, items):
        self._i = {a["id"]: a for a in items}

    def get(self, key):
        return self._i.get(key)


def _fsm(ids, store):
    t = fsm_logic.MnemosyneFSM.__new__(fsm_logic.MnemosyneFSM)
    t._aid_ids_this_concept = ids
    t.aid_store = store
    return t


# --------------------------------------------------------------- the module

def test_facts_are_built_from_specs_directly():
    out = facts_from_aids([SURFACE])
    assert "Peak at x=0" in out and "z=10" in out
    assert "must agree" in out, "the block has to state the constraint"


def test_a_nested_spec_is_read_too():
    out = facts_from_aids([{"id": "a", "title": "S", "spec": SURFACE}])
    assert "Peak" in out


def test_points_without_coordinates_are_not_facts():
    assert facts_from_aids([{"kind": "chart",
                             "points": [{"label": "A"}, {"label": "B"}]}]) == ""


def test_junk_never_raises():
    for bad in (None, [], [None], ["x"], [{}], [{"points": "nope"}]):
        assert facts_from_aids(bad) == ""


def test_the_benchmark_entry_point_still_works():
    """facts_from was refactored onto the shared core; it must not regress."""
    import json
    t = [{"role": "tutor", "text": "```aid\n" + json.dumps(SURFACE) + "\n```"}]
    assert "Peak" in facts_from(t)


def test_a_student_described_figure_is_not_a_tutor_commitment():
    import json
    t = [{"role": "student", "text": "```aid\n" + json.dumps(SURFACE) + "\n```"}]
    assert facts_from(t) == ""


# ---------------------------------------------------------------- the wiring

def test_the_fsm_reads_the_aids_it_actually_showed():
    note = _fsm({"aid1"}, _Store([SURFACE]))._figure_facts_note()
    assert "Peak at x=0" in note, (
        "the product still cannot see the figure it drew")


def test_an_evicted_aid_is_normal_not_an_error():
    """AidStore is bounded; a miss must cost the facts, never the turn."""
    assert _fsm({"aid1"}, _Store([]))._figure_facts_note() == ""


def test_no_aids_shown_means_no_note():
    assert _fsm(set(), _Store([SURFACE]))._figure_facts_note() == ""


def test_a_broken_store_does_not_break_the_turn():
    class Boom:
        def get(self, key):
            raise RuntimeError("store down")

    assert _fsm({"aid1"}, Boom())._figure_facts_note() == ""


def test_a_bare_fsm_without_init_is_safe():
    """Recovery paths build FSMs via __new__; getattr must cover them."""
    t = fsm_logic.MnemosyneFSM.__new__(fsm_logic.MnemosyneFSM)
    assert t._figure_facts_note() == ""


def test_the_pair_and_the_facts_compose_rather_than_replace():
    """Both ride `figure_facts`; one must not silently drop the other."""
    import inspect
    src = inspect.getsource(fsm_logic)
    i = src.index("_fig_facts = self._figure_facts_note()")
    block = src[i:i + 400]
    assert "_domain_pair" in block and "_fig_facts" in block, (
        "the mined pair and the figure facts must both reach the prompt")
    call = src[src.index("get_typed_socratic_prompt(", src.index("else:")):][:4000]
    assert "figure_facts=_extra" in call


def test_a_reused_prebuilt_aid_is_found_by_its_SLOT():
    """The reuse path records `decision.slot`, not an aid id.

    A slot is not a store key, so an id-only lookup misses exactly the aids
    that were drawn and CHECKED at course-creation time — the ones most worth
    holding the tutor to.
    """
    t = fsm_logic.MnemosyneFSM.__new__(fsm_logic.MnemosyneFSM)
    t._aid_ids_this_concept = {"opening"}          # a slot, not an id
    t.aid_store = _Store([])                        # nothing under that key
    t._concept_aids = {"opening": SURFACE}
    assert "Peak at x=0" in t._figure_facts_note()


def test_ids_and_slots_mix_without_loss():
    other = {"id": "aid2", "kind": "chart",
             "points": [{"label": "B", "x": 9, "y": 9}]}
    t = fsm_logic.MnemosyneFSM.__new__(fsm_logic.MnemosyneFSM)
    t._aid_ids_this_concept = {"aid2", "opening"}
    t.aid_store = _Store([other])
    t._concept_aids = {"opening": SURFACE}
    note = t._figure_facts_note()
    assert "Peak" in note and "B at x=9" in note
