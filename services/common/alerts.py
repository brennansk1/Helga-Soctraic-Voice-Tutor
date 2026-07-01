"""Struggle / inactivity alert scan (B24.4, design spec 06 §3.4 + 11).

Run nightly (cron / night audit) per family. Shares the exact struggle
thresholds with the parent dashboard: ≥3 low (grade<3) graded attempts on one
concept in the trailing 30 days AND ≥50% of that concept's graded attempts
low. Inactivity: a previously-active student with zero activity for 7+ days.

Alerts dedupe against unread notifications so a parent is nudged once per
issue, not nightly.
"""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

STRUGGLE_MIN_LOW = 3
STRUGGLE_LOW_RATIO = 0.5
INACTIVITY_DAYS = 7


def _struggling_concepts(storage, student_id):
    start = (date.today() - timedelta(days=30)).isoformat()
    acts = storage.activity.get_activities(start_date=start, student_id=student_id)
    per_concept = {}
    for a in acts:
        if a.get("grade") is None or not a.get("concept_uid"):
            continue
        c = per_concept.setdefault(a["concept_uid"], {"attempts": 0, "low": 0})
        c["attempts"] += 1
        if a["grade"] < 3:
            c["low"] += 1
    return [uid for uid, c in per_concept.items()
            if c["low"] >= STRUGGLE_MIN_LOW
            and c["low"] / c["attempts"] >= STRUGGLE_LOW_RATIO]


def _is_inactive(storage, student_id):
    """True when the student HAS a history but nothing in the last week."""
    recent = storage.activity.get_activities(
        start_date=(date.today() - timedelta(days=INACTIVITY_DAYS)).isoformat(),
        student_id=student_id)
    if recent:
        return False
    ever = storage.activity.get_activities(student_id=student_id)
    return bool(ever)


def _already_alerted(storage, parent_id, ref_uid, kind):
    return any(n["kind"] == kind and n["ref_uid"] == ref_uid
               for n in storage.notifications.list_for(parent_id, unread_only=True))


def run_alert_scan(storage):
    """One pass over every active student. Returns counts for logging/cron."""
    result = {"students": 0, "struggle_alerts": 0, "inactivity_alerts": 0}
    conn = storage.progress._get_db()
    parents = [r["id"] for r in conn.execute(
        "SELECT id FROM parents WHERE status = 'active'").fetchall()]
    for pid in parents:
        for student in storage.accounts.list_students(pid):
            sid = student["id"]
            result["students"] += 1
            name = student.get("display_name", "your learner")

            struggles = _struggling_concepts(storage, sid)
            if struggles and not _already_alerted(storage, pid, sid, "struggle_alert"):
                storage.notifications.create(
                    pid, "parent", "struggle_alert",
                    title=f"{name} is stuck on {len(struggles)} concept(s)",
                    body=(f"{name} has had repeated low grades on "
                          f"{len(struggles)} concept(s) this month. A short "
                          "check-in or a different explanation usually helps."),
                    ref_uid=sid)
                result["struggle_alerts"] += 1

            if _is_inactive(storage, sid) and not _already_alerted(
                    storage, pid, sid, "system"):
                storage.notifications.create(
                    pid, "parent", "system",
                    title=f"{name} hasn't practiced in a week",
                    body=(f"{name} has no learning activity in the last "
                          f"{INACTIVITY_DAYS} days. A small nudge keeps the "
                          "streak habit alive."),
                    ref_uid=sid)
                result["inactivity_alerts"] += 1
    logger.info(f"Alert scan: {result}")
    return result
