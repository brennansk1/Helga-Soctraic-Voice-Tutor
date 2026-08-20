"""Course export/import (services/rag/share_api.py).

Three properties carry the whole feature, and each has a failure mode that
looks like success:

  1. ROUND TRIP. The friend's machine must receive the COURSE — structure,
     content, spoken math, trust-panel sources, licensed assets — and none of
     the SENDER's learner state. A bundle that quietly drops the SQLite half
     still "imports fine" and opens as a course with no trust panel.
  2. REJECTION IS RESIDUE-FREE AND NAMED. A malformed bundle that leaves a
     half-course behind turns one bad file into a corrupted library; a
     rejection without a name turns it into an unanswerable support question.
  3. A ZIP IS HOSTILE INPUT. Path traversal and decompression bombs are the
     classic ways an "import" becomes a write primitive on the host.
"""

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from flask import Flask                                     # noqa: E402

from services.common.storage import StorageManager          # noqa: E402
import services.rag.share_api as share                      # noqa: E402
from services.rag.share_api import create_share_blueprint   # noqa: E402


def _client(sm):
    app = Flask(__name__)
    app.register_blueprint(create_share_blueprint(sm))
    return app.test_client()


def _build_course(sm, uid="course_aaaa1111"):
    """A small but COMPLETE course: every store the bundle must carry, plus
    learner state that must not travel."""
    sm.courses.create_course({
        "uid": uid, "title": "Sharing 101", "status": "ready",
        "description": "How courses travel.",
        "modules": [{"uid": "mod_00000001", "title": "M1", "units": [
            {"uid": "unit_00000001", "title": "U1", "lessons": [
                {"uid": "less_00000001", "title": "L1", "concepts": [
                    {"uid": "con_alpha000", "title": "Alpha", "completed": True},
                    {"uid": "con_beta0000", "title": "Beta"},
                ]}]}]}],
    })
    sm.courses.save_concept_content(uid, "con_alpha000", "# Alpha\n\n$a^2$ body")
    sm.courses.save_concept_content(uid, "con_beta0000", "# Beta\n\nsecond body")
    sm.courses.save_concept_math(uid, "con_alpha000", [("a^2", "a squared", [])])

    db = sm.courses._get_db()
    cur = db.execute(
        "INSERT INTO sources (course_uid, concept_uid, title, url, passage, "
        "source_type, domain_tier, grounding, degraded, retrieved_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (uid, "con_alpha000", "A Source", "https://example.org/a",
         "the retained passage", "encyclopedia", "high", 0.9, 0, "2026-01-01"))
    db.execute(
        "INSERT INTO claim_sources (course_uid, concept_uid, claim, source_id, "
        "supplementary) VALUES (?,?,?,?,?)",
        (uid, "con_alpha000", "alpha squares things", cur.lastrowid, 0))
    db.commit()

    aid = sm.courses.save_asset("sha_share_1", data=b"PNG-BYTES",
                                mime="image/png", source="NASA",
                                license="public domain", alt_text="a square")
    sm.courses.attach_asset(uid, "con_alpha000", aid, "illustrates")

    # Learner state — the part a shared course must arrive WITHOUT.
    sm.progress.mark_completed("con_alpha000", uid)
    sm.flashcards.add_card(uid, "con_alpha000", "front", "back")
    return uid


def _snapshot(sm):
    """Everything a botched import could leave behind: course dirs plus row
    counts in every table the importer writes."""
    dirs = sorted(os.listdir(sm.courses.courses_dir))
    db = sm.courses._get_db()
    counts = {}
    for t in ("courses", "concepts", "concept_math", "sources",
              "claim_sources", "assets", "concept_assets"):
        counts[t] = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return dirs, counts


def _export_bytes(sm, uid):
    res = _client(sm).get(f"/api/share/course/{uid}/export")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.data


