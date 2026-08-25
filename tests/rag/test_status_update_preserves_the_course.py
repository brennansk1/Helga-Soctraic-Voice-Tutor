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
