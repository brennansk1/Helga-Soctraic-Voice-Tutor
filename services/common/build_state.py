"""Shared, durable record of the course build currently running.

WHY THIS EXISTS
---------------
A build takes tens of minutes. Two things follow, and neither worked before:

1. THE LEARNER MUST BE ABLE TO NAVIGATE AWAY AND COME BACK. Build progress
   lived only in the page that started it — in browser memory, fed by a
   Socket.IO stream. Navigating to Courses and back lost everything: the same
   build was still running on the server, but the UI had no idea and showed a
   fresh, empty screen. Reloading looked like the build had vanished.

2. THE UI MUST LOCK WHILE IT RUNS. `_build_lock` in course_builder already
   allows exactly one build at a time and rejects the second — but it rejects
   it *after* the learner has filled in a form and pressed the button, with an
   error. The interface should not offer an action the system will refuse.

State is written to a file rather than held in a process because the services
are separate processes: core-logic runs the build, web-ui renders the UI. An
in-memory flag in one is invisible to the other.

WHAT THE RECORD COVERS
----------------------
The record spans the WHOLE pipeline — skeleton, audit, hydration, assets,
finalise — not just the skeleton. It used to end at the skeleton: `finish()`
was called from SkeletonBuilder.build's `finally`, roughly 5% into a 40-minute
build, so build-guard.js toasted "Your course is ready." and unlocked every
creation control while hydration had not started. Ownership is therefore
explicit: whoever runs the pipeline calls `start()` and exactly one of
`finish()` / `fail()`; the individual builder stages only report progress.

`start()` returns a BUILD ID and the stage calls accept it. A second build that
is refused by `_build_lock` used to overwrite the running build's record on its
way out, so the banner switched to the wrong topic and the first build's
eventual `finish()` marked the second one complete. `start()` now refuses to
clobber a live record, and a `build_id` mismatch makes a late `finish()` a
no-op.

Everything here is best-effort in the sense that a failure to record progress
must never break a build — but it is not silent. Write failures are logged at
WARNING and counted in `health()`; a build whose writes are all failing also
stops advancing `updated_at`, so the staleness check below eventually reaps it.
"""

import contextlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid

try:
    import fcntl
except ImportError:                     # pragma: no cover - Windows/dev only
    fcntl = None

logger = logging.getLogger(__name__)

_default_root = "/app/data" if os.path.exists("/app/data") and os.access("/app/data", os.W_OK) else os.path.abspath("data")
DATA_ROOT = os.getenv("DATA_ROOT", _default_root)
STATE_PATH = os.path.join(DATA_ROOT, "build_state.json")
LOCK_PATH = STATE_PATH + ".lock"

# A build that has not reported for this long is treated as dead. Without it a
# crashed build would lock the UI forever, which is worse than the problem the
# lock solves.
#
# The budget is PER STAGE because the quiet gaps are not comparable. The
# skeleton emits a line every few seconds; hydration emits one pair of lines
# per concept and a single concept can take minutes (research + generate +
# fact-check retry + depth retry). A flat 15 minutes was safe only while the
# record stopped at the skeleton — the moment it spans hydration, one slow
# concept would falsely report the build dead and unlock the UI mid-build,
# which is the exact failure this module exists to prevent.
STALE_AFTER_SECONDS = 15 * 60
STAGE_QUIET_BUDGET = {
    "preflight": 5 * 60,
    "skeleton": 15 * 60,
    "audit": 20 * 60,
    "hydrate": 20 * 60,
    "assets": 20 * 60,
    "finalize": 10 * 60,
}

# Absolute backstop. If a pipeline owner dies without calling finish()/fail()
# and something keeps the record chatty, the banner must still go away.
MAX_BUILD_SECONDS = 4 * 60 * 60

# Status vocabulary the builders already emit. Counting here rather than at the
# call site means every stage that tees its callback contributes — previously
# only SkeletonBuilder incremented anything, so `concepts` was declared and
# never written and the hydrator's progress was invisible.
_COUNTERS = (
    ("STRUCT:MODULE:", "modules"),
    ("STRUCT:CONCEPT:", "concepts"),
    ("STRUCT:HYDRATED:", "hydrated"),
    ("STRUCT:WARN:CONCEPT_STUB:", "stubs"),
)

# Stage inferred from the message stream. A caller that forgets an explicit
# stage() call still gets the right quiet budget, which is the setting that
# decides whether a live build gets reaped.
_STAGE_HINTS = (
    ("STRUCT:HYDRATING:", "hydrate"),
    ("STRUCT:HYDRATED:", "hydrate"),
    ("STRUCT:MODULE:", "skeleton"),
)

