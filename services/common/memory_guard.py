"""Adaptive memory safeguard — don't OOM the machine the user is also using.

WHY THIS EXISTS
---------------
Helga runs on a shared personal machine, not a dedicated server. Measured on
this Mac Mini M4 Pro (24 GB) with Docker DOWN and only a browser open:

    total            24.0 GB
    free             ~25%
    ollama llama-server  6.11 GB resident
    swap             10.3 GB used of 11.3 GB   <-- nearly exhausted

Bring up the six containers (~4.2 GB of declared limits) and load a larger
model, and the box thrashes or the OOM killer starts picking victims — while
the user is trying to do something else on their own computer.

The existing GpuGate limits CONCURRENCY, which does not help here: one
background hydration call is enough to push an already-tight machine over.
This module adds the missing dimension — actual memory headroom — and lets
background work stand down while foreground (a student mid-lesson) keeps
running.

DESIGN
------
- Foreground work is never blocked. A student waiting on a tutor reply is not
  the thing to sacrifice; if we must degrade, we degrade the batch job.
- Background work (course generation, hydration, audits) yields under pressure.
- Pressure is judged on BOTH free memory and swap activity: on macOS the free
  page count stays deceptively low because of caching, so swap-in-use is the
  more honest distress signal.
- Everything is advisory and fail-open: if we cannot read memory stats we allow
  the work rather than deadlocking the pipeline.
"""

import logging
import os
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# Below this much free RAM, background work stands down.
MIN_FREE_GB = float(os.getenv("HELGA_MIN_FREE_GB", "3.0"))
# Above this fraction of swap consumed, the machine is already distressed.
MAX_SWAP_USED_FRAC = float(os.getenv("HELGA_MAX_SWAP_FRAC", "0.80"))
# Don't re-measure more often than this (vm_stat/psutil calls aren't free).
_CACHE_TTL_S = float(os.getenv("HELGA_MEM_POLL_S", "5"))

_lock = threading.Lock()
_cached = {"t": 0.0, "snap": None}


class MemorySnapshot(dict):
    """dict with attribute access, so callers can use either style."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


def _read_psutil():
    import psutil
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return MemorySnapshot(
        total_gb=vm.total / 2**30,
        available_gb=vm.available / 2**30,
        used_pct=vm.percent,
        swap_total_gb=sw.total / 2**30,
        swap_used_gb=sw.used / 2**30,
        swap_used_frac=(sw.used / sw.total) if sw.total else 0.0,
        source="psutil",
    )


def _read_vm_stat():
    """macOS fallback when psutil isn't installed."""
    out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                         timeout=5).stdout
    page = 4096
    first = out.splitlines()[0]
    if "page size of" in first:
        page = int(first.split("page size of")[1].split("bytes")[0].strip())
    vals = {}
    for line in out.splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            vals[k.strip()] = int(v.strip().rstrip("."))
    free_pages = vals.get("Pages free", 0) + vals.get("Pages speculative", 0)
    total_bytes = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                     capture_output=True, text=True,
                                     timeout=5).stdout.strip())
    sw = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True,
                        text=True, timeout=5).stdout
    swap_total = swap_used = 0.0
    for part in sw.split():
        pass
    try:
        seg = sw.split("total =")[1]
        swap_total = float(seg.split("M")[0].strip()) / 1024
        swap_used = float(seg.split("used =")[1].split("M")[0].strip()) / 1024
    except Exception:
        pass
    avail = free_pages * page / 2**30
    return MemorySnapshot(
        total_gb=total_bytes / 2**30,
        available_gb=avail,
        used_pct=100.0 * (1 - avail / (total_bytes / 2**30)),
        swap_total_gb=swap_total,
        swap_used_gb=swap_used,
        swap_used_frac=(swap_used / swap_total) if swap_total else 0.0,
        source="vm_stat",
    )


def snapshot(force=False):
    """Current memory state. Cached briefly; never raises."""
    now = time.time()
    with _lock:
        if not force and _cached["snap"] and now - _cached["t"] < _CACHE_TTL_S:
            return _cached["snap"]
    snap = None
    for reader in (_read_psutil, _read_vm_stat):
        try:
            snap = reader()
            break
        except Exception as e:
            logger.debug(f"memory reader {reader.__name__} failed: {e}")
    if snap is None:
        # Fail OPEN: an unreadable machine must not deadlock the pipeline.
        snap = MemorySnapshot(total_gb=0.0, available_gb=float("inf"),
                              used_pct=0.0, swap_total_gb=0.0,
                              swap_used_gb=0.0, swap_used_frac=0.0,
                              source="unavailable")
    with _lock:
        _cached["t"], _cached["snap"] = now, snap
    return snap


def pressure_reason(snap=None):
    """Return a human reason if memory is tight, else None."""
    s = snap or snapshot()
    if s.source == "unavailable":
        return None
    if s.available_gb < MIN_FREE_GB:
        return (f"only {s.available_gb:.1f} GB free "
                f"(floor {MIN_FREE_GB:.1f} GB)")
    if s.swap_used_frac > MAX_SWAP_USED_FRAC:
        return (f"swap {100 * s.swap_used_frac:.0f}% used "
                f"({s.swap_used_gb:.1f}/{s.swap_total_gb:.1f} GB)")
    return None


def under_pressure(snap=None):
    return pressure_reason(snap) is not None


def allow_background(snap=None):
    """May a background (batch) job start work right now?

    Foreground work should NOT consult this — a student mid-lesson is not the
    thing to sacrifice.
    """
    return pressure_reason(snap) is None


def wait_for_headroom(timeout_s=300.0, poll_s=10.0, on_wait=None):
    """Block a background worker until memory frees up.

    Returns True if headroom became available, False on timeout so the caller
    can degrade rather than hang forever. `on_wait(reason)` is called once per
    poll so a pipeline can surface "paused: system memory low" to the user
    instead of appearing to freeze.
    """
    deadline = time.time() + timeout_s
    notified = False
    while time.time() < deadline:
        reason = pressure_reason(snapshot(force=True))
        if reason is None:
            if notified:
                logger.info("[MEM] headroom restored — resuming background work")
            return True
        if on_wait:
            try:
                on_wait(reason)
            except Exception:
                pass
        if not notified:
            logger.warning(f"[MEM] background work paused — {reason}")
            notified = True
        time.sleep(poll_s)
    logger.warning("[MEM] timed out waiting for headroom; degrading")
    return False


def suggested_workers(default=1, per_worker_gb=2.0):
    """How many background workers current headroom supports."""
    s = snapshot()
    if s.source == "unavailable":
        return default
    usable = max(0.0, s.available_gb - MIN_FREE_GB)
    return max(1, min(default, int(usable // max(0.5, per_worker_gb)) or 1))


def describe():
    s = snapshot(force=True)
    reason = pressure_reason(s)
    return (f"mem {s.available_gb:.1f}/{s.total_gb:.1f} GB free, "
            f"swap {100 * s.swap_used_frac:.0f}% "
            f"[{'PRESSURE: ' + reason if reason else 'ok'}] ({s.source})")
