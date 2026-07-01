"""B27.5 (circuit breaker), B22.3 (badges/quests), B24.4 (alert scan) tests."""

import os
import sys
import time
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../services/core')))

from gpu_gate import OllamaBreaker
from services.common.storage import StorageManager
from services.common.alerts import run_alert_scan


@pytest.fixture
def storage(tmp_path):
    return StorageManager(str(tmp_path))


@pytest.fixture
def family(storage):
    pid = storage.accounts.create_parent("rel@example.com", "h", status="active")
    sid = storage.accounts.create_student(pid, "Kid")
    return pid, sid


class TestCircuitBreaker:
    def test_trips_after_consecutive_failures(self):
        b = OllamaBreaker(trip_after=3, probe_interval=60)
        for _ in range(2):
            b.record_failure()
        assert b.state == "closed" and b.allow()
        b.record_failure()
        assert b.state == "open" and not b.allow()

    def test_success_resets_failure_count(self):
        b = OllamaBreaker(trip_after=3)
        b.record_failure()
        b.record_failure()
        b.record_success()
        b.record_failure()
        b.record_failure()
        assert b.state == "closed"

    def test_half_open_probe_then_close(self):
        b = OllamaBreaker(trip_after=1, probe_interval=0.05)
        b.record_failure()
        assert not b.allow()
        time.sleep(0.06)
        assert b.allow()            # single probe admitted
        assert not b.allow()        # everything else still fast-fails
        b.record_success()
        assert b.state == "closed" and b.allow()

    def test_half_open_failure_reopens(self):
        b = OllamaBreaker(trip_after=1, probe_interval=0.05)
        b.record_failure()
        time.sleep(0.06)
        assert b.allow()
        b.record_failure()
        assert b.state == "open" and not b.allow()


class TestBadges:
    def test_streak_badge_awarded_once_with_xp(self, storage, family):
        _, sid = family
        conn = storage.gamification._get_db()
        storage.gamification._row(sid)
        conn.execute("UPDATE student_gamification SET streak_days=3 WHERE student_id=?", (sid,))
        conn.commit()
        newly = storage.gamification.check_and_award_badges(student_id=sid)
        assert [b["id"] for b in newly] == ["bdg_streak_3"]
        assert storage.gamification.get(student_id=sid)["total_xp"] == 25
        # second scan: no re-award
        assert storage.gamification.check_and_award_badges(student_id=sid) == []
        badges = storage.gamification.badges_for(student_id=sid)
        assert any(b["id"] == "bdg_streak_3" for b in badges["unlocked"])

    def test_exam_pass_badge_from_ledger(self, storage, family):
        _, sid = family
        storage.gamification.award_xp("exam_pass", student_id=sid, ref_uid="exm_1")
        newly = storage.gamification.check_and_award_badges(student_id=sid)
        assert "bdg_exam_pass" in [b["id"] for b in newly]

    def test_badges_are_per_student(self, storage, family):
        pid, sid = family
        other = storage.accounts.create_student(pid, "Other")
        storage.gamification.award_xp("exam_pass", student_id=sid)
        storage.gamification.check_and_award_badges(student_id=sid)
        other_badges = storage.gamification.badges_for(student_id=other)
        assert other_badges["unlocked"] == []


class TestQuests:
    def test_daily_quest_progress_and_completion(self, storage, family):
        _, sid = family
        quests = storage.gamification.quests_for(student_id=sid)
        assert {q["id"] for q in quests} == {"qst_answer_5", "qst_review_3"}
        completed = []
        for _ in range(5):
            completed = storage.gamification.increment_quest("answer", student_id=sid)
        assert [q["id"] for q in completed] == ["qst_answer_5"]
        # quest XP awarded exactly once
        assert storage.gamification.get(student_id=sid)["total_xp"] == 20
        # no further increments once completed
        assert storage.gamification.increment_quest("answer", student_id=sid) == []

    def test_quests_isolated_per_student(self, storage, family):
        pid, sid = family
        other = storage.accounts.create_student(pid, "Q2")
        storage.gamification.increment_quest("review", student_id=sid)
        theirs = storage.gamification.quests_for(student_id=other)
        assert all(q["progress"] == 0 for q in theirs)


class TestAlertScan:
    def _log_low_grades(self, storage, sid, concept="con_hard", n=4, low=3):
        grades = [1] * low + [3] * (n - low)
        for g in grades:
            storage.activity.log_activity("course_x", "socratic_answer",
                                          concept_uid=concept, grade=g,
                                          student_id=sid)

    def test_struggle_alert_created_once(self, storage, family):
        pid, sid = family
        self._log_low_grades(storage, sid)
        r1 = run_alert_scan(storage)
        assert r1["struggle_alerts"] == 1
        alerts = storage.notifications.list_for(pid, unread_only=True)
        assert any(a["kind"] == "struggle_alert" and a["ref_uid"] == sid
                   for a in alerts)
        # nightly re-run does not spam while unread
        r2 = run_alert_scan(storage)
        assert r2["struggle_alerts"] == 0

    def test_no_alert_below_thresholds(self, storage, family):
        pid, sid = family
        self._log_low_grades(storage, sid, n=5, low=2)   # 2 low of 5 → no flag
        r = run_alert_scan(storage)
        assert r["struggle_alerts"] == 0

    def test_inactivity_alert_only_for_previously_active(self, storage, family):
        pid, sid = family
        # brand-new student with no history → no inactivity alert
        r = run_alert_scan(storage)
        assert r["inactivity_alerts"] == 0
        # backdate an activity 10 days ago
        conn = storage.activity._get_db()
        old = (date.today() - timedelta(days=10)).isoformat() + "T10:00:00"
        conn.execute(
            "INSERT INTO activity_log (course_uid, activity_type, created_at, student_id) "
            "VALUES ('course_x', 'session', ?, ?)", (old, sid))
        conn.commit()
        r = run_alert_scan(storage)
        assert r["inactivity_alerts"] == 1