def _post_bundle(sm, data, name="bundle.zip"):
    return _client(sm).post(
        "/api/share/course/import", content_type="multipart/form-data",
        data={"bundle": (io.BytesIO(data), name)})


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.sm = StorageManager(tempfile.mkdtemp(prefix="share_src_"))
        self.uid = _build_course(self.sm)
        self.bundle = _export_bytes(self.sm, self.uid)

    def test_bundle_declares_itself_and_carries_no_learner_state(self):
        zf = zipfile.ZipFile(io.BytesIO(self.bundle))
        manifest = json.loads(zf.read("manifest.json"))
        self.assertEqual(manifest["format"], "helga-course-bundle")
        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(manifest["course_uid"], self.uid)
        self.assertTrue(manifest["files"], "manifest must inventory the files")
        for entry in manifest["files"]:
            self.assertIn("bytes", entry)
        # The sender's progress must not be inspectable in the bundle either:
        # structure.json travels with completion scrubbed, and no db table of
        # learner state exists in the layout at all.
        structure = json.loads(zf.read("course/structure.json"))
        self.assertNotIn("completed", json.dumps(structure))
        for name in zf.namelist():
            for forbidden in ("user_progress", "flashcards", "scheduled",
                              "activity", "session_notes", "interactions"):
                self.assertNotIn(forbidden, name)

    def test_import_on_fresh_machine_reproduces_the_course(self):
        dest = StorageManager(tempfile.mkdtemp(prefix="share_dst_"))
        res = _post_bundle(dest, self.bundle)
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertTrue(body["ok"])
        # No collision on a fresh machine, so the uid survives — a course
        # shared around a classroom keeps one identity until it has to fork.
        self.assertEqual(body["course_uid"], self.uid)
        self.assertFalse(body["renamed"])

        course = dest.courses.get_course(self.uid)
        self.assertEqual(course["title"], "Sharing 101")
        src_flat = self.sm.courses.get_flat_concepts(self.uid)
        dst_flat = dest.courses.get_flat_concepts(self.uid)
        self.assertEqual([c["uid"] for c in src_flat],
                         [c["uid"] for c in dst_flat])
        self.assertEqual(dest.courses.get_concept_content(self.uid, "con_alpha000"),
                         "# Alpha\n\n$a^2$ body")
        # The SQLite half arrived: spoken math, trust panel, licensed asset.
        math = dest.courses.get_concept_math(self.uid, "con_alpha000")
        self.assertEqual(math[0]["speech"], "a squared")
        trust = dest.courses.get_concept_sources(self.uid, "con_alpha000")
        self.assertTrue(trust["available"])
        self.assertEqual(trust["sources"][0]["title"], "A Source")
        self.assertEqual(trust["claims_total"], 1)
        assets = dest.courses.concept_asset_list(self.uid, "con_alpha000")
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["role"], "illustrates")
        self.assertEqual(assets[0]["license"], "public domain")

    def test_imported_course_arrives_fresh_of_learner_state(self):
        dest = StorageManager(tempfile.mkdtemp(prefix="share_fresh_"))
        _post_bundle(dest, self.bundle)
        self.assertEqual(dest.progress.get_course_progress(self.uid), [])
        self.assertEqual(dest.flashcards.get_cards_for_course(self.uid), [])
        # And the structure itself carries no completion residue.
        self.assertNotIn("completed",
                         json.dumps(dest.courses.get_course(self.uid)))

    def test_uid_collision_yields_an_independent_copy_under_a_new_uid(self):
        res = _post_bundle(self.sm, self.bundle)
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertTrue(body["renamed"])
        new_uid = body["course_uid"]
        self.assertNotEqual(new_uid, self.uid)

        # Identical structure, different identity: the concept tree matches
        # and the new copy's content lives under the NEW uid in both stores.
        self.assertEqual(
            [c["uid"] for c in self.sm.courses.get_flat_concepts(self.uid)],
            [c["uid"] for c in self.sm.courses.get_flat_concepts(new_uid)])
        self.assertEqual(
            self.sm.courses.get_concept_content(new_uid, "con_beta0000"),
            "# Beta\n\nsecond body")
        # The original's learner state stays the original's; the copy has none.
        self.assertEqual(self.sm.progress.get_course_progress(new_uid), [])
        # Provenance is recorded on the copy.
        self.assertEqual(
            self.sm.courses.get_course(new_uid)["share"]["original_uid"],
            self.uid)


