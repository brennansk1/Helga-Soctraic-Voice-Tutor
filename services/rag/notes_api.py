"""The Session Notebook — the read surface for a learner's own session notes.

REGISTER WITH (in librarian.py, next to the exam blueprint mount):

    from services.rag.notes_api import create_notes_blueprint
    app.register_blueprint(create_notes_blueprint(storage))

Session notes have been write-only since they existed. They live in TWO places,
and the notebook reads both, because that is where the notes really are:

  1. The `session_notes` table (schema v13, services/common/storage.py). This
     is the intended store — append-only rows of (role, text, grade,
     created_at) with a compaction boundary designed in from the start. A
     compacted row keeps its grade and timestamp but drops the raw text; that
     row is still evidence a turn happened and is shown as such, never hidden.
     As of this writing no production code path calls
     `CourseStore.add_session_note`, so on most installs this table is empty.

  2. The "## Session Notes" section of each concept's Markdown file, written
     by `fsm_logic.append_session_note` on every graded exchange, one bullet
     per turn, newest first:

         - [2026-08-19 10:11:12] Question: ... | Answer: ... | Grade: 3 | Reasoning: ...

     These are the notes real sessions have actually produced. The parser
     below recovers the structure (question / answer / grade / reasoning) when
     a bullet matches that shape and falls back to the raw line when it does
     not, so a future free-form note is displayed rather than dropped.

Reading both and merging is deliberate: a view that read only the table would
show an empty notebook to a learner with months of session history on disk,
and a view that read only the Markdown would go dark the day the table writer
is finally wired. Either failure looks like "your notes are gone".

Also mounted here: `/api/courses/<uid>/completion`, the small read model the
printable certificate needs (course title, concept counts, completion date,
learner name from the profile). It shares this blueprint because it is the
same kind of surface — a read-only view over data other code already writes.
"""

import logging
import os
import re
import sqlite3
from datetime import datetime

from flask import Blueprint, Response, jsonify

logger = logging.getLogger(__name__)

# One bullet as append_session_note writes it. The timestamp bracket is fixed;
# the note text is anything after it.
_BULLET_RE = re.compile(r"^\s*-\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)$")

# The one note shape the FSM produces today (its grading log line). Anchored on
# the literal separators so a question containing a lone "|" still parses; a
# note that does not match stays a free-text note rather than erroring.
_EXCHANGE_RE = re.compile(
    r"^Question:\s*(?P<question>.*?)\s*\|\s*Answer:\s*(?P<answer>.*?)\s*"
    r"\|\s*Grade:\s*(?P<grade>\d+)\s*\|\s*Reasoning:\s*(?P<reasoning>.*)$",
    re.S,
)


def _parse_markdown_notes(md_text):
    """Bullets under '## Session Notes' -> note dicts. Order restored by sort.

    The FSM inserts new bullets directly under the header, so the file is
    newest-first; callers sort on created_at and must not rely on file order.
    """
    notes = []
    in_section = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = "session notes" in stripped.lower()
            continue
        if not in_section:
            continue
        m = _BULLET_RE.match(line)
        if not m:
            continue
        ts, body = m.group(1), m.group(2).strip()
        # ISO-normalise so DB rows (datetime.isoformat) and Markdown rows
        # ("%Y-%m-%d %H:%M:%S") sort together on plain string comparison.
        created_at = ts.replace(" ", "T")
        ex = _EXCHANGE_RE.match(body)
        if ex:
            notes.append({
                "kind": "exchange",
                "created_at": created_at,
                "question": ex.group("question"),
                "answer": ex.group("answer"),
                "grade": int(ex.group("grade")),
                "reasoning": ex.group("reasoning").strip(),
                "source": "markdown",
            })
        else:
            notes.append({
                "kind": "note",
                "created_at": created_at,
                "text": body,
                "role": None,
                "grade": None,
                "source": "markdown",
            })
    return notes


