"""B16.3/B26.1–B26.3 tests: standards-driven build, tagging, review state
machine guards, publish snapshots + changelog, republish versioning.

Builder classes are injected fakes — the pipeline contract, tagging, and
state machine are what is under test, not the LLM.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.storage import StorageManager
from services.core.catalog_admin import (
    build_from_brief, tag_concept_standards, transition_catalog_course,
    republish, get_version_snapshot, load_brief,
)

BRIEF = {
    "brief_id": "math_g6_rp",
    "title": "Grade 6 Mathematics: Ratios, Rates & Percent",
    "subject": "math",
    "grade_band": "6-8",
    "grade_numeric": 6,
    "standard_codes": ["6.RP", "6.NS"],
    "scope": 3, "mastery": 3, "starting_from": 2,
    "concept_standard_map": {
        r"ratio": {"standard_code": "6.RP", "coverage": "full"},
        r"number|fraction": {"standard_code": "6.NS", "coverage": "full"},
    },
}


class FakeSkeleton:
    """Writes a deterministic two-concept course into the injected storage."""
    def __init__(self, storage=None, status_callback=None, **kw):
        self.storage = storage

    def build(self, topic):
        return self.storage.courses.create_course({
            "uid": "course_cat_fake", "title": topic.split("\n")[0],
            "status": "ready",
            "modules": [{"uid": "mod_1", "title": "M1", "units": [
                {"uid": "unit_1", "title": "U1", "lessons": [
                    {"uid": "less_1", "title": "L1", "concepts": [
                        {"uid": "con_ratio_1", "title": "Ratio Notation"},
                        {"uid": "con_frac_1", "title": "Dividing Fractions"}]}]}]}],
        })


class FakeAuditor:
    def __init__(self, storage=None, status_callback=None, **kw):
        pass

    def audit(self, uid, target_depth=2):
        return uid


class FakeHydrator:
    def __init__(self, storage=None, status_callback=None, **kw):
        pass

    def hydrate(self, uid):
        return uid


@pytest.fixture
def storage(tmp_path):
    st = StorageManager(str(tmp_path))
    st.standards.upsert("6.RP", "math", "Ratios & Proportional Relationships",
                        "Ratio reasoning.", grade_band="6-8")
    st.standards.upsert("6.NS", "math", "The Number System",
                        "Fractions and rational numbers.", grade_band="6-8")
    return st


def _build(storage, brief=None, llm_json=None):
    return build_from_brief(storage, brief or BRIEF,
                            skeleton_cls=FakeSkeleton, auditor_cls=FakeAuditor,
                            hydrator_cls=FakeHydrator, llm_json=llm_json)


class TestBuild:
    def test_builds_draft_catalog_course(self, storage):
        uid = _build(storage)
        course = storage.catalog_courses.get_course(uid)
        cat = course["catalog"]
        assert cat["catalog_status"] == "draft"
        assert cat["subject"] == "math" and cat["grade_band"] == "6-8"
        assert cat["visibility"] == "private"
        # band bounds override sliders (spec 04 §3.2.2)
        assert course["bloom_floor"] == 2 and course["bloom_ceiling"] == 5
        # files under data/catalog/courses, not user courses
        assert os.path.exists(os.path.join(storage.catalog_dir, uid, "structure.json"))
        # drafts invisible to students
        assert storage.catalog_courses.list_catalog_courses(published_only=True) == []

    def test_unknown_standard_rejected(self, storage):
        bad = dict(BRIEF, standard_codes=["6.RP", "NOPE.1"])
        with pytest.raises(ValueError, match="unknown standard"):
            _build(storage, bad)

    def test_hint_map_tags_concepts(self, storage):
        uid = _build(storage)
        tags = storage.standards.standards_for_concept("con_ratio_1")
        assert tags and tags[0]["standard_code"] == "6.RP"
        tags = storage.standards.standards_for_concept("con_frac_1")
        assert tags and tags[0]["standard_code"] == "6.NS"

    def test_llm_tag_flagged_for_confirmation(self, storage):
        brief = dict(BRIEF, concept_standard_map={
            r"ratio": {"standard_code": "6.RP", "coverage": "full"}})
        llm = lambda s, u, sch: {"standard_code": "6.NS", "coverage": "partial"}
        uid = _build(storage, brief, llm_json=llm)
        course = storage.catalog_courses.get_course(uid)
        cons = {c["uid"]: c for lesson in course["modules"][0]["units"][0]["lessons"]
                for c in lesson["concepts"]}
        assert cons["con_frac_1"]["concept_standards"][0]["tag_source"] == "llm"
        assert cons["con_ratio_1"]["concept_standards"][0]["tag_source"] == "human"


class TestReviewStateMachine:
    def _reviewed(self, storage):
        uid = _build(storage)
        transition_catalog_course(storage, uid, "submit_for_review")
        transition_catalog_course(storage, uid, "approve", actor="par_admin01")
        return uid

    def test_happy_path_to_published(self, storage):
        uid = self._reviewed(storage)
        status = transition_catalog_course(storage, uid, "publish",
                                           actor="par_admin01", note="v1 launch")
        assert status == "published"
        course = storage.catalog_courses.get_course(uid)
        assert course["catalog"]["visibility"] == "catalog"
        assert course["catalog"]["published_at"]
        # now visible to students
        assert [c["uid"] for c in
                storage.catalog_courses.list_catalog_courses()] == [uid]
        # snapshot + changelog written (B26.3)
        vpath = os.path.join(storage.catalog_dir, uid, "versions", "v1.json")
        assert os.path.exists(vpath)
        changelog = open(os.path.join(storage.catalog_dir, uid, "CHANGELOG.md")).read()
        assert "v1" in changelog and "v1 launch" in changelog

    def test_submit_blocks_untagged_concepts(self, storage):
        brief = dict(BRIEF, concept_standard_map={
            r"ratio": {"standard_code": "6.RP", "coverage": "full"}})
        uid = _build(storage, brief)   # fraction concept left untagged
        with pytest.raises(ValueError, match="missing standard tags"):
            transition_catalog_course(storage, uid, "submit_for_review")

    def test_publish_requires_reviewer(self, storage):
        uid = _build(storage)
        transition_catalog_course(storage, uid, "submit_for_review")
        with pytest.raises(ValueError, match="sign-off"):
            transition_catalog_course(storage, uid, "publish")

    def test_publish_blocks_unconfirmed_llm_tags(self, storage):
        brief = dict(BRIEF, concept_standard_map={
            r"ratio": {"standard_code": "6.RP", "coverage": "full"}})
        llm = lambda s, u, sch: {"standard_code": "6.NS", "coverage": "full"}
        uid = _build(storage, brief, llm_json=llm)
        transition_catalog_course(storage, uid, "submit_for_review")
        transition_catalog_course(storage, uid, "approve", actor="rev")
        with pytest.raises(ValueError, match="LLM tag"):
            transition_catalog_course(storage, uid, "publish", actor="rev")

    def test_publish_requires_full_standards_coverage(self, storage):
        brief = dict(BRIEF,
                     standard_codes=["6.RP", "6.NS"],
                     concept_standard_map={
                         r"ratio": {"standard_code": "6.RP", "coverage": "full"},
                         r"fraction": {"standard_code": "6.RP", "coverage": "partial"}})
        uid = _build(storage, brief)   # 6.NS never covered
        transition_catalog_course(storage, uid, "submit_for_review")
        transition_catalog_course(storage, uid, "approve", actor="rev")
        with pytest.raises(ValueError, match="not covered"):
            transition_catalog_course(storage, uid, "publish", actor="rev")

    def test_request_changes_needs_note_and_returns_to_draft(self, storage):
        uid = self._reviewed(storage)
        with pytest.raises(ValueError, match="note required"):
            transition_catalog_course(storage, uid, "request_changes")
        status = transition_catalog_course(storage, uid, "request_changes",
                                           note="tighten unit 2")
        assert status == "draft"

    def test_retire_hides_from_students(self, storage):
        uid = self._reviewed(storage)
        transition_catalog_course(storage, uid, "publish", actor="rev")
        status = transition_catalog_course(storage, uid, "retire")
        assert status == "retired"
        assert storage.catalog_courses.list_catalog_courses() == []

    def test_invalid_transitions_rejected(self, storage):
        uid = _build(storage)
        with pytest.raises(ValueError):
            transition_catalog_course(storage, uid, "publish")     # draft→publish
        with pytest.raises(ValueError):
            transition_catalog_course(storage, uid, "retire")      # draft→retire
        with pytest.raises(ValueError):
            transition_catalog_course(storage, uid, "made_up")


class TestVersioning:
    def test_republish_bumps_version_and_pins_snapshots(self, storage):
        uid = _build(storage)
        transition_catalog_course(storage, uid, "submit_for_review")
        transition_catalog_course(storage, uid, "approve", actor="rev")
        transition_catalog_course(storage, uid, "publish", actor="rev", note="v1")

        # edit cycle: revise published → draft working copy, edit, re-review
        transition_catalog_course(storage, uid, "revise")
        course = storage.catalog_courses.get_course(uid)
        course["title"] = "Grade 6 Math: Ratios v2"
        storage.catalog_courses.update_course(uid, course)
        transition_catalog_course(storage, uid, "submit_for_review")
        transition_catalog_course(storage, uid, "approve", actor="rev")
        status = republish(storage, uid, actor="rev", note="v2 edits")
        assert status == "published"

        course = storage.catalog_courses.get_course(uid)
        assert course["catalog"]["version"] == 2
        # pinned v1 snapshot still readable with the ORIGINAL title
        v1 = get_version_snapshot(storage, uid, 1)
        assert "v2" not in v1["title"]
        v2 = get_version_snapshot(storage, uid, 2)
        assert v2["title"].endswith("v2")
        changelog = open(os.path.join(storage.catalog_dir, uid, "CHANGELOG.md")).read()
        assert "## v1" in changelog and "## v2" in changelog

    def test_snapshot_falls_back_to_live(self, storage):
        uid = _build(storage)
        snap = get_version_snapshot(storage, uid, 7)   # never published
        assert snap["uid"] == uid


class TestBriefLoading:
    def test_json_brief_roundtrip(self, tmp_path):
        path = tmp_path / "brief.json"
        path.write_text(json.dumps(BRIEF))
        brief = load_brief(str(path))
        assert brief["brief_id"] == "math_g6_rp"

    def test_missing_fields_rejected(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"brief_id": "x", "title": "T"}))
        with pytest.raises(ValueError, match="missing required"):
            load_brief(str(path))

    def test_bad_band_rejected(self, tmp_path):
        path = tmp_path / "bad2.json"
        path.write_text(json.dumps(dict(BRIEF, grade_band="14-16")))
        with pytest.raises(ValueError, match="not a band"):
            load_brief(str(path))
