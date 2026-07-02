"""xAPI learning-analytics statements (B27.3).

Translates Helga's durable records (activity_log, exam_attempts, xp_ledger)
into xAPI-format statements (actor / verb / object / result) so external
dashboards and LRS tooling can consume learning events without a bespoke
schema. Actor identity is the opaque student_id — no name or email leaves
the system (COPPA data minimization).
"""

VERBS = {
    "socratic_answer": "https://adlnet.gov/expapi/verbs/answered",
    "session": "https://adlnet.gov/expapi/verbs/attempted",
    "review": "https://adlnet.gov/expapi/verbs/reviewed",
    "concept_completed": "https://adlnet.gov/expapi/verbs/mastered",
    "exam": "https://adlnet.gov/expapi/verbs/completed",
}


def _actor(student_id):
    return {"objectType": "Agent",
            "account": {"homePage": "https://helga.local", "name": student_id}}


def _statement(student_id, verb_key, object_id, object_name,
               timestamp, result=None):
    stmt = {
        "actor": _actor(student_id),
        "verb": {"id": VERBS.get(verb_key, VERBS["session"]),
                 "display": {"en-US": verb_key}},
        "object": {"objectType": "Activity",
                   "id": f"https://helga.local/xapi/{object_id}",
                   "definition": {"name": {"en-US": object_name or object_id}}},
        "timestamp": timestamp,
    }
    if result:
        stmt["result"] = result
    return stmt


def statements_for_student(storage, student_id, since=None, limit=500):
    """xAPI statements from the student's records, newest first."""
    out = []
    for a in storage.activity.get_activities(start_date=since,
                                             student_id=student_id)[:limit]:
        result = None
        if a.get("grade") is not None:
            result = {"score": {"raw": a["grade"], "min": 1, "max": 4},
                      "success": a["grade"] >= 3}
        if a.get("duration_seconds"):
            result = result or {}
            result["duration"] = f"PT{int(a['duration_seconds'])}S"
        out.append(_statement(
            student_id, a.get("activity_type", "session"),
            a.get("concept_uid") or a.get("course_uid") or "unknown",
            a.get("concept_uid") or a.get("course_uid"),
            a.get("created_at"), result))

    conn = storage.progress._get_db()
    rows = conn.execute(
        "SELECT a.*, e.kind, e.pass_threshold FROM exam_attempts a "
        "JOIN exams e ON e.id = a.exam_id "
        "WHERE a.student_id = ? AND a.status = 'graded' "
        "ORDER BY a.submitted_at DESC LIMIT ?", (student_id, limit)).fetchall()
    for r in rows:
        out.append(_statement(
            student_id, "exam", r["exam_id"], f"{r['kind']} exam",
            r["submitted_at"],
            {"score": {"scaled": r["score"]},
             "success": bool(r["passed"])}))
    out.sort(key=lambda s: s.get("timestamp") or "", reverse=True)
    return out[:limit]