def _concept_markdown(courses_dir, course_uid, concept_uid):
    """The concept's .md straight from disk, or "".

    Deliberately NOT get_concept_content(): that reads DB-first (v15), and
    session notes are appended to the FILE only — a concept with a DB body
    would hide every note behind it. Both historical subdirs are checked, the
    same pair get_concept_content falls back through.
    """
    for subdir in ("content", "topics"):
        path = os.path.join(courses_dir, course_uid, subdir, f"{concept_uid}.md")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return f.read()
            except OSError as e:
                logger.warning(f"notebook: unreadable concept file {path}: {e}")
    return ""


def _db_notes(db_path, course_uid):
    """session_notes rows for a course, keyed by concept_uid.

    A short-lived read connection of its own: this blueprint has no business
    holding one open, and the table may simply not exist on a database that
    predates schema v13 — that is an empty notebook, not an error.
    """
    by_concept = {}
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT concept_uid, role, text, grade, created_at, compacted "
                "FROM session_notes WHERE course_uid = ? ORDER BY created_at",
                (course_uid,)).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return by_concept  # pre-v13 database: no table, no notes
    except Exception as e:
        logger.warning(f"notebook: session_notes read failed: {e}")
        return by_concept

    for r in rows:
        note = {
            "kind": "compacted" if r["compacted"] else "note",
            "created_at": r["created_at"] or "",
            "text": r["text"],
            "role": r["role"],
            "grade": r["grade"],
            "source": "db",
        }
        by_concept.setdefault(r["concept_uid"] or "", []).append(note)
    return by_concept


def _collect_notes(storage, course_uid):
    """Everything the notebook shows, or None when the course does not exist.

    Groups are the course's concepts IN COURSE ORDER, kept only when they have
    notes — a notebook is what you wrote, not the syllabus restated. Notes for
    concept uids the structure no longer contains (a rebuilt course, a deleted
    lesson) land in a trailing "Other notes" group instead of vanishing.
    """
    course = storage.courses.get_course(course_uid)
    if not course:
        return None

    flat = storage.courses.get_flat_concepts(course_uid)
    db_notes = _db_notes(storage.db_path, course_uid)

    groups = []
    seen = set()
    for c in flat:
        seen.add(c["uid"])
        notes = list(db_notes.get(c["uid"], []))
        md = _concept_markdown(storage.courses.courses_dir, course_uid, c["uid"])
        if md:
            notes.extend(_parse_markdown_notes(md))
        if not notes:
            continue
        notes.sort(key=lambda n: n.get("created_at") or "")
        groups.append({
            "concept_uid": c["uid"],
            "concept_title": c["title"],
            "module_title": c.get("module_title", ""),
            "lesson_title": c.get("lesson_title", ""),
            "notes": notes,
        })

    orphaned = []
    for uid, notes in db_notes.items():
        if uid not in seen:
            orphaned.extend(notes)
    if orphaned:
        orphaned.sort(key=lambda n: n.get("created_at") or "")
        groups.append({
            "concept_uid": "",
            "concept_title": "Other notes",
            "module_title": "",
            "lesson_title": "",
            "notes": orphaned,
        })

    return {
        "course_uid": course_uid,
        "course_title": course.get("title", ""),
        "total_notes": sum(len(g["notes"]) for g in groups),
        "groups": groups,
    }


def _display_ts(iso_ts):
    """ISO timestamp -> 'YYYY-MM-DD HH:MM' for the export; passthrough if odd."""
    if not iso_ts:
        return "undated"
    return iso_ts.replace("T", " ")[:16]


