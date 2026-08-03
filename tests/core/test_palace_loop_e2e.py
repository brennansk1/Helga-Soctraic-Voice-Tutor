"""Memory Palace, walked end to end against real storage.

Criterion 5 listed the Memory Palace as "UI restored but unexercised". The UI
being reachable says nothing about whether the loop behind it works, and this
feature has already shipped broken twice: once with the route redirecting away
while the FSM kept the full MEMORY_PALACE state, and once with web-ui dropping
course_uid so librarian's /locus/next 400'd on every call and the palace could
not be walked at all.

So this walks it: start -> advance through every locus -> wrap -> anchor a
concept -> confirm the anchor comes back on the next visit.

Storage here is a REAL StorageManager over a temp directory with a real course
on disk, not a MagicMock. Both previous breakages were at seams between
components, which is exactly what mocks paper over.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

# Heavy optional deps are stubbed PERMANENTLY rather than inside a with-block.
# patch.dict('sys.modules', ...) removes any module imported during the block
# when it exits, so importing librarian under it left a dangling app object:
# the routes closed over the original module's `storage` global while later
# patches created and patched a second, unrelated module instance.
_mock_cb = MagicMock()
_mock_cb.__file__ = 'mocked'
sys.modules.setdefault('libzim', MagicMock())
sys.modules.setdefault('libzim.reader', MagicMock())
sys.modules.setdefault('sentence_transformers', MagicMock())
sys.modules.setdefault('services.core.course_builder', _mock_cb)
sys.modules.setdefault('services.common.course_cleaner', MagicMock())

with patch('os.path.exists', return_value=False):
    from services.rag import librarian as _lib  # noqa: E402

COURSE_UID = "course_palace1"
CONCEPTS = ["The Front Door", "The Hallway Mirror", "The Kitchen Table"]


def _course():
    return {
        "uid": COURSE_UID, "title": "Palace Test", "mastery": 2,
        "modules": [{
            "uid": "mod_00000001", "title": "Ground Floor",
            "units": [{
                "uid": "unit_00000001", "title": "Entrance",
                "lessons": [{
                    "uid": "less_0000001", "title": "Walkthrough",
                    "concepts": [
                        {"uid": f"con_{i:08d}", "title": t, "bloom_level": 1}
                        for i, t in enumerate(CONCEPTS)
                    ],
                }],
            }],
        }],
    }


class PalaceLoopCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _lib.app
        cls.app.config['TESTING'] = True

    def setUp(self):
        # Fresh storage per test. Anchors accumulate in activity_log, so a
        # class-scoped DB lets one test's anchor answer another test's query —
        # which is how the "oldest anchor wins" bug first showed up here.
        from services.common.storage import StorageManager
        self.dir = tempfile.mkdtemp()
        self.storage = StorageManager(data_dir=self.dir)
        self.storage.courses.create_course(_course())
        self._patch = patch.object(_lib, 'storage', self.storage)
        self._patch.start()
        self.client = self.app.test_client()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    # helpers ---------------------------------------------------------------
    def _start(self):
        return self.client.get('/palace/start',
                               query_string={'course_uid': COURSE_UID})

    def _next(self, current):
        return self.client.get('/locus/next',
                               query_string={'current': current,
                                             'course_uid': COURSE_UID})

    def _anchor(self, locus_uid, concept_text, sensory_text=""):
        return self.client.post('/anchor', json={
            'course_uid': COURSE_UID, 'locus_uid': locus_uid,
            'concept_text': concept_text, 'sensory_text': sensory_text})


class TestEnteringThePalace(PalaceLoopCase):

    def test_start_returns_the_first_locus(self):
        rv = self._start()
        self.assertEqual(rv.status_code, 200)
        body = rv.get_json()
        self.assertEqual(body['description'], CONCEPTS[0])
        self.assertEqual(body['index'], 0)
        self.assertEqual(body['total'], len(CONCEPTS))

    def test_a_fresh_locus_has_no_anchor(self):
        self.assertFalse(self._start().get_json()['has_concept'])

    def test_missing_course_uid_is_rejected(self):
        self.assertEqual(self.client.get('/palace/start').status_code, 400)

    def test_unknown_course_is_404_not_a_crash(self):
        rv = self.client.get('/palace/start',
                             query_string={'course_uid': 'course_nope'})
        self.assertEqual(rv.status_code, 404)


class TestWalkingThePalace(PalaceLoopCase):
    """web-ui once dropped course_uid here and every call 400'd."""

    def test_next_advances_one_locus(self):
        first = self._start().get_json()
        nxt = self._next(first['uid']).get_json()
        self.assertEqual(nxt['index'], 1)
        self.assertEqual(nxt['description'], CONCEPTS[1])

    def test_walking_the_whole_palace_visits_every_locus(self):
        seen, cur = [], self._start().get_json()
        seen.append(cur['description'])
        for _ in range(len(CONCEPTS) - 1):
            cur = self._next(cur['uid']).get_json()
            seen.append(cur['description'])
        self.assertEqual(seen, CONCEPTS,
                         "a palace you cannot walk end to end is not a palace")

    def test_the_walk_wraps_back_to_the_start(self):
        cur = self._start().get_json()
        for _ in range(len(CONCEPTS)):
            cur = self._next(cur['uid']).get_json()
        self.assertEqual(cur['index'], 0)

    def test_next_requires_course_uid(self):
        rv = self.client.get('/locus/next', query_string={'current': 'con_00000000'})
        self.assertEqual(
            rv.status_code, 400,
            "this is the call web-ui used to make without course_uid"
        )

    def test_an_unknown_current_locus_does_not_wedge_the_walk(self):
        rv = self._next('con_does_not_exist')
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_json()['index'], 1)


