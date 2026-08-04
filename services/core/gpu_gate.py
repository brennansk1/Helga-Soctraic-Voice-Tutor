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
        # Raising it does not put a student behind a course build. The reserved
        # slot is what protects them: `_can_dispatch_now` refuses background
        # beyond `bg_slots`, so with cap=4 at most three slots are background
        # and an arriving interactive turn always finds the fourth free — it is
        # dispatched immediately rather than queued behind a 30-second
        # generation. `_dispatch_next` also drains interactive waiters first,
        # and background is never granted while any interactive turn waits.
        #
        # Set HELGA_BG_SLOTS=1 to restore the old single-file behaviour.
        if bg_slots is None:
            bg_slots = int(os.getenv("HELGA_BG_SLOTS", "0")) or (self.cap - 1)
        self.bg_slots = max(1, min(bg_slots, self.cap - 1)) if self.cap > 1 else 1
        self.busy_after = busy_after_s
        self.max_queue = max_queue
        self.admit_timeout = admit_timeout_s

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

    def _can_dispatch_now(self, ctx):
        if self._inflight >= self.cap:
            return False
        if ctx.klass == BACKGROUND:
            if self._bg_inflight >= self.bg_slots:
                return False
            # never let background jump while any interactive turn waits
            if any(self._iq.values()):
                return False
        return True

    def _grant_locked(self, klass):
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
# B27.5 / B9.5: Ollama circuit breaker — the LLM host is a hard external
# dependency with no fallback; when it is down we fast-fail with a friendly
# message instead of hanging every student for the full client timeout.

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


class OllamaBreaker:
    """CLOSED → OPEN after `trip_after` consecutive connection failures;
    while OPEN, allow() fast-fails; after `probe_interval` seconds one probe
    request is let through (HALF_OPEN) — success closes, failure re-opens."""

    def __init__(self, trip_after=3, probe_interval=15.0):
        self.trip_after = trip_after
        self.probe_interval = probe_interval
        self._state = CLOSED
        self._fails = 0
        self._opened_at = 0.0
        self._probing = False
        self._lock = threading.RLock()
        self.state_changes = 0

    @property
    def state(self):
        return self._state

    def allow(self):
        """True if a request may proceed (CLOSED always; OPEN only the single
        half-open probe after the probe interval)."""
        with self._lock:
            if self._state == CLOSED:
                return True
            if self._state == OPEN:
                if time.time() - self._opened_at >= self.probe_interval:
                    self._state = HALF_OPEN
                    self._probing = True
                    logger.info("Ollama breaker HALF_OPEN — probing")
                    return True
                return False
            # HALF_OPEN: one probe in flight; everything else fast-fails
            if not self._probing:
                self._probing = True
                return True
            return False

    def record_success(self):
        with self._lock:
            if self._state != CLOSED:
                logger.info("Ollama breaker CLOSED (recovered)")
                self.state_changes += 1
            self._state = CLOSED
            self._fails = 0
            self._probing = False

    def record_failure(self):
        with self._lock:
            self._fails += 1
            self._probing = False
            if self._state == HALF_OPEN or self._fails >= self.trip_after:
                if self._state != OPEN:
                    logger.error(f"Ollama breaker OPEN after {self._fails} "
                                 f"consecutive failures — fast-failing LLM calls")
                    self.state_changes += 1
                self._state = OPEN
                self._opened_at = time.time()

    def stats(self):
        with self._lock:
            return {"state": self._state, "consecutive_failures": self._fails,
                    "state_changes": self.state_changes}


class OllamaUnavailable(Exception):
    """Raised (or mapped to a friendly message) when the breaker is OPEN."""


_breaker = None
_breaker_lock = threading.Lock()


def get_breaker() -> OllamaBreaker:
    global _breaker
    if _breaker is None:
        with _breaker_lock:
            if _breaker is None:
                _breaker = OllamaBreaker(
                    trip_after=int(os.getenv("OLLAMA_BREAKER_TRIP", "3")),
                    probe_interval=float(os.getenv("OLLAMA_BREAKER_PROBE_S", "15")))
    return _breaker
