"""The search index's relationship with the truth on disk.

`concept_fts` was populated in exactly two places: lazily, when it was found
EMPTY, and by a full rebuild at the end of a course build. Two consequences,
both silent:

  * Content rewritten between builds — by the tutor's `update_topic_context`,
    by session notes, by the asset collector's `## Visual Aids` write — stayed
    searchable only under its old text.
  * A deleted course's concepts kept answering searches, because
    `delete_course`'s cascade list (whose stated purpose is that nothing is
    missing from it) did not include the index.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from services.common.storage import StorageManager


def _fts5_available():
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        return True
    except sqlite3.OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _fts5_available(), reason="FTS5 not compiled into SQLite"
)


@pytest.fixture
def storage(tmp_path):
    return StorageManager(str(tmp_path))


def _make_course(storage, title, concepts):
    uid = storage.courses.create_course({
        "title": title,
        "modules": [{
            "title": "M1", "uid": f"mod_{title}",
            "units": [{
                "title": "U1", "uid": f"unit_{title}",
                "lessons": [{
                    "title": "L1", "uid": f"less_{title}",
                    "concepts": [{"uid": cu, "title": ct} for cu, ct, _ in concepts],
                }],
            }],
        }],
    })
    for cu, _ct, body in concepts:
        storage.courses.save_concept_content(uid, cu, body)
    return uid


class TestWritesAreIndexed:
    def test_a_saved_concept_is_immediately_searchable(self, storage):
        _make_course(storage, "Bio", [("c1", "Mitosis", "Chromosomes condense.")])
        hits = storage.search.search("chromosomes")
        assert [h["concept_uid"] for h in hits] == ["c1"]

    def test_a_second_course_is_searchable_without_a_rebuild(self, storage):
        """The lazy path only fires when the index is EMPTY. Once course one
        populated it, course two was invisible until something else forced a
        full rebuild."""
        _make_course(storage, "Bio", [("c1", "Mitosis", "Chromosomes condense.")])
        storage.search.search("chromosomes")           # populate
        _make_course(storage, "Chem", [("c2", "Moles", "Avogadro's number.")])
        hits = storage.search.search("avogadro")
        assert [h["concept_uid"] for h in hits] == ["c2"]

    def test_a_rewrite_replaces_the_old_text(self, storage):
        uid = _make_course(storage, "Bio", [("c1", "Mitosis", "Chromosomes condense.")])
        storage.courses.save_concept_content(uid, "c1", "Spindle fibres attach.")
        assert storage.search.search("chromosomes") == []
        assert [h["concept_uid"] for h in storage.search.search("spindle")] == ["c1"]

    def test_repeated_rewrites_do_not_duplicate_the_row(self, storage):
        """concept_fts has no UNIQUE constraint, so INSERT OR REPLACE appends
        instead of replacing — a concept edited five times answers five times."""
        uid = _make_course(storage, "Bio", [("c1", "Mitosis", "Anaphase begins.")])
        for i in range(5):
            storage.courses.save_concept_content(uid, "c1", f"Anaphase begins, take {i}.")
        assert len(storage.search.search("anaphase")) == 1

    def test_index_failure_never_costs_the_content_write(self, storage):
        uid = _make_course(storage, "Bio", [("c1", "Mitosis", "Anaphase.")])

        def explode(*_a, **_k):
            raise sqlite3.OperationalError("index is on fire")

        storage.courses.on_content_saved = explode
        storage.courses.save_concept_content(uid, "c1", "Telophase.")
        assert "Telophase" in storage.courses.get_concept_content(uid, "c1")


class TestDeletionCascades:
    def test_a_deleted_course_stops_answering_searches(self, storage):
        uid = _make_course(storage, "Bio", [("c1", "Mitosis", "Chromosomes condense.")])
        assert storage.search.search("chromosomes")
        storage.courses.delete_course(uid)
        assert storage.search.search("chromosomes") == []

    def test_other_courses_survive_the_delete(self, storage):
        bio = _make_course(storage, "Bio", [("c1", "Mitosis", "Chromosomes condense.")])
        _make_course(storage, "Chem", [("c2", "Moles", "Avogadro's number.")])
        storage.courses.delete_course(bio)
        assert [h["concept_uid"] for h in storage.search.search("avogadro")] == ["c2"]

    def test_hydration_provenance_is_cascaded(self, storage):
        """The licensing record of content that no longer exists."""
        uid = _make_course(storage, "Bio", [("c1", "Mitosis", "Chromosomes.")])
        conn = sqlite3.connect(storage.db_path)
        conn.execute(
            "INSERT INTO hydration_provenance (course_uid, concept_uid, sources, model) "
            "VALUES (?,?,?,?)", (uid, "c1", "[]", "test-model"))
        conn.commit()
        conn.close()

        storage.courses.delete_course(uid)

        conn = sqlite3.connect(storage.db_path)
        rows = conn.execute(
            "SELECT COUNT(*) FROM hydration_provenance WHERE course_uid=?", (uid,)
        ).fetchone()[0]
        conn.close()
        assert rows == 0


class TestFlatConceptFields:
    def test_quality_signals_reach_the_syllabus_queue(self, storage):
        """`llm_fallback` and `source_confidence` are written into
        structure.json and were dropped by get_flat_concepts, so the FSM taught
        a generated stub with the same confidence as a well-sourced concept."""
        uid = storage.courses.create_course({
            "title": "Bio",
            "modules": [{
                "title": "M1", "uid": "mod_1",
                "units": [{
                    "title": "U1", "uid": "unit_1",
                    "lessons": [{
                        "title": "L1", "uid": "less_1",
                        "concepts": [{
                            "uid": "c1", "title": "Mitosis", "ordinal": 3,
                            "llm_fallback": True, "source_confidence": 0.12,
                        }],
                    }],
                }],
            }],
        })
        concept = storage.courses.get_flat_concepts(uid)[0]
        assert concept["ordinal"] == 3
        assert concept["llm_fallback"] is True
        assert concept["source_confidence"] == 0.12

    def test_absent_signals_default_without_lying(self, storage):
        uid = _make_course(storage, "Bio", [("c1", "Mitosis", "Chromosomes.")])
        concept = storage.courses.get_flat_concepts(uid)[0]
        assert concept["llm_fallback"] is False
        assert concept["source_confidence"] is None