_thread_lock = threading.RLock()
_write_failures = 0
_last_write_error = None


def _record_failure(where, exc):
    """Count and surface a recording failure.

    Four bare `except -> logger.debug` used to make a broken state file look
    identical to "no build has ever run": the UI silently stopped locking and
    nobody could tell why.
    """
    global _write_failures, _last_write_error
    _write_failures += 1
    _last_write_error = f"{where}: {type(exc).__name__}: {exc}"
    logger.warning("build_state.%s failed: %s", where, exc, exc_info=True)


def health():
    """Recording health, for a health endpoint or a test assertion."""
    return {"write_failures": _write_failures, "last_error": _last_write_error}


def _atomic_write(path, payload):
    """Write via a temp file + rename so a reader never sees half a document."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def _exclusive():
    """Serialise read-modify-write across threads AND processes.

    Every mutator here is a whole-file read-modify-write. That was harmless
    while only the skeleton wrote — one thread, one process. It stops being
    harmless the moment the auditor, the hydrator and the asset collector tee
    into the same file from a ThreadPoolExecutor, and web-ui reaps stale
    records from a different container: two interleaved cycles lose one side's
    messages and counters entirely.

    The flock is advisory and best-effort; if it cannot be taken (a filesystem
    that does not support it) the thread lock still holds and the atomic
    rename still guarantees readers see a whole document.
    """
    with _thread_lock:
        fh = None
        if fcntl is not None:
            try:
                os.makedirs(os.path.dirname(LOCK_PATH) or ".", exist_ok=True)
                fh = open(LOCK_PATH, "a+")
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except Exception as e:
                logger.debug("build_state: file lock unavailable (%s)", e)
                if fh:
                    fh.close()
                fh = None
        try:
            yield
        finally:
            if fh is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                fh.close()


def _read_raw():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as e:
        # A corrupt file is not "nothing is running" — say so out loud.
        logger.warning("build_state: unreadable state file (%s)", e)
        return None


def _quiet_budget(state):
    return STAGE_QUIET_BUDGET.get(state.get("stage"), STALE_AFTER_SECONDS)


def _is_stale(state, now=None):
    now = now or time.time()
    if now - (state.get("updated_at") or 0) > _quiet_budget(state):
        return True
    started = state.get("started_at") or 0
    return bool(started and now - started > MAX_BUILD_SECONDS)


def _owns(state, build_id):
    """Does `build_id` still own this record?

    None means "no claim" — legacy/unowned callers keep working. A mismatch
    means a superseded build is talking, and it must not touch the live one.
    """
    if not build_id or not state:
        return True
    current_id = state.get("build_id")
    return not current_id or current_id == build_id


# --- lifecycle -------------------------------------------------------------


def start(topic, course_uid=None, source=None, stage="preflight"):
    """Claim the build slot and record that a build has begun.

    Returns a build id, or None if a live build already owns the record.
    A None return means DO NOT proceed with a competing build: the previous
    unconditional overwrite meant a rejected second build replaced the running
    build's topic and reset its timer, and then the first build's finish()
    marked the second one complete.
    """
    build_id = uuid.uuid4().hex[:12]
    try:
        with _exclusive():
            existing = _read_raw()
            if existing and existing.get("active") and not _is_stale(existing):
                logger.warning(
                    "build_state.start refused: '%s' (%s) is still building",
                    existing.get("topic"), existing.get("build_id"))
                return None
            now = time.time()
            _atomic_write(STATE_PATH, {
                "active": True,
                "build_id": build_id,
                "topic": topic,
                "course_uid": course_uid,
                "source": source,          # "topic" | "book" | "upload"
                "stage": stage,
                "pct": 0,
                "started_at": now,
                "updated_at": now,
                "modules": 0,
                "concepts": 0,
                "hydrated": 0,
                "stubs": 0,
                "ok": None,
                "error": None,
                "messages": [],
            })
        return build_id
    except Exception as e:
        _record_failure("start", e)
        return None


def _mutate(where, build_id, fn):
    """Read-modify-write the live record under the lock."""
    try:
        with _exclusive():
            state = _read_raw()
            if not state or not _owns(state, build_id):
                return False
            fn(state)
            state["updated_at"] = time.time()
            _atomic_write(STATE_PATH, state)
            return True
    except Exception as e:
        _record_failure(where, e)
        return False


def update(build_id=None, **fields):
    """Merge fields into the running build's record."""
    return _mutate("update", build_id, lambda s: s.update(fields))


