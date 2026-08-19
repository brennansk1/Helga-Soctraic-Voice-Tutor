"""Choosing between assets — across sources, and across a whole course.

`assets.sha256` already dedupes byte-identical files. It does not catch the same
diagram re-encoded or rescaled by two institutions, which is the normal
cross-source case. Measured on real textbook figures: a JPEG re-encoding of a
figure hashes 1 bit away from the original, a 50% rescale 0 bits away, and a
different figure 30 bits away.
"""

import io
import os
import sys
import tempfile
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.asset_arbiter import (  # noqa: E402
    MAX_CONCEPTS_PER_ASSET, arbitrate, course_duplicates, dhash, hamming,
    near_duplicate_groups, score)


def _png(w=200, h=200, seed=0):
    from PIL import Image
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 3 + seed * 37) % 256, (y * 5 + seed * 11) % 256,
                        (x + y + seed) % 256)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue(), img


class TestPerceptualHash(unittest.TestCase):
    def test_a_re_encoding_is_the_same_picture(self):
        raw, img = _png(seed=1)
        b = io.BytesIO()
        img.save(b, "JPEG", quality=80)
        self.assertLessEqual(hamming(dhash(raw), dhash(b.getvalue())), 4)

    def test_a_rescale_is_the_same_picture(self):
        raw, img = _png(seed=2)
        b = io.BytesIO()
        img.resize((img.width // 2, img.height // 2)).save(b, "PNG")
        self.assertLessEqual(hamming(dhash(raw), dhash(b.getvalue())), 4)

    def test_a_different_picture_is_far_away(self):
        a, _ = _png(seed=3)
        b, _ = _png(seed=99)
        self.assertGreater(hamming(dhash(a), dhash(b)), 10)

    def test_unreadable_bytes_do_not_raise(self):
        self.assertIsNone(dhash(b"not an image"))
        self.assertEqual(hamming(None, 5), 64)


class TestScoring(unittest.TestCase):
    def test_a_textbook_figure_outranks_an_aggregator(self):
        """A textbook figure was drawn FOR teaching and carries a caption
        written by the author."""
        book = {"source": "OpenStax/CNX", "license": "CC BY 4.0",
                "caption": "x", "caption_verified": True}
        agg = {"source": "Openverse", "license": "by"}
        self.assertGreater(score(book), score(agg))

    def test_a_verified_caption_is_worth_more_than_none(self):
        base = {"source": "NASA", "license": "public domain"}
        withcap = dict(base, caption="x", caption_verified=True)
        self.assertGreater(score(withcap), score(base))

    def test_resolution_is_capped(self):
        small = {"source": "NASA", "license": "public domain",
                 "width": 400, "height": 400}
        huge = dict(small, width=8000, height=8000)
        self.assertLessEqual(score(huge) - score(small), 15)


class TestArbitrate(unittest.TestCase):
    def test_the_best_of_a_duplicate_group_wins(self):
        raw, img = _png(seed=4)
        b = io.BytesIO()
        img.save(b, "JPEG", quality=80)
        cands = [
            {"asset_id": 1, "bytes": b.getvalue(), "source": "Openverse",
             "license": "by"},
            {"asset_id": 2, "bytes": raw, "source": "OpenStax/CNX",
             "license": "CC BY 4.0", "caption": "c", "caption_verified": True},
        ]
        kept, dropped = arbitrate(cands, keep=1)
        self.assertEqual([k["asset_id"] for k in kept], [2])
        self.assertEqual([d["asset_id"] for d in dropped], [1])

    def test_distinct_pictures_both_survive_when_keep_allows(self):
        a, _ = _png(seed=5)
        b, _ = _png(seed=60)
        cands = [{"asset_id": 1, "bytes": a, "source": "NASA", "license": "public domain"},
                 {"asset_id": 2, "bytes": b, "source": "NASA", "license": "public domain"}]
        kept, _ = arbitrate(cands, keep=2)
        self.assertEqual(len(kept), 2)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(arbitrate([]), ([], []))

    def test_dropped_are_returned_not_silently_lost(self):
        a, _ = _png(seed=6)
        b, _ = _png(seed=61)
        kept, dropped = arbitrate(
            [{"asset_id": 1, "bytes": a, "source": "X", "license": "cc0"},
             {"asset_id": 2, "bytes": b, "source": "Y", "license": "cc0"}], keep=1)
        self.assertEqual(len(kept) + len(dropped), 2)


class TestCourseDuplicates(unittest.TestCase):
    def test_wallpaper_is_caught(self):
        att = [{"concept_uid": f"c{i}", "asset_id": 7} for i in range(6)]
        over = course_duplicates(att)
        self.assertIn(7, over)
        self.assertEqual(len(over[7]), 6)

    def test_a_recurring_anchor_is_allowed(self):
        """Not 1, deliberately: a diagram anchoring a spiral SHOULD recur —
        the same reasoning that made the text redundancy gate a share."""
        att = [{"concept_uid": f"c{i}", "asset_id": 7}
               for i in range(MAX_CONCEPTS_PER_ASSET)]
        self.assertEqual(course_duplicates(att), {})

    def test_the_same_concept_twice_counts_once(self):
        att = [{"concept_uid": "c1", "asset_id": 7}] * 9
        self.assertEqual(course_duplicates(att), {})


class TestNearDuplicateGroups(unittest.TestCase):
    def test_two_sources_one_picture_group_together(self):
        raw, img = _png(seed=7)
        b = io.BytesIO()
        img.save(b, "JPEG", quality=80)
        groups = near_duplicate_groups([
            {"asset_id": 1, "bytes": raw, "source": "OpenStax/CNX",
             "license": "CC BY 4.0", "caption": "c", "caption_verified": True},
            {"asset_id": 2, "bytes": b.getvalue(), "source": "Openverse",
             "license": "by"}])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["keep"], 1)
        self.assertEqual(groups[0]["collapse"], [2])

    def test_distinct_assets_form_no_group(self):
        a, _ = _png(seed=8)
        b, _ = _png(seed=80)
        self.assertEqual(near_duplicate_groups([
            {"asset_id": 1, "bytes": a, "source": "X", "license": "cc0"},
            {"asset_id": 2, "bytes": b, "source": "Y", "license": "cc0"}]), [])


class TestSweepIntegration(unittest.TestCase):
    def setUp(self):
        from services.common.storage import StorageManager
        self.sm = StorageManager(tempfile.mkdtemp(prefix="sweep_test_"))
        self.cs = self.sm.courses

    def test_a_duplicate_repoints_to_the_winner(self):
        raw, img = _png(seed=9)
        b = io.BytesIO()
        img.save(b, "JPEG", quality=80)
        good = self.cs.save_asset("g", data=raw, source="OpenStax/CNX",
                                  license="CC BY 4.0", caption="c",
                                  caption_verified=True, width=200, height=200)
        dupe = self.cs.save_asset("d", data=b.getvalue(), source="Openverse",
                                  license="by", width=200, height=200)
        self.cs.attach_asset("c1", "con_a", good, "illustrates")
        self.cs.attach_asset("c1", "con_b", dupe, "illustrates")
        rep = self.cs.sweep_course_assets("c1")
        self.assertEqual(len(rep["collapsed"]), 1)
        self.assertEqual(
            self.cs.concept_asset_list("c1", "con_b")[0]["asset_id"], good)

    def test_wallpaper_is_thinned_to_the_cap(self):
        raw, _ = _png(seed=10)
        a = self.cs.save_asset("w", data=raw, source="NASA",
                               license="public domain", width=200, height=200)
        for i in range(6):
            self.cs.attach_asset("c1", f"con_{i}", a, "illustrates")
        self.cs.sweep_course_assets("c1")
        left = sum(len(self.cs.concept_asset_list("c1", f"con_{i}")) for i in range(6))
        self.assertEqual(left, MAX_CONCEPTS_PER_ASSET)

    def test_a_dry_run_changes_nothing(self):
        raw, _ = _png(seed=11)
        a = self.cs.save_asset("dr", data=raw, source="NASA",
                               license="public domain", width=200, height=200)
        for i in range(6):
            self.cs.attach_asset("c1", f"con_{i}", a, "illustrates")
        rep = self.cs.sweep_course_assets("c1", dry_run=True)
        self.assertGreater(rep["detached"], 0)
        left = sum(len(self.cs.concept_asset_list("c1", f"con_{i}")) for i in range(6))
        self.assertEqual(left, 6, "a dry run must not detach anything")

    def test_a_course_with_no_assets_is_survivable(self):
        self.assertEqual(self.cs.sweep_course_assets("empty")["assets"], 0)
