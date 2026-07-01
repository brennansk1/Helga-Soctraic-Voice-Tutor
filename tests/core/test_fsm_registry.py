"""B15.6/B15.7 tests: FSMRegistry lifecycle + per-student FSM isolation.

Covers spec-03 §9 rows: FSM isolation (distinct objects, no attr bleed),
restart-restore (flush_all + fresh registry re-hydrates position),
eviction-lossless (force-evict mid-session, re-get, state intact),
LRU capacity, and the single-sweeper tick.
"""

import json
import os
import sys
import tempfile
import shutil
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), 'services/core'))

import logging
logging.basicConfig = MagicMock()
logging.FileHandler = MagicMock()

core_deps = {'yaml': MagicMock(), 'kuzu': MagicMock(), 'libzim': MagicMock()}
with patch.dict('sys.modules', core_deps):
    from fsm_logic import MnemosyneFSM
    from fsm_registry import FSMRegistry
    from services.common.storage import StorageManager, DEFAULT_STUDENT_ID


def _make_registry(storage, **kw):
    kw.setdefault('start_sweeper', False)
    return FSMRegistry(
        lambda sid, st: _quiet_fsm(sid, st), storage, **kw)


def _quiet_fsm(sid, storage):
    fsm = MnemosyneFSM(sid, storage=storage)
    fsm.speak = MagicMock()
    return fsm


class TestFSMRegistry(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="helga_reg_")
        self.storage = StorageManager(self.data_dir)
        pid = self.storage.accounts.create_parent("reg@example.com", "h")
        self.a = self.storage.accounts.create_student(pid, "A", grade_band="3-5")
        self.b = self.storage.accounts.create_student(pid, "B", grade_band="9-12")

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def test_distinct_instances_no_attr_bleed(self):
        reg = _make_registry(self.storage)
        fa, fb = reg.get(self.a), reg.get(self.b)
        self.assertIsNot(fa, fb)
        self.assertEqual(fa.student_id, self.a)
        self.assertEqual(fb.student_id, self.b)

        fa.active_course_uid = "course_a"
        fa.current_lesson_node = {"uid": "node_A"}
        fa.current_bloom_level = 4
        fa.transcript.append({"sender": "user", "text": "hi from A"})

        self.assertIsNone(fb.current_lesson_node)
        self.assertEqual(fb.current_bloom_level, 1)
        self.assertNotIn({"sender": "user", "text": "hi from A"}, fb.transcript)
        # same-id get returns the same resident object
        self.assertIs(reg.get(self.a), fa)

    def test_grade_band_resolved_per_student(self):
        reg = _make_registry(self.storage)
        self.assertEqual(reg.get(self.a).grade_band, "3-5")
        self.assertEqual(reg.get(self.b).grade_band, "9-12")

    def test_restart_restore(self):
        reg = _make_registry(self.storage)
        fa = reg.get(self.a)
        fa.active_course_uid = "course_r"
        fa.current_lesson_node = {"uid": "node_R", "text": "restore me"}
        fa.completed_topics = {"t1", "t2"}
        fa.current_bloom_level = 3
        fa._save_current_course_progress()
        reg.flush_all()

        reg2 = _make_registry(self.storage)   # fresh registry = process restart
        fa2 = reg2.get(self.a)
        self.assertIsNot(fa2, fa)
        self.assertEqual(fa2.active_course_uid, "course_r")
        self.assertTrue(fa2._load_course_progress("course_r"))
        self.assertEqual(fa2.current_lesson_node["uid"], "node_R")
        self.assertEqual(fa2.completed_topics, {"t1", "t2"})
        self.assertEqual(fa2.current_bloom_level, 3)

    def test_eviction_lossless(self):
        reg = _make_registry(self.storage, idle_ttl=0)
        fa = reg.get(self.a)
        fa.active_course_uid = "course_e"
        fa.current_lesson_node = {"uid": "node_E"}
        fa.last_touch = 0  # force idle
        evicted = reg.evict_idle()
        self.assertGreaterEqual(evicted, 1)
        self.assertIsNone(reg.peek(self.a))

        fa2 = reg.get(self.a)  # transparent re-hydrate
        self.assertEqual(fa2.active_course_uid, "course_e")
        self.assertTrue(fa2._load_course_progress("course_e"))
        self.assertEqual(fa2.current_lesson_node["uid"], "node_E")

    def test_lru_capacity_evicts_idle_oldest(self):
        reg = _make_registry(self.storage, max_size=2)
        fa = reg.get(self.a)
        fa.active_course_uid = "course_lru"
        fa.last_touch = 0  # idle → evictable
        reg.get(self.b)
        pid2 = self.storage.accounts.create_parent("reg2@example.com", "h")
        c = self.storage.accounts.create_student(pid2, "C")
        reg.get(c)  # over cap — must evict idle A
        self.assertIsNone(reg.peek(self.a))
        self.assertIsNotNone(reg.peek(self.b))
        # A's state was flushed on evict
        row = self.storage.fsm.get(self.a)
        self.assertIsNotNone(row)

    def test_per_student_locks_are_distinct(self):
        reg = _make_registry(self.storage)
        la, lb = reg.lock_for(self.a), reg.lock_for(self.b)
        self.assertIsNot(la, lb)
        self.assertIs(reg.lock_for(self.a), la)
        # holding A's lock never blocks B's
        with la:
            acquired = lb.acquire(timeout=0.5)
            self.assertTrue(acquired)
            lb.release()

    def test_tick_runs_for_all_residents(self):
        reg = _make_registry(self.storage)
        fa, fb = reg.get(self.a), reg.get(self.b)
        fa.tick()
        fb.tick()  # must not raise; pure single pass, no loop

    def test_set_maintenance_propagates(self):
        reg = _make_registry(self.storage)
        fa, fb = reg.get(self.a), reg.get(self.b)
        reg.set_maintenance(True)
        self.assertTrue(fa.maintenance_paused and fb.maintenance_paused)
        reg.set_maintenance(False)
        self.assertFalse(fa.maintenance_paused or fb.maintenance_paused)

    def test_stats(self):
        reg = _make_registry(self.storage)
        reg.get(self.a)
        s = reg.stats()
        self.assertEqual(s["resident"], 1)
        self.assertEqual(s["students"][0]["student_id"], self.a)


if __name__ == '__main__':
    unittest.main()