class TestAnchoringRoundTrip(PalaceLoopCase):
    """The point of the whole feature: what you place stays placed."""

    def test_anchor_is_accepted(self):
        locus = self._start().get_json()['uid']
        rv = self._anchor(locus, "Pythagorean theorem", "a glowing red triangle")
        self.assertEqual(rv.status_code, 200)
        self.assertTrue(rv.get_json()['success'])

    def test_anchor_is_visible_on_the_next_visit(self):
        locus = self._start().get_json()['uid']
        self._anchor(locus, "Pythagorean theorem", "a glowing red triangle")
        again = self._start().get_json()
        self.assertTrue(again['has_concept'])
        self.assertEqual(again['concept_title'], "Pythagorean theorem")
        self.assertEqual(again['sensory_text'], "a glowing red triangle")

    def test_anchor_is_attached_to_its_own_locus_only(self):
        first = self._start().get_json()
        second = self._next(first['uid']).get_json()
        self._anchor(first['uid'], "Only here", "vivid")
        self.assertFalse(self._next(first['uid']).get_json()['has_concept'],
                         "an anchor must not leak to a neighbouring locus")
        self.assertEqual(second['index'], 1)

    def test_re_anchoring_replaces_the_earlier_memory(self):
        locus = self._start().get_json()['uid']
        self._anchor(locus, "First idea", "first image")
        self._anchor(locus, "Second idea", "second image")
        body = self._start().get_json()
        self.assertEqual(body['concept_title'], "Second idea",
                         "the most recent anchor is the one that must win")

    def test_anchor_requires_a_locus_and_a_concept(self):
        self.assertEqual(self._anchor("", "something").status_code, 400)
        self.assertEqual(
            self._anchor("con_00000000", "").status_code, 400)

    def test_anchors_survive_a_fresh_read_of_storage(self):
        """Anchors go through activity_log; confirm they are really persisted
        rather than held in a request-scoped cache."""
        from services.common.storage import StorageManager
        locus = self._start().get_json()['uid']
        self._anchor(locus, "Durable idea", "engraved")
        fresh = StorageManager(data_dir=self.dir)
        acts = fresh.activity.get_activities(
            course_uid=COURSE_UID, activity_type="palace_anchor")
        details = [json.loads(a["details"]) if isinstance(a["details"], str)
                   else a["details"] for a in acts]
        self.assertTrue(any(d.get("concept_text") == "Durable idea"
                            for d in details))


if __name__ == "__main__":
    unittest.main()
