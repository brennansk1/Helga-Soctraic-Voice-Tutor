"""GPU admission gate (B23.1/B23.2) — bounded concurrency + per-student fair
queue + priority classes for every LLM call.

Ollama serves at most OLLAMA_NUM_PARALLEL requests concurrently; beyond that it
queues internally with no fairness and no backpressure. This gate keeps
in-flight requests at the cap and decides who gets the next freed slot:

- INTERACTIVE (live tutoring) always outranks BACKGROUND (course building).
- Background holds at most `bg_slots` (=1) slots and is never granted while an
  interactive turn waits — a course build can't starve live students.
- Among waiting interactive turns, slots are granted round-robin across
  distinct student_ids (FIFO within a student), so one chatty student can't
  monopolize the GPU.
- Backpressure: a waiter blocked longer than `busy_after` seconds fires its
  ctx.on_busy callback once (the FSM emits a friendly "busy" status to the
  student's room) instead of freezing until the client timeout.
- Overload: queue depth beyond `max_queue` or a wait beyond `admit_timeout`
  raises GpuOverloaded; callers degrade gracefully, never a raw timeout.

In the R1 single-worker topology core-logic owns LLM traffic, so an in-process
gate is authoritative. At R4 multi-worker the counters move to Redis behind
this same API (design spec 10 §4.4) — call sites never change.

Uses threading primitives: correct under plain threads today and cooperative
automatically if the process is gevent-monkey-patched.
"""

import itertools
import logging
import os
import threading
import time
from collections import deque, OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

INTERACTIVE = "interactive"
BACKGROUND = "background"


class GpuOverloaded(Exception):
    """Raised when the queue is saturated or an admit wait times out."""


@dataclass
class LLMContext:
    """Tag carried by every LLM call: who is asking and at what priority."""
    klass: str = INTERACTIVE
    student_id: str = "_anon"
    room: Optional[str] = None          # Socket.IO room for busy emits (info)
    request_id: Optional[str] = None    # correlation id (B27.1)
    on_busy: Optional[Callable[[dict], None]] = field(default=None, repr=False)


class _Waiter:
    __slots__ = ("event", "enqueued_at", "klass", "student_id", "ctx", "granted")

    def __init__(self, ctx):
        self.event = threading.Event()
        self.enqueued_at = time.monotonic()
        self.klass = ctx.klass
        self.student_id = ctx.student_id
        self.ctx = ctx
        self.granted = False


class _Slot:
    """Context manager returned by admit(); releasing returns the slot."""
    __slots__ = ("_gate", "klass", "_released")

    def __init__(self, gate, klass):
        self._gate = gate
        self.klass = klass
        self._released = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def release(self):
        if not self._released:
            self._released = True
            self._gate._release(self)


