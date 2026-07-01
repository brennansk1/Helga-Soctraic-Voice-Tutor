"""B23.1/B23.2 tests: GPU admission gate — cap, priority, fairness,
backpressure, overload shedding.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../services/core')))

from gpu_gate import (
    GpuGate, GpuOverloaded, LLMContext, INTERACTIVE, BACKGROUND,
)


def _ictx(sid="stu_a", on_busy=None):
    return LLMContext(INTERACTIVE, sid, on_busy=on_busy)


def _bctx(sid="_system"):
    return LLMContext(BACKGROUND, sid)


class TestCapAndRelease:
    def test_admits_up_to_cap(self):
        g = GpuGate(num_parallel=2)
        s1 = g.admit(_ictx())
        s2 = g.admit(_ictx())
        assert g.stats()["inflight"] == 2
        s1.release()
        s2.release()
        assert g.stats()["inflight"] == 0

    def test_third_waits_until_release(self):
        g = GpuGate(num_parallel=1, admit_timeout_s=5)
        s1 = g.admit(_ictx("stu_a"))
        result = {}

        def waiter():
            with g.admit(_ictx("stu_b"), timeout=5):
                result["granted_at"] = time.monotonic()

        t = threading.Thread(target=waiter)
        start = time.monotonic()
        t.start()
        time.sleep(0.2)
        assert "granted_at" not in result  # still queued
        s1.release()
        t.join(timeout=2)
        assert result["granted_at"] - start >= 0.2

    def test_double_release_is_safe(self):
        g = GpuGate(num_parallel=1)
        s = g.admit(_ictx())
        s.release()
        s.release()
        assert g.stats()["inflight"] == 0

    def test_context_manager_releases(self):
        g = GpuGate(num_parallel=1)
        with g.admit(_ictx()):
            assert g.stats()["inflight"] == 1
        assert g.stats()["inflight"] == 0


class TestPriority:
    def test_background_capped_to_bg_slots(self):
        g = GpuGate(num_parallel=4, bg_slots=1, admit_timeout_s=0.3)
        b1 = g.admit(_bctx())
        with pytest.raises(GpuOverloaded):
            g.admit(_bctx(), timeout=0.3)  # 2nd bg blocked despite free cap
        assert g.stats()["inflight"] == 1
        b1.release()

    def test_interactive_beats_waiting_background(self):
        g = GpuGate(num_parallel=1, admit_timeout_s=5)
        s = g.admit(_ictx("stu_a"))
        order = []

        def bg():
            with g.admit(_bctx(), timeout=5):
                order.append("bg")

        def live():
            with g.admit(_ictx("stu_b"), timeout=5):
                order.append("live")

        tb = threading.Thread(target=bg)
        tb.start()
        time.sleep(0.1)  # bg queued first
        tl = threading.Thread(target=live)
        tl.start()
        time.sleep(0.1)
        s.release()      # one slot frees: live student must win despite FIFO
        tl.join(timeout=2)
        tb.join(timeout=2)
        assert order[0] == "live"

    def test_background_never_granted_while_interactive_waits(self):
        g = GpuGate(num_parallel=2, bg_slots=1, admit_timeout_s=0.4)
        s1 = g.admit(_ictx("stu_a"))
        s2 = g.admit(_ictx("stu_b"))
        held = []

        def live_waiter():
            with g.admit(_ictx("stu_c"), timeout=5):
                held.append("c")
                time.sleep(0.5)

        t = threading.Thread(target=live_waiter)
        t.start()
        time.sleep(0.1)
        # interactive c is waiting; a background admit must time out even
        # after a slot frees, because c takes it first
        s1.release()
        with pytest.raises(GpuOverloaded):
            g.admit(_bctx(), timeout=0.4)
        s2.release()
        t.join(timeout=3)
        assert held == ["c"]


class TestFairness:
    def test_round_robin_across_students(self):
        """One chatty student queues 3 turns; two other students queue 1 each.
        The freed slots must interleave students, not drain the chatty one."""
        g = GpuGate(num_parallel=1, admit_timeout_s=10)
        s = g.admit(_ictx("stu_z"))
        order = []
        done = threading.Event()

        def turn(sid):
            with g.admit(_ictx(sid), timeout=10):
                order.append(sid)
                time.sleep(0.02)

        threads = []
        # chatty student queues 3 turns first
        for _ in range(3):
            t = threading.Thread(target=turn, args=("stu_chatty",))
            t.start()
            threads.append(t)
            time.sleep(0.05)
        for sid in ("stu_b", "stu_c"):
            t = threading.Thread(target=turn, args=(sid,))
            t.start()
            threads.append(t)
            time.sleep(0.05)

        s.release()
        for t in threads:
            t.join(timeout=5)

        # every student appears in the first three grants (round-robin), i.e.
        # chatty cannot take grants 1-3 back-to-back
        assert set(order[:3]) == {"stu_chatty", "stu_b", "stu_c"}


class TestBackpressureAndOverload:
    def test_busy_callback_fires_once_while_waiting(self):
        g = GpuGate(num_parallel=1, busy_after_s=0.1, admit_timeout_s=2)
        s = g.admit(_ictx("stu_a"))
        busy_calls = []

        def waiter():
            ctx = _ictx("stu_b", on_busy=lambda info: busy_calls.append(info))
            with g.admit(ctx, timeout=2):
                pass

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.4)   # past busy_after while still queued
        s.release()
        t.join(timeout=2)
        assert len(busy_calls) == 1
        assert busy_calls[0]["type"] == "GPU_BUSY"

    def test_admit_timeout_raises_overloaded(self):
        g = GpuGate(num_parallel=1, busy_after_s=0.05)
        s = g.admit(_ictx("stu_a"))
        with pytest.raises(GpuOverloaded):
            g.admit(_ictx("stu_b"), timeout=0.3)
        s.release()
        # abandoned waiter must not receive the freed slot
        assert g.stats()["waiting_interactive"] == 0
        with g.admit(_ictx("stu_c"), timeout=1):
            pass  # slot reusable

    def test_queue_saturation_rejects(self):
        g = GpuGate(num_parallel=1, max_queue=2, admit_timeout_s=3)
        s = g.admit(_ictx("stu_a"))
        threads = []
        for sid in ("stu_b", "stu_c"):
            t = threading.Thread(target=lambda x=sid: g.admit(_ictx(x), timeout=3).release())
            t.start()
            threads.append(t)
        time.sleep(0.2)  # both queued → at max_queue
        with pytest.raises(GpuOverloaded):
            g.admit(_ictx("stu_d"), timeout=0.2)
        s.release()
        for t in threads:
            t.join(timeout=3)


class TestEnvDefaults:
    def test_cap_reads_ollama_num_parallel(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_NUM_PARALLEL", "7")
        g = GpuGate()
        assert g.cap == 7
