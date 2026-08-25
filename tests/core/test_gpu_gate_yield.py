"""Background must yield bandwidth, not just slots, to a live learner.

The gate used to argue that reserving a slot was enough: an interactive turn
always finds a free slot, so it is never behind the build. Measured during a
live build on 2026-08-25, with the slot free and dispatch immediate, two
4-token generations took 145 seconds. Slots were never the constraint — the
memory bus was, and the gate cannot schedule that.

So the only lever it has is COUNT: fewer concurrent background generations
while somebody is actually in a session.
"""
import time

import pytest

from services.core.gpu_gate import GpuGate, BACKGROUND, INTERACTIVE, LLMContext


def _ctx(klass, student="s1"):
    return LLMContext(klass=klass, student_id=student)


@pytest.fixture
def gate():
    return GpuGate(num_parallel=4, admit_timeout_s=0.2)


def test_build_gets_full_width_when_nobody_is_learning(gate):
    assert gate._effective_bg_slots() == 3, (
        "an idle machine should build at full width")


def test_build_narrows_to_one_while_a_learner_is_active(gate):
    gate._grant_locked(INTERACTIVE)
    assert gate._effective_bg_slots() == 1, (
        "a live session must not compete with three build generations")


def test_learner_stays_active_through_reading_time(gate):
    """The gap between turns is when the learner reads — not when they left."""
    gate._grant_locked(INTERACTIVE)
    gate._inflight = 0
    gate._bg_inflight = 0
    assert gate._is_learner_active(), "throttling ended during reading time"


def test_build_recovers_full_width_after_the_learner_leaves(gate):
    gate._grant_locked(INTERACTIVE)
    gate._inflight = 0
    gate._last_interactive_at = time.time() - (gate.interactive_window_s + 1)
    assert not gate._is_learner_active()
    assert gate._effective_bg_slots() == 3, (
        "a build must not stay throttled all night by someone who left")


def test_background_is_refused_beyond_the_narrowed_limit(gate):
    gate._grant_locked(INTERACTIVE)          # learner now active
    gate._inflight, gate._bg_inflight = 1, 1  # one build call already running
    assert not gate._can_dispatch_now(_ctx(BACKGROUND)), (
        "second concurrent build generation admitted during a session")


def test_interactive_is_still_admitted_immediately(gate):
    """Narrowing background must never narrow the learner."""
    gate._inflight, gate._bg_inflight = 1, 1
    assert gate._can_dispatch_now(_ctx(INTERACTIVE))


def test_background_only_workload_is_unaffected(gate):
    """No learner, no throttle — the build must not lose throughput."""
    gate._inflight, gate._bg_inflight = 2, 2
    assert gate._can_dispatch_now(_ctx(BACKGROUND)), (
        "build throttled with nobody in a session")