class GpuGate:
    def __init__(self, num_parallel=None, bg_slots=None, busy_after_s=8.0,
                 max_queue=256, admit_timeout_s=55.0):
        self.cap = int(num_parallel or os.getenv("OLLAMA_NUM_PARALLEL", "4"))
        # Background gets every slot but one.
        #
        # This defaulted to 1, and the hydrator sizes its thread pool from it,
        # so course building ran ONE concept at a time against a server able to
        # decode four in parallel. Measured on a 12-concept mastery-4 build:
        # 70 LLM calls, ~35,500 generated tokens, and decode is 93% of the wall
        # clock — so the serialisation was costing roughly the whole build.
        #
        # A RESERVED SLOT IS NOT RESERVED BANDWIDTH. This is the part that was
        # wrong, and it was wrong in the direction that hurts the learner.
        #
        # The paragraph here used to claim that raising bg_slots "does not put
        # a student behind a course build", because an arriving interactive
        # turn always finds a free slot and is dispatched immediately. Both
        # halves of that are true and the conclusion still does not follow.
        # Measured on 2026-08-25, during a live build, with the slot free and
        # dispatch immediate:
        #
        #     two concurrent 4-token generations: 145 SECONDS
        #
        # Four tokens. The slot was granted at once; what the learner queued
        # behind was memory bandwidth. This is a 120 GB/s machine whose decode
        # already runs at ~85% of that ceiling, so three background generations
        # do not leave a quarter of the machine for the fourth request — they
        # leave a sliver. The gate can schedule slots. It cannot schedule the
        # memory bus.
        #
        # So background yields by COUNT when someone is actually learning:
        # full width while nobody is in a session, one slot while somebody is.
        # `_dispatch_next` still drains interactive waiters first, and
        # background is still never granted while an interactive turn waits —
        # those were right, they were just not sufficient on their own.
        #
        # Set HELGA_BG_SLOTS=1 to restore the old single-file behaviour.
        if bg_slots is None:
            bg_slots = int(os.getenv("HELGA_BG_SLOTS", "0")) or (self.cap - 1)
        self.bg_slots = max(1, min(bg_slots, self.cap - 1)) if self.cap > 1 else 1
        self.busy_after = busy_after_s
        self.max_queue = max_queue
        self.admit_timeout = admit_timeout_s

        # How long after an interactive turn the machine is still treated as
        # "in use by a learner". A Socratic turn is followed by the learner
        # READING and typing, which is exactly when a build would grab the
        # bandwidth back and be mid-generation when the next turn arrives.
        # Long enough to cover thinking time, short enough that a build is not
        # throttled all evening by someone who left.
        self.interactive_window_s = float(
            os.getenv("HELGA_INTERACTIVE_WINDOW_S", "180"))
        self._last_interactive_at = 0.0

        self._inflight = 0
        self._bg_inflight = 0
        self._iq = OrderedDict()   # student_id -> deque[_Waiter]; rotation order
        self._bq = deque()         # background waiters, plain FIFO
        self._lock = threading.RLock()
        self._waiters = 0
        # counters for /health and B27 metrics
        self.stats_granted = 0
        self.stats_busy_emits = 0
        self.stats_overloads = 0

    # ------------------------------------------------------------------ API

    def admit(self, ctx: LLMContext = None, timeout: float = None) -> _Slot:
        """Acquire a slot (blocking, fair). Returns a context-manager slot.
        Raises GpuOverloaded on saturation or wait timeout."""
        ctx = ctx or LLMContext()
        timeout = timeout if timeout is not None else self.admit_timeout

        # Memory safeguard. This gate limits CONCURRENCY, which does not stop a
        # single background job from pushing an already-tight machine into swap
        # — measured on this hardware: 24 GB total, 10.3 GB of 11.3 GB swap
        # already consumed with Docker DOWN and only a browser open.
        #
        # Helga runs on the user's own computer, so batch work must yield when
        # memory is scarce. FOREGROUND work is deliberately never blocked here:
        # a student waiting mid-lesson is not the thing to sacrifice.
        if getattr(ctx, "klass", None) == BACKGROUND:
            try:
                from services.common.memory_guard import (
                    pressure_reason, wait_for_headroom,
                )
                reason = pressure_reason()
                if reason:
                    logger.warning(f"[MEM] background admit deferred — {reason}")
                    if not wait_for_headroom(timeout_s=min(timeout, 120.0)):
                        self.stats_overloads += 1
                        raise GpuOverloaded(
                            f"insufficient memory for background work — {reason}")
            except GpuOverloaded:
                raise
            except Exception as e:
                # Fail OPEN: an unreadable memory subsystem must never wedge
                # the pipeline.
                logger.debug(f"memory guard unavailable, allowing: {e}")

        with self._lock:
            if self._waiters >= self.max_queue:
                self.stats_overloads += 1
                raise GpuOverloaded(f"queue saturated ({self._waiters} waiting)")
            if self._can_dispatch_now(ctx):
                return self._grant_locked(ctx.klass)
            w = _Waiter(ctx)
            self._enqueue(w)
            self._waiters += 1

        try:
            # First wait window: until the busy threshold, then notify once.
            granted = w.event.wait(min(self.busy_after, timeout))
            if not granted and timeout > self.busy_after:
                self._notify_busy(w)
                granted = w.event.wait(timeout - self.busy_after)
        finally:
            if not w.granted:
                self._abandon(w)
        if not w.granted:
            self.stats_overloads += 1
            raise GpuOverloaded(f"admit wait exceeded {timeout}s "
                                f"(student {ctx.student_id})")
        return _Slot(self, w.klass)

    def stats(self):
        with self._lock:
            return {
                "cap": self.cap,
                "inflight": self._inflight,
                "bg_inflight": self._bg_inflight,
                "waiting_interactive": sum(len(d) for d in self._iq.values()),
                "waiting_background": len(self._bq),
                "granted_total": self.stats_granted,
                "busy_emits": self.stats_busy_emits,
                "overloads": self.stats_overloads,
            }

    # ------------------------------------------------------------- internal

    def _effective_bg_slots(self):
        """How many background slots background may hold right now.

        Full width when nobody is learning. One while a learner is in a
        session, because the second and third concurrent generation cost that
        learner more than they gain the build — see the 145s measurement above.
        """
        if self._is_learner_active():
            return 1
        return self.bg_slots

    def _is_learner_active(self):
        if any(self._iq.values()):
            return True
        if self._inflight > self._bg_inflight:
            return True   # an interactive turn is generating right now
        return (time.time() - self._last_interactive_at) < self.interactive_window_s

    def _can_dispatch_now(self, ctx):
        if self._inflight >= self.cap:
            return False
        if ctx.klass == BACKGROUND:
            if self._bg_inflight >= self._effective_bg_slots():
                return False
            # never let background jump while any interactive turn waits
            if any(self._iq.values()):
                return False
        return True

    def _grant_locked(self, klass):
        if klass != BACKGROUND:
            self._last_interactive_at = time.time()
        self._inflight += 1
        if klass == BACKGROUND:
            self._bg_inflight += 1
        self.stats_granted += 1
        return _Slot(self, klass)

    def _enqueue(self, w):
        if w.klass == BACKGROUND:
            self._bq.append(w)
        else:
            self._iq.setdefault(w.student_id, deque()).append(w)

    def _abandon(self, w):
        """Remove a timed-out waiter from its queue (under lock)."""
        with self._lock:
            if w.granted:
                return
            if w.klass == BACKGROUND:
                try:
                    self._bq.remove(w)
                except ValueError:
                    pass
            else:
                dq = self._iq.get(w.student_id)
                if dq:
                    try:
                        dq.remove(w)
                    except ValueError:
                        pass
                    if not dq:
                        del self._iq[w.student_id]
            self._waiters = max(0, self._waiters - 1)

    def _release(self, slot):
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            if slot.klass == BACKGROUND:
                self._bg_inflight = max(0, self._bg_inflight - 1)
            self._dispatch_next()

    def _dispatch_next(self):
        """Called under lock: hand freed slots to waiters — interactive first
        (round-robin across students), background only when nothing
        interactive waits and bg is under its slot cap."""
        while self._inflight < self.cap:
            w = self._pick_interactive_rr()
            if w is None:
                if self._bg_inflight < self.bg_slots and self._bq:
                    w = self._bq.popleft()
                else:
                    break
            self._inflight += 1
            if w.klass == BACKGROUND:
                self._bg_inflight += 1
            self._waiters = max(0, self._waiters - 1)
            self.stats_granted += 1
            w.granted = True
            w.event.set()

    def _pick_interactive_rr(self):
        """Queue-of-queues rotation: grant the head student's oldest turn,
        then rotate that student's bucket to the back. New students join at
        the back, so with K students waiting each gets every Kth freed slot
        regardless of how many turns they queued."""
        while self._iq:
            sid, dq = next(iter(self._iq.items()))
            if not dq:
                del self._iq[sid]
                continue
            w = dq.popleft()
            del self._iq[sid]
            if dq:
                self._iq[sid] = dq  # rotate to the back of the rotation
            return w
        return None

    def _notify_busy(self, w):
        """One-time informational 'busy' to the waiting student (never drops
        the request — it still gets dispatched fairly)."""
        self.stats_busy_emits += 1
        cb = w.ctx.on_busy
        if cb is None:
            return
        try:
            with self._lock:
                depth = sum(len(d) for d in self._iq.values()) + len(self._bq)
            cb({
                "type": "GPU_BUSY",
                "msg": "Helga is thinking — lots of students right now, one moment…",
                "queue_depth": depth,
                "student_id": w.student_id,
            })
        except Exception as e:
            logger.warning(f"busy callback failed: {e}")


