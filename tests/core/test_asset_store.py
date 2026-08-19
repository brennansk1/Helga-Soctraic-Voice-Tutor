"""Assets as BLOBs, with licence and role both enforced at the boundary."""

import os
import sys
import tempfile
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common.storage import StorageManager  # noqa: E402


class TestAssetStore(unittest.TestCase):
    def setUp(self):
        self.sm = StorageManager(tempfile.mkdtemp(prefix="assets_"))
        self.cs = self.sm.courses

    def _asset(self, sha="a1", **kw):
        kw.setdefault("license", "public domain")
        kw.setdefault("source", "NASA")
        return self.cs.save_asset(sha, data=b"bytes", mime="image/png", **kw)

    def test_an_unlicensed_asset_is_refused(self):
        """Fail-closed licensing is the one part of the safety story that
        demonstrably works, so it is enforced at the storage boundary rather
        than trusted to every caller."""
        self.assertIsNone(self.cs.save_asset("x", data=b"b", license=None))
        self.assertIsNone(self.cs.save_asset("x", data=b"b", license=""))

    def test_a_licensed_asset_is_stored_and_deduped_by_hash(self):
        a = self._asset("sha_dup")
        b = self._asset("sha_dup")
        self.assertIsNotNone(a)
        self.assertEqual(a, b, "the same bytes must not store twice")

    def test_a_role_is_required_and_must_be_known(self):
        """The seductive-details evidence is about DECORATIVE images, and
        'photograph' was the research's proxy for that. Since curated
        photographs are permitted, the role replaces the medium proxy."""
        a = self._asset("sha_role")
        self.assertFalse(self.cs.attach_asset("c1", "con_a", a, "decorative"))
        self.assertFalse(self.cs.attach_asset("c1", "con_a", a, ""))
        self.assertTrue(self.cs.attach_asset("c1", "con_a", a, "illustrates"))

    def test_every_allowed_role_is_accepted(self):
        a = self._asset("sha_all")
        for role in self.cs.ALLOWED_ASSET_ROLES:
            self.assertTrue(self.cs.attach_asset("c1", f"con_{role}", a, role), role)

    def test_licence_verification_time_is_recorded(self):
        """Makes fail-closed licensing auditable rather than asserted."""
        self._asset("sha_when")
        row = self.sm.progress._get_db().execute(
            "SELECT license_verified_at FROM assets WHERE sha256='sha_when'"
        ).fetchone()
        self.assertTrue(row[0])

    def test_the_link_carries_provenance_back(self):
        a = self._asset("sha_prov", caption="Jupiter", caption_verified=True)
        self.cs.attach_asset("c1", "con_a", a, "illustrates")
        linked = self.cs.concept_asset_list("c1", "con_a")
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["role"], "illustrates")
        self.assertEqual(linked[0]["source"], "NASA")
        self.assertTrue(linked[0]["caption_verified"])

    def test_a_concept_with_no_assets_returns_empty(self):
        self.assertEqual(self.cs.concept_asset_list("c1", "nothing"), [])


class TestConceptMath(unittest.TestCase):
    def setUp(self):
        self.sm = StorageManager(tempfile.mkdtemp(prefix="math_"))
        self.cs = self.sm.courses

    def test_spans_round_trip_in_order(self):
        self.cs.save_concept_math("c1", "con_a", [
            ("a^2", "a squared", []), (r"\frac{a}{b}", "a over b", [])])
        got = self.cs.get_concept_math("c1", "con_a")
        self.assertEqual([g["speech"] for g in got], ["a squared", "a over b"])

    def test_speakable_substitutes_formulas(self):
        self.cs.save_concept_math("c1", "con_a", [("a^2", "a squared", [])])
        out = self.cs.speakable("c1", "con_a", "The term $a^2$ appears.")
        self.assertEqual(out, "The term a squared appears.")

    def test_a_formula_with_no_speech_is_left_visible_not_deleted(self):
        """Silence would be a worse failure than an awkward reading, and it
        stays visible to whoever debugs it."""
        self.cs.save_concept_math("c1", "con_a", [("a^2", "", [])])
        self.assertIn("$a^2$", self.cs.speakable("c1", "con_a", "Term $a^2$."))

    def test_resaving_replaces_rather_than_appends(self):
        for _ in range(3):
            self.cs.save_concept_math("c1", "con_a", [("a^2", "a squared", [])])
        self.assertEqual(len(self.cs.get_concept_math("c1", "con_a")), 1)
