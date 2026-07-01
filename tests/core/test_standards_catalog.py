"""B16.1/B16.2 tests: v5 standards layer, loader, catalog store separation."""

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.storage import StorageManager
from services.common.standards_loader import load_standards_dir


@pytest.fixture
def storage(tmp_path):
    return StorageManager(str(tmp_path))


def _seed_file(tmp_path, name="math.json", subject="math", standards=None):
    seed_dir = tmp_path / "standards"
    seed_dir.mkdir(exist_ok=True)
    payload = {
        "subject": subject,
        "source": "USBE",
        "adopted_year": 2016,
        "standards": standards if standards is not None else [
            {"code": "6.RP", "grade_band": "6-8", "grade_numeric": 6,
             "strand": "Ratios & Proportional Relationships",
             "text": "Understand ratio concepts and use ratio reasoning."},
            {"code": "6.NS", "grade_band": "6-8", "grade_numeric": 6,
             "strand": "The Number System",
             "text": "Divide fractions by fractions; rational numbers."},
            {"code": "6.RP.PROB", "grade_band": "6-8", "grade_numeric": 6,
             "strand": "Ratios & Proportional Relationships",
             "text": "Introductory probability.", "is_enrichment": 1},
        ],
    }
    (seed_dir / name).write_text(json.dumps(payload))
    return str(seed_dir)


class TestV5Migration:
    def test_tables_and_columns_exist(self, storage):
        conn = sqlite3.connect(storage.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"standards", "concept_standards"} <= tables
        cols = {c[1] for c in conn.execute("PRAGMA table_info(courses)").fetchall()}
        for col in ("subject", "grade_band", "is_catalog", "catalog_status",
                    "version", "visibility", "published_at", "enrichment_included"):
            assert col in cols, f"missing catalog column {col}"
        ver = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert ver >= 5
        conn.close()


class TestStandardsStore:
    def test_upsert_and_get(self, storage):
        assert storage.standards.upsert("BIO.1", "science", "Ecosystems",
                                        "Analyze interactions.", grade_band="BIO")
        # replace, not duplicate
        assert not storage.standards.upsert("BIO.1", "science", "Ecosystems",
                                            "Analyze interactions v2.", grade_band="BIO")
        row = storage.standards.get("BIO.1")
        assert row["text"].endswith("v2.")

    def test_subject_whitelist(self, storage):
        with pytest.raises(ValueError):
            storage.standards.upsert("X.1", "underwater_basketweaving", "S", "T")

    def test_list_filters(self, storage):
        storage.standards.upsert("K.CC", "math", "Counting", "Count to 100.",
                                 grade_band="K-2")
        storage.standards.upsert("6.RP", "math", "Ratios", "Ratio reasoning.",
                                 grade_band="6-8")
        storage.standards.upsert("6.RP.X", "math", "Ratios", "Enrichment.",
                                 grade_band="6-8", is_enrichment=True)
        assert len(storage.standards.list(subject="math")) == 3
        assert len(storage.standards.list(subject="math", grade_band="6-8")) == 2
        assert len(storage.standards.list(subject="math", include_enrichment=False)) == 2

    def test_concept_tagging_and_coverage(self, storage):
        storage.standards.upsert("6.RP", "math", "Ratios", "Ratio reasoning.")
        storage.standards.sync_concept_standards(
            ["con_aaa", "con_bbb"],
            [{"concept_uid": "con_aaa", "standard_code": "6.RP", "coverage": "full"},
             {"concept_uid": "con_bbb", "standard_code": "6.RP", "coverage": "partial"}])
        assert len(storage.standards.concepts_for_standard("6.RP")) == 2
        tagged = storage.standards.standards_for_concept("con_aaa")
        assert tagged[0]["standard_code"] == "6.RP" and tagged[0]["coverage"] == "full"
        # re-sync replaces (delete-then-insert)
        storage.standards.sync_concept_standards(
            ["con_aaa", "con_bbb"],
            [{"concept_uid": "con_aaa", "standard_code": "6.RP"}])
        assert len(storage.standards.concepts_for_standard("6.RP")) == 1
        report = storage.standards.coverage_report("math")
        assert report[0]["concept_count"] == 1

    def test_delete_blocked_when_tagged(self, storage):
        storage.standards.upsert("6.NS", "math", "Numbers", "Number system.")
        storage.standards.sync_concept_standards(
            [], [{"concept_uid": "con_x", "standard_code": "6.NS"}])
        assert not storage.standards.delete("6.NS")   # blocked
        storage.standards.sync_concept_standards(["con_x"], [])
        assert storage.standards.delete("6.NS")

    def test_invalid_coverage_rejected(self, storage):
        storage.standards.upsert("6.RP", "math", "Ratios", "T.")
        with pytest.raises(ValueError):
            storage.standards.sync_concept_standards(
                [], [{"concept_uid": "c", "standard_code": "6.RP", "coverage": "sorta"}])