_gate = None
_gate_lock = threading.Lock()


def get_gpu_gate() -> GpuGate:
    """Module-level singleton (mirrors get_llm_client)."""
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                _gate = GpuGate()
                logger.info(f"GpuGate initialized: cap={_gate.cap} "
                            f"bg_slots={_gate.bg_slots}")
    return _gate


# --------------------------------------------------------------------------
# B27.5 / B9.5 / A7: Ollama circuit breaker.
#
# The implementation used to live here, which meant only the core service had
# one: `services/common/llm_utils.py` — the BUILD path, the caller with the most
# to lose from a dead host — could not import it, because the RAG container has
# no `services/core`. So a course build kept paying full timeouts on every one
# of its dozens of calls while live tutoring was already fast-failing.
#
# It now lives in `services/common/llm_breaker.py`, which every container ships,
# and both paths share ONE breaker instance. These re-exports keep the historical
# `gpu_gate` surface (`OllamaBreaker`, `OllamaUnavailable`, `get_breaker`,
# `CLOSED/OPEN/HALF_OPEN`) working unchanged for fsm_logic, llm_client and their
# tests.

from services.common.llm_breaker import (  # noqa: E402,F401
    CLOSED, OPEN, HALF_OPEN,
    CircuitBreaker, OllamaBreaker, OllamaUnavailable,
    LLMError, LLMUnavailable, LLMCircuitOpen, LLMTimeout, LLMTransportError,
    LLMOverloaded, LLMBadOutput, LLMBadJSON, LLMSchemaMismatch,
    LLMEmptyResponse, LLMRequestRejected,
    get_breaker, reset_breaker, last_llm_failure, record_failure_reason,
    strict_default,
)
