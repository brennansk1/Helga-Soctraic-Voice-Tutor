"""update_course OVERWRITES; a status change must read-modify-write.

While fixing a course that advertised "failed" during an active resume, the
obvious call — update_course(uid, {"status": "building"}) — would have
replaced structure.json with a three-key stub and destroyed every module in
the course. The docstring says "Overwrite course structure.json"; the name
does not.

This pins the shape of the call, because the destructive version looks more
natural than the correct one.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def test_no_caller_passes_a_bare_status_dict():
    """A literal dict with only status/uid keys means the rest is being lost."""
    offenders = []
    for rel in (("services", "rag", "librarian.py"),
                ("services", "core", "fsm_logic.py"),
                ("services", "rag", "pipeline_api.py")):
        src = _read(*rel)
        for m in re.finditer(r"update_course\(\s*[^,]+,\s*\{([^}]*)\}", src):
            keys = set(re.findall(r'"(\w+)"\s*:', m.group(1)))
            if keys and keys <= {"status", "uid", "updated_at"}:
                offenders.append(f"{rel[-1]}: update_course(..., {{{m.group(1)[:60]}}})")
    assert not offenders, (
        "update_course overwrites the whole structure; these calls would "
        "replace the course with a stub:\n  " + "\n  ".join(offenders))


def test_the_resume_path_reads_before_it_writes():
    src = _read("services", "rag", "librarian.py")
    i = src.find("def resume_build")
    body = src[i:i + 8000]
    # The CALL, not the word — the comment above it also says update_course.
    j = body.find("storage.courses.update_course(")
    assert j > 0, "resume no longer records that it is building"
    assert "get_course" in body[max(0, j - 400):j], \
        "resume writes a status without reading the course first"


def test_every_handler_that_spawns_a_status_worker_binds_its_owner():
    """_update_status stamps messages with _status_owner(); off the request
    thread that falls back to a module global — "whatever the previous request
    left behind". web-ui emits to room student:<owner>, so an unbound worker
    publishes its whole progress stream to the wrong room.

    resume_build spawned a thread and never bound. It went unnoticed because
    this machine has one student and the global holds the default; a second
    profile would have sent one learner's build progress to another's screen,
    or to nobody.
    """
    import ast
    import inspect
    from services.rag import librarian

    tree = ast.parse(inspect.getsource(librarian))
    offenders = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        body = ast.dump(fn)
        spawns = "Thread" in body or "ThreadPoolExecutor" in body
        emits = "_update_status" in body
        if not (spawns and emits):
            continue
        if "_bind_status_owner" not in body:
            offenders.append(fn.name)
    assert not offenders, (
        "these handlers spawn a worker that emits progress without pinning the "
        f"owner first: {offenders}")
