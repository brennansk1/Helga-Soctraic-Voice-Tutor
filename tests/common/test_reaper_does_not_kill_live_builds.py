"""A long build is not a dead build.

The stale-build reaper measures age from `created_at` with a one-hour cutoff,
so a course created hours ago is permanently past it and the reap repeats every
300 seconds for the entire life of a build. Its only guard was
build_state.current(), and resume_build never claimed the slot — so `live_uid`
was None, the guard never matched, and the reaper stamped
status="failed", error="Course creation timed out (>1 hour)" on a course that
was actively hydrating.

Measured 2026-08-25: four reaps in twelve minutes, one of them fourteen seconds
after the hydration it was reaping had started. The course card then alternated
between "Building…" and "failed" on a five-minute cycle.

Two independent defences now: resume claims the slot, and a concept written
recently is treated as proof of life regardless of any bookkeeping.
"""
import os
import time

import pytest


@pytest.fixture()
def ops(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib

    from services.common import background_ops
    return importlib.reload(background_ops), tmp_path


def _concept(root, uid, age_seconds):
    d = root / "courses" / uid / "content"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "con_a.md"
    f.write_text("# A concept\n\nreal content\n")
    when = time.time() - age_seconds
    os.utime(f, (when, when))
    return f


def test_a_course_writing_concepts_is_not_stale(ops):
    mod, root = ops
    _concept(root, "course_live", age_seconds=60)
    o = mod.BackgroundOperations(storage_manager=None)
    assert o._wrote_recently("course_live") is True


def test_a_course_that_stopped_writing_is(ops):
    mod, root = ops
    _concept(root, "course_dead", age_seconds=6 * 60 * 60)
    o = mod.BackgroundOperations(storage_manager=None)
    assert o._wrote_recently("course_dead") is False


def test_a_course_with_no_content_yet_is_not_protected_by_this_check(ops):
    """Pre-hydration builds are covered by the build slot, not by this."""
    mod, root = ops
    (root / "courses" / "course_new").mkdir(parents=True)
    o = mod.BackgroundOperations(storage_manager=None)
    assert o._wrote_recently("course_new") is False


def test_an_unreadable_directory_is_treated_as_alive(ops):
    """Unknown is not 'dead'. Nothing unreadable should be the reason a course
    gets marked failed."""
    mod, _root = ops
    o = mod.BackgroundOperations(storage_manager=None)
    assert o._wrote_recently(None) is True


def test_the_reaper_consults_it(ops):
    mod, _root = ops
    import inspect
    src = inspect.getsource(mod.BackgroundOperations)
    i = src.find("Cleaning stale course")
    assert i > 0, "the reaper moved"
    assert "_wrote_recently" in src[:i], \
        "the reaper marks a course failed without checking for recent writes"


def test_resume_claims_the_build_slot():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "services", "rag", "librarian.py"),
              encoding="utf-8") as f:
        src = f.read()
    i = src.find("def resume_build")
    body = src[i:i + 8000]  # the release sits in the thread's finally, further down
    assert "build_state.start(" in body, \
        "resume does not claim the slot, so the reaper's guard cannot match"
    assert "build_state.finish(" in body, \
        "resume does not release the slot, so the next build is refused"