def stage(name, pct=None, build_id=None):
    """Record which phase of the pipeline is running.

    Also selects the quiet budget used by the staleness check, so a phase that
    reports rarely is not mistaken for a dead build.
    """
    def _apply(state):
        state["stage"] = name
        if pct is not None:
            state["pct"] = pct
    return _mutate("stage", build_id, _apply)


def heartbeat(build_id=None):
    """Touch the record without saying anything.

    For a phase that can legitimately go quiet for longer than its budget.
    """
    return _mutate("heartbeat", build_id, lambda s: None)


def note(message, cap=400, build_id=None):
    """Append a status line so a learner who returns can see what happened.

    Capped: a long build emits thousands of messages and this file is read on
    every page load.
    """
    text = str(message)

    def _apply(state):
        msgs = state.get("messages") or []
        msgs.append({"t": time.time(), "m": text[:300]})
        state["messages"] = msgs[-cap:]
        for prefix, field in _COUNTERS:
            if text.startswith(prefix):
                state[field] = (state.get(field) or 0) + 1
        for prefix, name in _STAGE_HINTS:
            if text.startswith(prefix) and state.get("stage") != name:
                state["stage"] = name
                break

    return _mutate("note", build_id, _apply)


def tee(callback, build_id=None):
    """Wrap a status callback so everything it carries also lands on disk.

    Every pipeline stage should be constructed with a teed callback. Wrapping
    only the SkeletonBuilder instance — which is what used to happen — left the
    auditor, the hydrator and the asset collector writing to the raw FSM
    callback, so the durable record covered the first ~5% of the build and
    nothing else.
    """
    def _teed(message):
        note(message, build_id=build_id)
        if callback:
            callback(message)
    return _teed


def finish(course_uid=None, error=None, ok=None, build_id=None):
    """Mark the build complete. Kept on disk so the result is still visible.

    `error` is only written when one is supplied. It used to be assigned
    unconditionally, so the single bare `finish()` call site overwrote any
    error already recorded with None and every failed build read as a success.
    """
    try:
        with _exclusive():
            state = _read_raw() or {}
            if not _owns(state, build_id):
                logger.warning(
                    "build_state.finish ignored: record belongs to '%s', not %s",
                    state.get("topic"), build_id)
                return False
            if error is not None:
                state["error"] = error
            resolved_ok = ok if ok is not None else (state.get("error") is None)
            state.update({
                "active": False,
                "ok": bool(resolved_ok),
                "stage": "done" if resolved_ok else "failed",
                "finished_at": time.time(),
                "updated_at": time.time(),
                "course_uid": course_uid or state.get("course_uid"),
            })
            _atomic_write(STATE_PATH, state)
            return True
    except Exception as e:
        _record_failure("finish", e)
        return False


def fail(error, course_uid=None, build_id=None):
    """Mark the build finished AND failed.

    Separate from finish() because the UI must be able to tell them apart:
    build-guard.js toasts "Your course is ready." on any transition out of
    active, which it also did when the build raised.
    """
    return finish(course_uid=course_uid, error=str(error) or "build failed",
                  ok=False, build_id=build_id)


# --- reading ---------------------------------------------------------------


def current():
    """The running build, or None.

    A stale record — no update for its stage's quiet budget — is reported as
    not running. A crashed build must not lock the interface permanently.
    """
    state = _read_raw()
    if not state:
        return None
    if not state.get("active"):
        return state           # finished; the caller decides whether to show it
    if not _is_stale(state):
        return state

    logger.warning("build_state: stale active build '%s', treating as dead",
                   state.get("topic"))
    reaped = {**state, "active": False, "stale": True, "ok": False,
              "stage": "failed",
              "error": state.get("error") or "build stopped reporting"}
    # Write the reaping back. current() used to return `stale` without
    # persisting it, so disk stayed active:True forever and every reader in
    # every process paid the same discovery again.
    try:
        with _exclusive():
            fresh = _read_raw()
            if fresh and fresh.get("active") and _is_stale(fresh):
                fresh.update({k: reaped[k] for k in
                              ("active", "stale", "ok", "stage", "error")})
                fresh["finished_at"] = time.time()
                _atomic_write(STATE_PATH, fresh)
    except Exception as e:
        _record_failure("current(reap)", e)
    return reaped


def is_building():
    state = current()
    return bool(state and state.get("active"))