def _export_markdown(data):
    """The notebook as one Markdown document — 'download my notes'."""
    lines = [
        f"# {data['course_title'] or data['course_uid']} — Session Notebook",
        "",
        f"Exported {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"{data['total_notes']} note{'s' if data['total_notes'] != 1 else ''}",
        "",
    ]
    for g in data["groups"]:
        crumb = " › ".join(p for p in (g["module_title"], g["lesson_title"]) if p)
        lines.append(f"## {g['concept_title']}")
        if crumb:
            lines.append(f"*{crumb}*")
        lines.append("")
        for n in g["notes"]:
            ts = _display_ts(n.get("created_at"))
            if n["kind"] == "exchange":
                lines.append(f"- **{ts}** — Q: {n['question']}")
                lines.append(f"  - Your answer: {n['answer']}")
                lines.append(f"  - Grade: {n['grade']}/4")
                if n.get("reasoning") and n["reasoning"] != "N/A":
                    lines.append(f"  - Tutor's note: {n['reasoning']}")
            elif n["kind"] == "compacted":
                grade = n.get("grade")
                kept = f"grade {grade}/4 retained" if grade is not None \
                    else "grade and timestamp retained"
                lines.append(f"- **{ts}** — older note compacted ({kept})")
            else:
                role = f"{n['role']}: " if n.get("role") else ""
                lines.append(f"- **{ts}** — {role}{n.get('text') or ''}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _safe_filename(title, course_uid):
    """A filename from the course title; the uid when the title yields nothing."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title or "").strip("-").lower()
    return f"{slug or course_uid}-notebook.md"


def create_notes_blueprint(storage):
    """Flask blueprint factory — mounted on the RAG process beside the exam
    engine; shares the StorageManager, adds no new container."""
    bp = Blueprint("notes", __name__)

    @bp.route("/api/courses/<course_uid>/notes", methods=["GET"])
    def course_notes(course_uid):
        try:
            data = _collect_notes(storage, course_uid)
        except Exception as e:
            logger.error(f"notebook read failed for {course_uid}: {e}",
                         exc_info=True)
            return jsonify({"error": "notebook read failed"}), 500
        if data is None:
            return jsonify({"error": "Course not found"}), 404
        return jsonify(data)

    @bp.route("/api/courses/<course_uid>/notes/export", methods=["GET"])
    def course_notes_export(course_uid):
        try:
            data = _collect_notes(storage, course_uid)
        except Exception as e:
            logger.error(f"notebook export failed for {course_uid}: {e}",
                         exc_info=True)
            return jsonify({"error": "notebook export failed"}), 500
        if data is None:
            return jsonify({"error": "Course not found"}), 404
        md = _export_markdown(data)
        filename = _safe_filename(data["course_title"], course_uid)
        return Response(
            md,
            mimetype="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @bp.route("/api/courses/<course_uid>/completion", methods=["GET"])
    def course_completion(course_uid):
        """The certificate's read model: is this course finished, and by whom.

        completion_date is the newest updated_at among completed concepts —
        the moment the last one was closed out — and only reported when the
        course is actually complete, so a half-done course cannot leak a
        plausible-looking date onto a certificate.
        """
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "Course not found"}), 404

        flat = storage.courses.get_flat_concepts(course_uid)
        total = len(flat)
        concept_uids = {c["uid"] for c in flat}
        completed_rows = [
            p for p in storage.progress.get_course_progress(course_uid)
            if p.get("status") == "completed" and p.get("concept_uid") in concept_uids
        ]
        completed = len(completed_rows)
        complete = total > 0 and completed >= total

        completion_date = None
        if complete:
            dates = [p.get("updated_at") or "" for p in completed_rows]
            completion_date = max(dates) if any(dates) else None

        # The learner's own name, from the same user_profile row Settings
        # edits. Missing table or blank value both mean "no name on file" —
        # the certificate leaves a signature line instead of inventing one.
        learner_name = ""
        try:
            conn = sqlite3.connect(storage.db_path, timeout=30)
            try:
                row = conn.execute(
                    "SELECT value FROM user_profile WHERE key = 'display_name'"
                ).fetchone()
                learner_name = (row[0] or "").strip() if row else ""
            finally:
                conn.close()
        except sqlite3.OperationalError:
            pass

        return jsonify({
            "course_uid": course_uid,
            "course_title": course.get("title", ""),
            "total_concepts": total,
            "completed_concepts": completed,
            "complete": complete,
            "completion_date": completion_date,
            "learner_name": learner_name,
        })

    return bp
