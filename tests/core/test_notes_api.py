"""The Session Notebook read path (services/rag/notes_api.py).

Notes have been write-only since schema v13; these tests pin down the first
reader. Three failure modes matter because each one looks like success:

  1. THE EMPTY NOTEBOOK. A fresh install has no notes anywhere. That must come
     back as a well-formed 200 with zero groups — the UI's "notes appear as
     you study" state — never a 500 and never a fabricated group.
  2. THE TWO STORES. Notes live in the session_notes table AND in the
     Markdown "## Session Notes" sections the FSM actually writes. A reader
     that consults only one silently hides the other's history.
  3. COMPACTION. A compacted row has no text but is still evidence a turn
     happened; dropping it would silently shorten a learner's history.

Fixture style mirrors tests/core/test_search.py: a fresh StorageManager on a
tmp_path, endpoints exercised through a real Flask test client.
"""

import os
import sqlite3
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.storage import StorageManager          # noqa: E402
from services.rag.notes_api import create_notes_blueprint   # noqa: E402


@pytest.fixture
def storage(tmp_path):
    return StorageManager(str(tmp_path))


@pytest.fixture
def client(storage):
    app = Flask(__name__)
    app.register_blueprint(create_notes_blueprint(storage))
    return app.test_client()


def _make_course(storage):
    """One course, one module/unit/lesson, two concepts, in a fixed order."""
    uid = storage.courses.create_course({
        "title": "Alpine Botany",
        "modules": [{
            "title": "Meadows", "uid": "mod_meadows",
            "units": [{
                "title": "Flowers", "uid": "unit_flowers",
                "lessons": [{
                    "title": "Edelweiss", "uid": "less_edelweiss",
                    "concepts": [
                        {"uid": "con_leaf", "title": "Leaf structure"},
                        {"uid": "con_root", "title": "Root systems"},
                    ],
                }],
            }],
        }],
    })
    return uid


def _insert_note(storage, course_uid, concept_uid, text, created_at,
                 role="tutor", grade=None, compacted=0):
    """Direct row insert so tests control created_at; the public writer
    (add_session_note) always stamps now()."""
    conn = sqlite3.connect(storage.db_path)
    conn.execute(
        "INSERT INTO session_notes (course_uid, concept_uid, role, text, "
        "grade, created_at, compacted) VALUES (?,?,?,?,?,?,?)",
        (course_uid, concept_uid, role, text, grade, created_at, compacted))
    conn.commit()
    conn.close()


