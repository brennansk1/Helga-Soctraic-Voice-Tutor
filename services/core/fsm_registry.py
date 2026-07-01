"""Per-student FSM registry (B15.6) — kills the global MnemosyneFSM singleton.

One MnemosyneFSM per active student, held in an in-process LRU with idle-TTL
eviction and flush-on-evict. The shared StorageManager (thread-safe, WAL) is
injected into every FSM; each FSM passes its own student_id into storage calls.

This registry is the seam for the eventual stateless multi-worker "Option A"
(design spec 03 §7): swapping this implementation for hydrate-per-turn requires
no call-site changes in the routes.
"""

import logging
import threading
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)


class FSMRegistry:
    """Create-or-revive MnemosyneFSM instances keyed by student_id.

    - LRU capacity cap (`max_size`): at cap the oldest idle FSM is flushed to
      its fsm_sessions row and dropped before admitting a new one.
    - Idle TTL: a single sweeper thread ticks every live FSM's timers and
      evicts FSMs idle longer than `idle_ttl` (flush-on-evict, lossless).
    - Per-student RLock serializes one student's rapid events; different
      students take different locks so they run fully concurrently.
    """

    def __init__(self, fsm_factory, storage, *, max_size=64, idle_ttl=1800,
                 sweep_interval=60, start_sweeper=True):
        """fsm_factory(student_id, storage) -> MnemosyneFSM.

        Passing the factory (rather than importing MnemosyneFSM here) keeps
        this module import-light and unit-testable with fakes.
        """
        self._factory = fsm_factory
        self.storage = storage
        self._fsms = OrderedDict()
        self._locks = {}
        self._reg_lock = threading.RLock()   # guards _fsms/_locks structure only
        self._max = max_size
        self._ttl = idle_ttl
        self._sweep_interval = sweep_interval
        self._sweeper = None
        if start_sweeper:
            self._start_sweeper()

    # ------------------------------------------------------------------ core

    def get(self, student_id):
        """Create-or-revive the FSM for this student (LRU touch)."""
        with self._reg_lock:
            fsm = self._fsms.get(student_id)
            if fsm is None:
                self._evict_if_over_cap()
                fsm = self._factory(student_id, self.storage)
                self._fsms[student_id] = fsm
                self._locks.setdefault(student_id, threading.RLock())
                logger.info(f"FSM created for student {student_id} "
                            f"({len(self._fsms)} resident)")
            self._fsms.move_to_end(student_id)
            fsm.last_touch = time.time()
            return fsm

    def lock_for(self, student_id):
        """Per-student RLock — hold across transition() to serialize one
        student's rapid events without blocking other students."""
        with self._reg_lock:
            return self._locks.setdefault(student_id, threading.RLock())

    def peek(self, student_id):
        """Return the resident FSM or None — never creates (used by /health)."""
        with self._reg_lock:
            return self._fsms.get(student_id)

    # ------------------------------------------------------------- lifecycle

    def evict(self, student_id):
        """Explicit flush+drop (logout, capacity)."""
        with self._reg_lock:
            fsm = self._fsms.pop(student_id, None)
        if fsm is not None:
            self._flush(fsm)

    def evict_idle(self):
        """Flush+drop FSMs idle longer than the TTL. Returns count evicted."""
        now = time.time()
        evicted = 0
        with self._reg_lock:
            idle = [sid for sid, f in self._fsms.items()
                    if now - getattr(f, 'last_touch', now) > self._ttl]
        for sid in idle:
            self.evict(sid)
            evicted += 1
        if evicted:
            logger.info(f"Evicted {evicted} idle FSM(s)")
        return evicted

    def flush_all(self):
        """Persist every live FSM (graceful-shutdown hook). FSMs stay resident."""
        with self._reg_lock:
            fsms = list(self._fsms.values())
        for fsm in fsms:
            self._flush(fsm)

    def stats(self):
        with self._reg_lock:
            return {
                "resident": len(self._fsms),
                "max_size": self._max,
                "idle_ttl": self._ttl,
                "students": [
                    {"student_id": sid,
                     "state": getattr(f, 'state', '?'),
                     "idle_seconds": round(time.time() - getattr(f, 'last_touch', 0))}
                    for sid, f in self._fsms.items()
                ],
            }

    def set_maintenance(self, paused):
        """Propagate maintenance pause/resume to every live FSM (replaces the
        per-instance SIGUSR1/2 handlers, which cannot be installed from
        request threads)."""
        with self._reg_lock:
            fsms = list(self._fsms.values())
        for fsm in fsms:
            fsm.maintenance_mode = paused
            fsm.maintenance_paused = paused

    # -------------------------------------------------------------- internal

    def _flush(self, fsm):
        try:
            fsm._save_current_course_progress()
        except Exception as e:
            logger.error(f"Flush failed for student "
                         f"{getattr(fsm, 'student_id', '?')}: {e}")

    def _evict_if_over_cap(self):
        """Called under _reg_lock. Drop LRU-oldest idle FSMs until under cap.
        If everything is hot, admit anyway (correctness > cap) but log."""
        while len(self._fsms) >= self._max:
            now = time.time()
            victim = None
            for sid, f in self._fsms.items():  # ordered oldest-touch first
                if now - getattr(f, 'last_touch', 0) > 60:  # only evict quiet FSMs
                    victim = sid
                    break
            if victim is None:
                logger.warning(f"FSM registry over cap ({self._max}) with all "
                               f"FSMs hot — admitting anyway")
                return
            fsm = self._fsms.pop(victim)
            self._flush(fsm)
            logger.info(f"LRU-evicted FSM for student {victim} (capacity)")

    def _start_sweeper(self):
        """ONE timer thread for all students (was one thread per FSM)."""
        def _sweep():
            while True:
                time.sleep(self._sweep_interval)
                try:
                    now = time.time()
                    with self._reg_lock:
                        fsms = list(self._fsms.values())
                    for fsm in fsms:
                        try:
                            fsm.tick(now)
                        except Exception as e:
                            logger.warning(f"tick failed: {e}")
                    self.evict_idle()
                except Exception as e:
                    logger.error(f"Registry sweep error: {e}")

        self._sweeper = threading.Thread(target=_sweep, daemon=True,
                                         name="fsm-registry-sweeper")
        self._sweeper.start()
