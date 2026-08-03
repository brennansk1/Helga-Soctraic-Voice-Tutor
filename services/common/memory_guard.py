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
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Below this much free RAM, background work stands down.
# 1.5 GB: a hard floor for "about to fail", not a comfort margin. The
# earlier 3.0 GB was an arbitrary comfort number that blocked work while
# the kernel reported the machine healthy.
MIN_FREE_GB = float(os.getenv("HELGA_MIN_FREE_GB", "1.5"))
# Above this fraction of swap consumed, the machine is already distressed.
MAX_SWAP_USED_FRAC = float(os.getenv("HELGA_MAX_SWAP_FRAC", "0.80"))
# Don't re-measure more often than this (vm_stat/psutil calls aren't free).
_CACHE_TTL_S = float(os.getenv("HELGA_MEM_POLL_S", "5"))

# Master switch. Set HELGA_MEMORY_GUARD=0 to disable throttling entirely.
# Automatically off under pytest: the guard is designed to BLOCK background
# work for up to two minutes when the machine is genuinely short of memory,
# which is right in production and disastrous in a test suite — it turned a
# 40s run into 182s on a developer box that happened to be swapping.
def _guard_enabled():
    if os.getenv("HELGA_MEMORY_GUARD", "").lower() in ("0", "false", "no"):
        return False
    if os.getenv("PYTEST_CURRENT_TEST") or "pytest" in os.path.basename(
            os.environ.get("_", "")):
        return False
    return "pytest" not in sys.modules


_lock = threading.Lock()
_cached = {"t": 0.0, "snap": None}


class MemorySnapshot(dict):
    """dict with attribute access, so callers can use either style."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


def _num(v):
    """Coerce to float, rejecting anything that isn't genuinely numeric.

    A safety component must not trust its inputs. Besides guarding against a
    misbehaving psutil in production, this matters in tests: eight modules in
    this suite install `sys.modules['psutil'] = MagicMock()` at import time,
    and a MagicMock attribute sails through arithmetic only to explode later on
    a comparison. Rejecting it here makes the reader fall through to vm_stat
    instead of producing nonsense thresholds.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(f"non-numeric memory value: {type(v).__name__}")
    return float(v)


def _read_psutil():
    import psutil
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    total = _num(vm.total)
    avail = _num(vm.available)
    sw_total = _num(sw.total)
    sw_used = _num(sw.used)
    if total <= 0:
        raise ValueError("psutil reported non-positive total memory")
    return MemorySnapshot(
        total_gb=total / 2**30,
        available_gb=avail / 2**30,
        used_pct=_num(vm.percent),
        swap_total_gb=sw_total / 2**30,
        swap_used_gb=sw_used / 2**30,
        swap_used_frac=(sw_used / sw_total) if sw_total else 0.0,
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


def macos_pressure_level():
    """macOS's own verdict: 1=normal, 2=warning, 4=critical. None elsewhere.

    This is the AUTHORITATIVE signal on this platform and it must outrank our
    own heuristics — see pressure_reason() for why that matters.
    """
    try:
        out = subprocess.run(
            ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            capture_output=True, text=True, timeout=3).stdout.strip()
        return int(out) if out else None
    except Exception:
        return None


def pressure_reason(snap=None):
    """Return a human reason if memory is genuinely tight, else None.

    CALIBRATION NOTE — this was wrong once and blocked real work.

    The first version treated swap-fill percentage as distress. On macOS that
    is simply false: the OS keeps swap populated on purpose, so a high fill is
    normal steady state, not thrashing. Measured while it was misfiring:

        my guard    : "PRESSURE: swap 89% used"   -> refused all background work
        macOS says  : pressure_level = 1 (NORMAL), free percentage 82%

    It aborted a course build three times over, and because the refusal
    surfaced as "LLM consistently failed to generate 3 modules", the real cause
    was two layers away from the error message.

    So: trust the kernel's own pressure level first. Swap fill is only
    corroborating evidence, and only when free memory is ALSO low — swap being
    full while 82% of RAM is free is not a reason to stop working.
    """
    s = snap or snapshot()
    if s.source == "unavailable":
        return None

    lvl = macos_pressure_level()
    if lvl is not None:
        # The kernel's verdict is authoritative in BOTH directions. Treating it
        # as authoritative only when it says "bad" was the second calibration
        # error: with the kernel reporting NORMAL, a heuristic still blocked the
        # build at "swap 89% with only 5.9 GB free" — 0.1 GB under an arbitrary
        # corroboration threshold. macOS knows whether it is thrashing; we do
        # not, and second-guessing it costs real work.
        # WARN (2) is advisory; CRITICAL (4) means stop.
        #
        # Third calibration pass, and the semantics matter: macOS enters level 2
        # routinely under ordinary load — a browser, an editor and a 6 GB model
        # resident is enough. Treating WARN as "stop all background work" stalled
        # a course build indefinitely on a machine that was working fine.
        # Level 2 is where we THROTTLE (see suggested_workers) and keep going;
        # level 4 is where we stand down.
        if lvl >= 4:
            return f"macOS reports CRITICAL memory pressure (level {lvl})"
        if s.available_gb < MIN_FREE_GB:
            return (f"only {s.available_gb:.1f} GB free "
                    f"(floor {MIN_FREE_GB:.1f} GB)")
        return None

    # --- non-macOS / kernel signal unavailable: fall back to heuristics ---
    if s.available_gb < MIN_FREE_GB:
        return (f"only {s.available_gb:.1f} GB free "
                f"(floor {MIN_FREE_GB:.1f} GB)")
    # Swap alone is NOT distress. Require corroboration from low free memory.
    if (s.swap_used_frac > MAX_SWAP_USED_FRAC
            and s.available_gb < MIN_FREE_GB * 2):
        return (f"swap {100 * s.swap_used_frac:.0f}% used with only "
                f"{s.available_gb:.1f} GB free")
    return None


def under_pressure(snap=None):
    return pressure_reason(snap) is not None


def allow_background(snap=None):
    """May a background (batch) job start work right now?

    Foreground work should NOT consult this — a student mid-lesson is not the
    thing to sacrifice.
    """
    if not _guard_enabled():
        return True
    return pressure_reason(snap) is None


def wait_for_headroom(timeout_s=300.0, poll_s=10.0, on_wait=None):
    """Block a background worker until memory frees up.

    Returns True if headroom became available, False on timeout so the caller
    can degrade rather than hang forever. `on_wait(reason)` is called once per
    poll so a pipeline can surface "paused: system memory low" to the user
    instead of appearing to freeze.
    """
    if not _guard_enabled():
        return True
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
    """How many background workers current headroom supports.

    This is where kernel WARN (level 2) takes effect: throttle to a single
    worker rather than stopping. Stopping at WARN stalled a real build on a
    machine that was otherwise healthy.
    """
    s = snapshot()
    if s.source == "unavailable":
        return default
    lvl = macos_pressure_level()
    if lvl is not None and lvl >= 2:
        return 1
    usable = max(0.0, s.available_gb - MIN_FREE_GB)
    return max(1, min(default, int(usable // max(0.5, per_worker_gb)) or 1))


def describe():
    s = snapshot(force=True)
    reason = pressure_reason(s)
    return (f"mem {s.available_gb:.1f}/{s.total_gb:.1f} GB free, "
            f"swap {100 * s.swap_used_frac:.0f}% "
            f"[{'PRESSURE: ' + reason if reason else 'ok'}] ({s.source})")