def _write_markdown_notes(storage, course_uid, concept_uid, bullets):
    """A concept .md with a Session Notes section, bullets newest-first —
    exactly the file fsm_logic.append_session_note produces."""
    content_dir = os.path.join(storage.courses.courses_dir, course_uid, "content")
    os.makedirs(content_dir, exist_ok=True)
    lines = ["# Leaf structure", "", "Body text.", "", "## Session Notes"]
    lines.extend(bullets)
    with open(os.path.join(content_dir, f"{concept_uid}.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Empty and missing
# ---------------------------------------------------------------------------

class TestEmptyNotebook:

    def test_course_with_no_notes_is_a_well_formed_empty_200(self, client, storage):
        uid = _make_course(storage)
        resp = client.get(f"/api/courses/{uid}/notes")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["course_title"] == "Alpine Botany"
        assert data["total_notes"] == 0
        assert data["groups"] == []

    def test_unknown_course_is_404_not_an_empty_notebook(self, client):
        resp = client.get("/api/courses/course_nope1234/notes")
        assert resp.status_code == 404

    def test_empty_export_still_downloads(self, client, storage):
        uid = _make_course(storage)
        resp = client.get(f"/api/courses/{uid}/notes/export")
        assert resp.status_code == 200
        assert "attachment" in resp.headers["Content-Disposition"]
        assert "Alpine Botany" in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

class TestDatabaseNotes:

    def test_writer_and_reader_agree(self, client, storage):
        """The public add_session_note writer round-trips through the read
        surface — the contract the FSM will rely on when it gets wired."""
        uid = _make_course(storage)
        storage.courses.add_session_note(uid, "con_leaf", "tutor",
                                         "Asked about venation.", grade=3)
        data = client.get(f"/api/courses/{uid}/notes").get_json()
        assert data["total_notes"] == 1
        group = data["groups"][0]
        assert group["concept_uid"] == "con_leaf"
        assert group["concept_title"] == "Leaf structure"
        assert group["module_title"] == "Meadows"
        note = group["notes"][0]
        assert note["text"] == "Asked about venation."
        assert note["grade"] == 3
        assert note["source"] == "db"

    def test_groups_follow_course_order_and_notes_are_chronological(
            self, client, storage):
        uid = _make_course(storage)
        # Written out of order, and to the LATER concept first.
        _insert_note(storage, uid, "con_root", "root day 2", "2026-02-02T10:00:00")
        _insert_note(storage, uid, "con_leaf", "leaf note", "2026-03-01T10:00:00")
        _insert_note(storage, uid, "con_root", "root day 1", "2026-02-01T10:00:00")
        data = client.get(f"/api/courses/{uid}/notes").get_json()
        assert [g["concept_uid"] for g in data["groups"]] == ["con_leaf", "con_root"]
        root_notes = data["groups"][1]["notes"]
        assert [n["text"] for n in root_notes] == ["root day 1", "root day 2"]

    def test_compacted_row_is_kept_as_evidence_not_hidden(self, client, storage):
        uid = _make_course(storage)
        _insert_note(storage, uid, "con_leaf", None, "2025-01-01T09:00:00",
                     grade=4, compacted=1)
        data = client.get(f"/api/courses/{uid}/notes").get_json()
        note = data["groups"][0]["notes"][0]
        assert note["kind"] == "compacted"
        assert note["grade"] == 4
        assert note["text"] is None

    def test_note_for_a_concept_the_structure_lost_lands_in_other_notes(
            self, client, storage):
        """A rebuilt course drops concept uids; their notes must not vanish."""
        uid = _make_course(storage)
        _insert_note(storage, uid, "con_gone9999", "orphaned turn",
                     "2026-01-01T08:00:00")
        data = client.get(f"/api/courses/{uid}/notes").get_json()
        tail = data["groups"][-1]
        assert tail["concept_title"] == "Other notes"
        assert tail["notes"][0]["text"] == "orphaned turn"


# ---------------------------------------------------------------------------
# The Markdown sections — where real sessions have actually written
# ---------------------------------------------------------------------------

class TestMarkdownNotes:

    GRADED = ("- [2026-08-19 10:11:12] Question: What is venation? | "
              "Answer: The vein pattern... | Grade: 3 | Reasoning: Solid.")
    FREEFORM = "- [2026-08-18 09:00:00] Learner asked to slow down."

    def test_grading_bullet_parses_into_an_exchange(self, client, storage):
        uid = _make_course(storage)
        _write_markdown_notes(storage, uid, "con_leaf", [self.GRADED])
        data = client.get(f"/api/courses/{uid}/notes").get_json()
        note = data["groups"][0]["notes"][0]
        assert note["kind"] == "exchange"
        assert note["question"] == "What is venation?"
        assert note["answer"] == "The vein pattern..."
        assert note["grade"] == 3
        assert note["reasoning"] == "Solid."
        assert note["source"] == "markdown"

    def test_unrecognised_bullet_survives_as_free_text(self, client, storage):
        uid = _make_course(storage)
        _write_markdown_notes(storage, uid, "con_leaf", [self.FREEFORM])
        data = client.get(f"/api/courses/{uid}/notes").get_json()
        note = data["groups"][0]["notes"][0]
        assert note["kind"] == "note"
        assert note["text"] == "Learner asked to slow down."

    def test_file_order_is_newest_first_but_the_view_is_chronological(
            self, client, storage):
        # append_session_note inserts under the header, newest first; the
        # notebook must not inherit that.
        uid = _make_course(storage)
        _write_markdown_notes(storage, uid, "con_leaf",
                              [self.GRADED, self.FREEFORM])
        data = client.get(f"/api/courses/{uid}/notes").get_json()
        kinds = [n["kind"] for n in data["groups"][0]["notes"]]
        assert kinds == ["note", "exchange"]  # 08-18 before 08-19

    def test_db_and_markdown_merge_into_one_timeline(self, client, storage):
        uid = _make_course(storage)
        _write_markdown_notes(storage, uid, "con_leaf", [self.GRADED])
        _insert_note(storage, uid, "con_leaf", "from the table",
                     "2026-08-20T12:00:00")
        data = client.get(f"/api/courses/{uid}/notes").get_json()
        notes = data["groups"][0]["notes"]
        assert [n["source"] for n in notes] == ["markdown", "db"]
        assert data["total_notes"] == 2


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class TestExport:

    def test_export_is_markdown_with_an_attachment_filename(self, client, storage):
        uid = _make_course(storage)
        _write_markdown_notes(storage, uid, "con_leaf",
                              [TestMarkdownNotes.GRADED])
        resp = client.get(f"/api/courses/{uid}/notes/export")
        assert resp.status_code == 200
        assert resp.mimetype == "text/markdown"
        assert resp.headers["Content-Disposition"] == \
            'attachment; filename="alpine-botany-notebook.md"'
        body = resp.get_data(as_text=True)
        assert body.startswith("# Alpine Botany — Session Notebook")
        assert "## Leaf structure" in body
        assert "Q: What is venation?" in body
        assert "Your answer: The vein pattern..." in body
        assert "Grade: 3/4" in body

    def test_compacted_rows_are_named_in_the_export(self, client, storage):
        uid = _make_course(storage)
        _insert_note(storage, uid, "con_leaf", None, "2025-01-01T09:00:00",
                     grade=4, compacted=1)
        body = client.get(f"/api/courses/{uid}/notes/export") \
            .get_data(as_text=True)
        assert "compacted" in body
        assert "grade 4/4 retained" in body


# ---------------------------------------------------------------------------
# Completion (the certificate's read model)
# ---------------------------------------------------------------------------

class TestCompletion:

    def test_unfinished_course_reports_no_completion_date(self, client, storage):
        uid = _make_course(storage)
        storage.progress.mark_completed("con_leaf", uid)
        data = client.get(f"/api/courses/{uid}/completion").get_json()
        assert data["complete"] is False
        assert data["completed_concepts"] == 1
        assert data["total_concepts"] == 2
        assert data["completion_date"] is None

    def test_finished_course_reports_complete_with_a_date(self, client, storage):
        uid = _make_course(storage)
        storage.progress.mark_completed("con_leaf", uid)
        storage.progress.mark_completed("con_root", uid)
        data = client.get(f"/api/courses/{uid}/completion").get_json()
        assert data["complete"] is True
        assert data["completed_concepts"] == 2
        assert data["completion_date"]  # newest updated_at among the rows

    def test_learner_name_comes_from_the_profile_when_present(self, client, storage):
        uid = _make_course(storage)
        conn = sqlite3.connect(storage.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS user_profile "
                     "(key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER)")
        conn.execute("INSERT INTO user_profile (key, value) VALUES "
                     "('display_name', 'Brennan')")
        conn.commit()
        conn.close()
        data = client.get(f"/api/courses/{uid}/completion").get_json()
        assert data["learner_name"] == "Brennan"

    def test_no_profile_table_means_blank_name_not_an_error(self, client, storage):
        uid = _make_course(storage)
        data = client.get(f"/api/courses/{uid}/completion").get_json()
        assert data["learner_name"] == ""
