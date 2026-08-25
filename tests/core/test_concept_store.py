"""Concept bodies in SQLite (v15), with the .md file as a mirror.

The reason is not speed. A row can be EMPTY and a file cannot, so
"hydrated and produced nothing" and "never hydrated" are the same state on
disk — a confusion this project has been bitten by repeatedly.
"""

import os
import sys
import tempfile
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common.storage import StorageManager  # noqa: E402


class TestConceptStore(unittest.TestCase):
    def setUp(self):
        self.sm = StorageManager(tempfile.mkdtemp(prefix="concept_store_"))
        self.cs = self.sm.courses
        self.cs.create_course({
            "uid": "c1", "title": "T",
            "modules": [{"uid": "m1", "title": "M", "units": [
                {"uid": "u1", "title": "U", "lessons": [
                    {"uid": "l1", "title": "L",
                     "concepts": [{"uid": "con_a", "title": "Eigenvalues"}]}]}]}]})

    def test_schema_is_at_least_v15(self):
        v = self.sm.progress._get_db().execute(
            "SELECT version FROM schema_version").fetchone()[0]
        self.assertGreaterEqual(v, 15)

    def test_content_round_trips_through_the_database(self):
        self.cs.save_concept_content("c1", "con_a", "## Key Facts\n- An eigenvalue scales its eigenvector.\n")
        self.assertIn("eigenvector", self.cs.get_concept_content("c1", "con_a"))

    def test_absent_empty_and_present_are_three_states(self):
        """The whole point. A file cannot express the middle one."""
        self.assertEqual(self.cs.concept_content_state("c1", "con_a"), "absent")
        self.cs.save_concept_content("c1", "con_a", "## Key Facts\n- Something.\n")
        self.assertEqual(self.cs.concept_content_state("c1", "con_a"), "present")
        self.cs.save_concept_content("c1", "con_b", "")
        self.assertEqual(self.cs.concept_content_state("c1", "con_b"), "empty")
        self.assertEqual(self.cs.concept_content_state("c1", "con_zz"), "absent")

    def test_the_markdown_mirror_is_still_written(self):
        """Kept greppable and exportable without a query."""
        self.cs.save_concept_content("c1", "con_a", "## Key Facts\n- Something.\n")
        row = self.sm.progress._get_db().execute(
            "SELECT path FROM concepts WHERE concept_uid='con_a'").fetchone()
        self.assertTrue(os.path.exists(row[0]))

    def test_a_content_hash_makes_drift_detectable(self):
        self.cs.save_concept_content("c1", "con_a", "one")
        h1 = self.sm.progress._get_db().execute(
            "SELECT content_hash FROM concepts WHERE concept_uid='con_a'").fetchone()[0]
        self.cs.save_concept_content("c1", "con_a", "two")
        h2 = self.sm.progress._get_db().execute(
            "SELECT content_hash FROM concepts WHERE concept_uid='con_a'").fetchone()[0]
        self.assertNotEqual(h1, h2)

    def test_fts5_indexes_the_content(self):
        # Asserted against `concept_fts` — the index SearchStore queries.
        # This used to name `concepts_fts`, a second index written on every
        # save and read by nothing, which has been removed; asserting on it
        # was what made a write-only index look load-bearing.
        self.cs.save_concept_content("c1", "con_a", "An eigenvalue scales its eigenvector.")
        hits = self.sm.progress._get_db().execute(
            "SELECT concept_uid FROM concept_fts WHERE concept_fts MATCH 'eigenvector'"
        ).fetchall()
        self.assertEqual([h[0] for h in hits], ["con_a"])

    def test_re_saving_replaces_rather_than_duplicating(self):
        for _ in range(3):
            self.cs.save_concept_content("c1", "con_a", "text")
        n = self.sm.progress._get_db().execute(
            "SELECT COUNT(*) FROM concepts WHERE concept_uid='con_a'").fetchone()[0]
        self.assertEqual(n, 1)
        n_fts = self.sm.progress._get_db().execute(
            "SELECT COUNT(*) FROM concept_fts WHERE concept_uid='con_a'").fetchone()[0]
        self.assertEqual(n_fts, 1, "the FTS row must be replaced too")

    def test_a_disk_only_concept_is_still_readable(self):
        """Courses built before v15 must keep working — this is additive."""
        d = os.path.join(self.sm.courses.courses_dir, "c1", "content")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "con_legacy.md"), "w") as f:
            f.write("legacy body")
        self.assertEqual(self.cs.get_concept_content("c1", "con_legacy"), "legacy body")