class TestRejection(unittest.TestCase):
    """Every rejection NAMED, every rejection residue-free."""

    def setUp(self):
        self.sm = StorageManager(tempfile.mkdtemp(prefix="share_rej_"))
        self.before = _snapshot(self.sm)

    def _assert_rejected(self, data, reason, status=400, name="bundle.zip"):
        res = _post_bundle(self.sm, data, name=name)
        self.assertEqual(res.status_code, status, res.get_data(as_text=True))
        self.assertEqual(res.get_json()["error"], reason)
        self.assertEqual(_snapshot(self.sm), self.before,
                         f"a {reason} rejection left residue behind")

    def _zip(self, entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, payload in entries.items():
                zf.writestr(name, payload)
        return buf.getvalue()

    def _manifest(self, **over):
        m = {"format": "helga-course-bundle", "format_version": 1,
             "app_version": "test", "course_uid": "course_feedbeef",
             "course_title": "T", "created_at": "now", "files": []}
        m.update(over)
        return json.dumps(m)

    def _structure(self):
        return json.dumps({"uid": "course_feedbeef", "title": "T",
                           "modules": []})

    def test_not_a_zip_is_rejected(self):
        self._assert_rejected(b"definitely not a zip archive", "not_a_zip")

    def test_missing_manifest_is_rejected(self):
        self._assert_rejected(
            self._zip({"course/structure.json": self._structure()}),
            "manifest_missing")

    def test_unsupported_format_version_is_rejected(self):
        self._assert_rejected(
            self._zip({"manifest.json": self._manifest(format_version=99),
                       "course/structure.json": self._structure()}),
            "unsupported_format_version")

    def test_unparseable_structure_is_rejected(self):
        self._assert_rejected(
            self._zip({"manifest.json": self._manifest(),
                       "course/structure.json": "{not json"}),
            "structure_invalid")

    def test_manifest_structure_disagreement_is_rejected(self):
        self._assert_rejected(
            self._zip({"manifest.json": self._manifest(course_uid="course_other000"),
                       "course/structure.json": self._structure()}),
            "manifest_mismatch")

    def test_path_traversal_entries_are_rejected(self):
        for evil in ("../evil.md", "course/../../evil.md", "/etc/evil",
                     "course\\evil.md"):
            self._assert_rejected(
                self._zip({"manifest.json": self._manifest(),
                           "course/structure.json": self._structure(),
                           evil: "owned"}),
                "path_traversal")
        # And nothing named evil appeared anywhere near the data dir.
        parent = os.path.dirname(self.sm.data_dir)
        self.assertFalse([f for f in os.listdir(parent) if "evil" in f])
        self.assertFalse([f for f in os.listdir(self.sm.data_dir) if "evil" in f])

    def test_entries_outside_the_layout_are_rejected(self):
        self._assert_rejected(
            self._zip({"manifest.json": self._manifest(),
                       "course/structure.json": self._structure(),
                       "somewhere/else.txt": "??"}),
            "unexpected_entry")

    def test_file_count_cap_is_enforced(self):
        entries = {"manifest.json": self._manifest(),
                   "course/structure.json": self._structure()}
        for i in range(5):
            entries[f"course/content/con_{i}.md"] = "x"
        old = share.MAX_BUNDLE_FILES
        share.MAX_BUNDLE_FILES = 3
        try:
            self._assert_rejected(self._zip(entries), "too_many_files")
        finally:
            share.MAX_BUNDLE_FILES = old

    def test_unpacked_size_cap_is_enforced(self):
        old = share.MAX_UNPACKED_BYTES
        share.MAX_UNPACKED_BYTES = 64
        try:
            self._assert_rejected(
                self._zip({"manifest.json": self._manifest(),
                           "course/structure.json": self._structure(),
                           "course/content/big.md": "A" * 4096}),
                "unpacked_too_large")
        finally:
            share.MAX_UNPACKED_BYTES = old

    def test_export_of_a_missing_course_is_a_named_404(self):
        res = _client(self.sm).get("/api/share/course/course_gone0000/export")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.get_json()["error"], "course_not_found")


class TestImportPolicies(unittest.TestCase):
    def test_unlicensed_asset_is_refused_with_a_warning_not_silence(self):
        """Fail-closed licensing must survive the trip: a bundle whose asset
        row lost its licence gets the course WITHOUT that asset, and says so."""
        src = StorageManager(tempfile.mkdtemp(prefix="share_lic_src_"))
        uid = _build_course(src)
        bundle = _export_bytes(src, uid)

        # Rewrite db/assets.json with the licence stripped.
        zin = zipfile.ZipFile(io.BytesIO(bundle))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zout:
            for name in zin.namelist():
                data = zin.read(name)
                if name == "db/assets.json":
                    rows = json.loads(data)
                    for r in rows:
                        r["license"] = None
                    data = json.dumps(rows)
                zout.writestr(name, data)

        dest = StorageManager(tempfile.mkdtemp(prefix="share_lic_dst_"))
        res = _post_bundle(dest, buf.getvalue())
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(any(w.startswith("asset_refused_unlicensed")
                            for w in body["warnings"]))
        self.assertEqual(
            dest.courses.concept_asset_list(uid, "con_alpha000"), [])
        # The course itself still arrived.
        self.assertEqual(dest.courses.get_concept_content(uid, "con_alpha000"),
                         "# Alpha\n\n$a^2$ body")


if __name__ == "__main__":
    unittest.main()
