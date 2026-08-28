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


# ---------------------------------------------------------------------------
# EVERY STAGE NAME HANDED TO build_state.start() MUST HAVE A QUIET BUDGET.
#
# _quiet_budget falls back to STALE_AFTER_SECONDS for an unknown stage, so a
# name the table does not carry is not a crash — it is a quieter, shorter
# deadline that nothing reports. resume_build passes stage="hydration" and the
# table had only "hydrate", one letter apart.
#
# Scoped to the actual callers on purpose. An earlier version of this test
# scanned every `stage=` literal under services/, which swept in `data-stage`
# from build.html — the build PAGE's checklist, a different namespace that
# never reaches this module — and made one real defect look like four.
# ---------------------------------------------------------------------------

def _stage_args_passed_to_build_state_start():
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2] / "services"
    found = set()
    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name != "start":
                continue
            owner = getattr(getattr(fn, "value", None), "id", "")
            if "build_state" not in owner and "_state" not in owner:
                continue
            for kw in node.keywords:
                if kw.arg == "stage" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    found.add(kw.value.value)
    return found


def test_every_stage_passed_to_build_state_has_a_quiet_budget():
    from services.common.build_state import STAGE_QUIET_BUDGET
    passed = _stage_args_passed_to_build_state_start()
    assert passed, "found no build_state.start(stage=...) calls — check the scan"
    missing = sorted(passed - set(STAGE_QUIET_BUDGET))
    assert not missing, (
        "these stage names reach build_state.start() but have no quiet "
        f"budget, so they silently take the default deadline: {missing}")


def test_the_budget_lookup_actually_uses_those_keys():
    """Guards the mechanism, not just the table: renaming _quiet_budget's
    lookup key would make every entry decorative."""
    from services.common import build_state
    assert build_state._quiet_budget({"stage": "hydration"}) == 20 * 60
    assert build_state._quiet_budget({"stage": "hydrate"}) == 20 * 60
    assert build_state._quiet_budget({"stage": "no_such_stage"}) == \
        build_state.STALE_AFTER_SECONDS


# ---------------------------------------------------------------------------
# HYDRATION MUST TELL THE BUILD RECORD IT IS ALIVE.
#
# build_state marks a build dead when `updated_at` has not moved inside the
# stage's quiet budget. SkeletonBuilder heartbeats through _record_progress;
# ContentHydrator had no equivalent, and the create pipeline calls
# build_state.stage() once entering hydration and again at finalize — so a
# 136-concept hydration ran for hours with updated_at frozen at its start.
#
# A long build was therefore not at RISK of being reaped, it was guaranteed to
# be. Measured on a live resume: updated_at == started_at exactly, while the
# hydrator was mid-concept and still writing files, and web-ui logged
# "build_state: stale active build 'HTTP status codes', treating as dead".
# ---------------------------------------------------------------------------

def test_the_hydrator_heartbeats_every_status_message(monkeypatch):
    from services.core.course_builder import ContentHydrator
    from services.common import build_state

    beats = []
    monkeypatch.setattr(build_state, "note", lambda m, **kw: beats.append(m))

    seen = []
    cb = ContentHydrator._with_heartbeat(lambda msg: seen.append(msg))
    cb("STRUCT:HYDRATING:uid:START:A concept")

    assert beats == ["STRUCT:HYDRATING:uid:START:A concept"], \
        "hydration status did not reach the durable build record"
    assert seen == ["STRUCT:HYDRATING:uid:START:A concept"], \
        "the caller's own callback must still run"


def test_a_heartbeat_failure_cannot_break_a_build(monkeypatch):
    from services.core.course_builder import ContentHydrator
    from services.common import build_state

    def _boom(*a, **kw):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(build_state, "note", _boom)

    seen = []
    cb = ContentHydrator._with_heartbeat(lambda msg: seen.append(msg))
    cb("anything")            # must not raise
    assert seen == ["anything"]


def test_the_hydrator_heartbeats_with_no_caller_callback(monkeypatch):
    """Resume passes one; the create path may not. Neither may crash."""
    from services.core.course_builder import ContentHydrator
    from services.common import build_state

    beats = []
    monkeypatch.setattr(build_state, "note", lambda m, **kw: beats.append(m))
    ContentHydrator._with_heartbeat(None)("solo")
    assert beats == ["solo"]


def test_note_actually_advances_updated_at(tmp_path, monkeypatch):
    """The heartbeat is only a heartbeat if it moves the field the staleness
    check reads."""
    import time
    from services.common import build_state

    monkeypatch.setattr(build_state, "STATE_PATH", str(tmp_path / "b.json"))
    monkeypatch.setattr(build_state, "LOCK_PATH", str(tmp_path / "b.json.lock"))
    build_state.start("A topic", course_uid="course_x", stage="hydration")
    first = (build_state.current() or {}).get("updated_at")
    time.sleep(0.02)
    build_state.note("still going")
    second = (build_state.current() or {}).get("updated_at")

    assert first and second and second > first, \
        "note() did not advance updated_at; the staleness check reads that field"


# ---------------------------------------------------------------------------
# THE SKELETON IS THE FIRST PHASE, NOT THE WHOLE BUILD.
#
# SkeletonBuilder.build() called build_state.start() and then, in its finally,
# build_state.finish() — closing the record for the entire pipeline the moment
# the structure was written, with audit, hydration, assets, the item bank and
# finalize still to come.
#
# fsm_logic ends that record and identifies it by `getattr(sb, "_build_id")`,
# a contract its own comment spells out. The attribute was never set, so the
# FSM always received None.
#
# Measured on the wizard build: build_state read active=False, ok=True,
# stage="audit" while core-logic was running "PASS 2: LLM quality review".
# ---------------------------------------------------------------------------

def test_the_skeleton_builder_keeps_the_id_the_fsm_reads_back():
    import inspect
    from services.core.course_builder import SkeletonBuilder

    builder = inspect.getsource(SkeletonBuilder)
    assert "self._build_id = build_state.start(" in builder, (
        "SkeletonBuilder discards the build id, so fsm_logic cannot identify "
        "the record it is meant to close")

    # Read as text: fsm_logic imports fsrs_engine by a container-relative path
    # and cannot be imported on the host.
    import pathlib
    fsm = (pathlib.Path(__file__).resolve().parents[2]
           / "services" / "core" / "fsm_logic.py").read_text()
    assert 'getattr(sb, "_build_id", None)' in fsm, (
        "the FSM no longer reads the id back; re-derive who owns the record")


def test_the_skeleton_builder_does_not_close_the_record():
    """Closing it here reported every create-path build as finished as soon as
    its structure existed."""
    import inspect
    import re
    from services.core.course_builder import SkeletonBuilder

    src = inspect.getsource(SkeletonBuilder.build)
    code = re.sub(r'"""[\s\S]*?"""', " ", src)
    code = re.sub(r"#[^\n]*", " ", code)
    assert "build_state.finish" not in code, (
        "the skeleton phase closes the whole build's durable record again")