class TestStandardsLoader:
    def test_load_json_seed(self, storage, tmp_path):
        seed_dir = _seed_file(tmp_path)
        result = load_standards_dir(storage, seed_dir)
        assert result["errors"] == []
        assert result["inserted"] == 3
        assert len(storage.standards.list(subject="math")) == 3
        assert storage.standards.get("6.RP.PROB")["is_enrichment"] == 1

    def test_reload_is_idempotent(self, storage, tmp_path):
        seed_dir = _seed_file(tmp_path)
        load_standards_dir(storage, seed_dir)
        result = load_standards_dir(storage, seed_dir)
        assert result["inserted"] == 0 and result["updated"] == 3
        assert len(storage.standards.list(subject="math")) == 3

    def test_prune_removes_retired_but_blocks_tagged(self, storage, tmp_path):
        seed_dir = _seed_file(tmp_path)
        load_standards_dir(storage, seed_dir)
        storage.standards.sync_concept_standards(
            [], [{"concept_uid": "con_1", "standard_code": "6.NS"}])
        # new seed retires 6.NS and 6.RP.PROB
        _seed_file(tmp_path, standards=[
            {"code": "6.RP", "grade_band": "6-8", "strand": "Ratios",
             "text": "Ratio reasoning."}])
        result = load_standards_dir(storage, seed_dir, prune=True)
        assert result["pruned"] == 1      # 6.RP.PROB gone
        assert result["blocked"] == 1     # 6.NS kept (tagged)
        assert storage.standards.get("6.NS") is not None
        assert storage.standards.get("6.RP.PROB") is None

    def test_bad_rows_reported_not_fatal(self, storage, tmp_path):
        seed_dir = _seed_file(tmp_path, standards=[
            {"code": "", "strand": "S", "text": "T"},                 # missing code
            {"code": "6.RP", "strand": "", "text": "T"},              # missing strand
            {"code": "OK.1", "grade_band": "6-8", "strand": "S", "text": "T"},
        ])
        result = load_standards_dir(storage, seed_dir)
        assert result["inserted"] == 1
        assert len(result["errors"]) == 2

    def test_unknown_subject_rejected(self, storage, tmp_path):
        seed_dir = _seed_file(tmp_path, subject="phys_ed")
        result = load_standards_dir(storage, seed_dir)
        assert result["inserted"] == 0 and result["errors"]


class TestCatalogStore:
    def _catalog_course(self, uid="course_cat00001", status="published"):
        return {
            "uid": uid,
            "title": "Grade 6 Math: Ratios",
            "status": "ready",
            "catalog": {
                "is_catalog": True, "subject": "math", "grade_band": "6-8",
                "grade_numeric": 6, "catalog_status": status,
                "version": 1, "visibility": "catalog",
            },
            "modules": [],
        }

    def test_catalog_files_separate_from_electives(self, storage):
        uid = storage.catalog_courses.create_course(self._catalog_course())
        assert os.path.exists(os.path.join(storage.catalog_dir, uid, "structure.json"))
        assert not os.path.exists(os.path.join(storage.courses_dir, uid))
        # elective goes to the user dir, not the catalog
        e_uid = storage.courses.create_course({"title": "My Elective", "modules": []})
        assert os.path.exists(os.path.join(storage.courses_dir, e_uid))
        assert not os.path.exists(os.path.join(storage.catalog_dir, e_uid))

    def test_students_see_only_published(self, storage):
        storage.catalog_courses.create_course(self._catalog_course("course_pub00001", "published"))
        storage.catalog_courses.create_course(self._catalog_course("course_drf00001", "draft"))
        storage.catalog_courses.create_course(self._catalog_course("course_rev00001", "reviewed"))
        visible = storage.catalog_courses.list_catalog_courses(published_only=True)
        assert [c["uid"] for c in visible] == ["course_pub00001"]
        admin_view = storage.catalog_courses.list_catalog_courses(published_only=False)
        assert len(admin_view) == 3

    def test_catalog_filters(self, storage):
        c = self._catalog_course("course_ela00001")
        c["catalog"]["subject"] = "ela"
        storage.catalog_courses.create_course(c)
        storage.catalog_courses.create_course(self._catalog_course("course_mat00001"))
        assert len(storage.catalog_courses.list_catalog_courses(subject="ela")) == 1
        assert len(storage.catalog_courses.list_catalog_courses(grade_band="6-8")) == 2

    def test_elective_not_in_catalog_listing(self, storage):
        storage.courses.create_course({"title": "Elective", "modules": []})
        assert storage.catalog_courses.list_catalog_courses(published_only=False) == []
