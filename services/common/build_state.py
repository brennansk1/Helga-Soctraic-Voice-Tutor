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

Everything here is best-effort. A failure to record progress must never break
a build — the writes are guarded and the reader treats a missing or corrupt
file as "nothing is running".
"""

import json
import logging
import os
import tempfile
import time

logger = logging.getLogger(__name__)

DATA_ROOT = os.getenv("DATA_ROOT", "/app/data")
STATE_PATH = os.path.join(DATA_ROOT, "build_state.json")

# A build that has not reported for this long is treated as dead. Without it a
# crashed build would lock the UI forever, which is worse than the problem the
# lock solves.
STALE_AFTER_SECONDS = 15 * 60


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


def start(topic, course_uid=None, source=None):
    """Record that a build has begun."""
    try:
        _atomic_write(STATE_PATH, {
            "active": True,
            "topic": topic,
            "course_uid": course_uid,
            "source": source,          # "topic" | "book" | "upload"
            "stage": "preflight",
            "started_at": time.time(),
            "updated_at": time.time(),
            "modules": 0,
            "concepts": 0,
            "messages": [],
        })
    except Exception as e:
        logger.debug(f"build_state.start failed: {e}")


def update(**fields):
    """Merge fields into the running build's record."""
    try:
        state = _read_raw()
        if not state:
            return
        state.update(fields)
        state["updated_at"] = time.time()
        _atomic_write(STATE_PATH, state)
    except Exception as e:
        logger.debug(f"build_state.update failed: {e}")


def note(message, cap=400):
    """Append a status line so a learner who returns can see what happened.

    Capped: a long build emits thousands of messages and this file is read on
    every page load.
    """
    try:
        state = _read_raw()
        if not state:
            return
        msgs = state.get("messages") or []
        msgs.append({"t": time.time(), "m": str(message)[:300]})
        state["messages"] = msgs[-cap:]
        state["updated_at"] = time.time()
        _atomic_write(STATE_PATH, state)
    except Exception as e:
        logger.debug(f"build_state.note failed: {e}")


def finish(course_uid=None, error=None):
    """Mark the build complete. Kept on disk so the result is still visible."""
    try:
        state = _read_raw() or {}
        state.update({
            "active": False,
            "finished_at": time.time(),
            "course_uid": course_uid or state.get("course_uid"),
            "error": error,
        })
        _atomic_write(STATE_PATH, state)
    except Exception as e:
        logger.debug(f"build_state.finish failed: {e}")


def _read_raw():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def current():
    """The running build, or None.

    A stale record — no update for STALE_AFTER_SECONDS — is reported as not
    running. A crashed build must not lock the interface permanently.
    """
    state = _read_raw()
    if not state:
        return None
    if not state.get("active"):
        return state           # finished; the caller decides whether to show it
    if time.time() - (state.get("updated_at") or 0) > STALE_AFTER_SECONDS:
        logger.warning("build_state: stale active build, treating as dead")
        return {**state, "active": False, "stale": True}
    return state


def is_building():
    state = current()
    return bool(state and state.get("active"))
