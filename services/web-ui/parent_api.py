"""Parent dashboard API (B19.1–B19.6 + FE6, design spec 06).

Role-gated blueprint under /parent/*. Every per-child endpoint passes the
ownership guard first (unknown id → 404, foreign id → 403, spec 03 §8.1) and
writes a FERPA audit row (spec 06 §1.3).
"""

import json
import logging
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from auth import (current_parent_id, owns_student_required, parent_required,
                  hash_secret)

logger = logging.getLogger(__name__)

GRADE_BANDS = ("K-1", "2-3", "4-5", "6-8", "9-12")
STATUS_ORDER = {"not_started": 0, "in_progress": 1, "mastered": 2}


def create_parent_blueprint(get_storage):
    bp = Blueprint("parent", __name__, url_prefix="/parent")

    def _st():
        return get_storage()

    def _audit(action, subject=None, detail=None):
        try:
            _st().audit.record(action, actor_id=current_parent_id(),
                               actor_role="parent", subject_student_id=subject,
                               detail=detail, ip_address=request.remote_addr)
        except Exception as e:
            logger.warning(f"audit write failed: {e}")

    # ------------------------------------------------------------- pages

    @bp.route("")
    @bp.route("/")
    @parent_required
    def parent_home():
        from flask import redirect
        return redirect("/parent/children")

    @bp.route("/children")
    @parent_required
    def children_page():
        return render_template("parent/children.html")

    @bp.route("/children/<student_id>")
    @owns_student_required("student_id")
    def child_detail_page(student_id):
        student = _st().accounts.get_student(student_id)
        return render_template("parent/child_detail.html", student=student)

    @bp.route("/approvals")
    @parent_required
    def approvals_page():
        return render_template("parent/approvals.html")

    @bp.route("/students")
    @parent_required
    def manage_students_page():
        return render_template("parent/manage_students.html")

    @bp.route("/account")
    @parent_required
    def account_page():
        return render_template("parent/account.html")

    # -------------------------------------------- B19.1 children overview

    def _child_aggregates(st, sid):
        active = st.enrollments.list_for_student(sid, status="active")
        completed = 0
        total = 0
        for e in active:
            course_uid = e["course_uid"]
            stats = (st.courses.get_course_stats(course_uid)
                     or st.catalog_courses.get_course_stats(course_uid) or {})
            n = stats.get("concepts", 0) or 0
            total += n
            completed += len([p for p in st.progress.get_course_progress(
                course_uid, student_id=sid) if p.get("status") == "completed"])
        pending = st.enrollments.list_for_student(sid, status="pending_approval")
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        acts = st.activity.get_activities(start_date=week_ago, student_id=sid)
        secs = sum(a.get("duration_seconds") or 0 for a in acts)
        return {
            "active_courses": len(active),
            "mastery_pct": round(100 * completed / max(total, 1)),
            "streak_days": st.activity.get_streak(student_id=sid),
            "time_on_task_7d_minutes": round(secs / 60),
            "due_reviews": len(st.progress.get_due_reviews(student_id=sid)),
            "pending_approvals": len(pending),
        }

    @bp.route("/api/children", methods=["GET"])
    @parent_required
    def api_children():
        st = _st()
        pid = current_parent_id()
        children = []
        for s in st.accounts.list_students(pid):
            row = {"student_id": s["id"], "display_name": s["display_name"],
                   "grade_band": s["grade_band"], "avatar_url": s.get("avatar_url")}
            row.update(_child_aggregates(st, s["id"]))
            children.append(row)
        _audit("view_progress", detail={"children": [c["student_id"] for c in children]})
        return jsonify({"children": children})

    # ------------------------------------------ B19.2 per-child detail

    def _concept_index(st, sid):
        """concept_uid → (course_uid, title) across the student's enrollments
        (spec 06 §3.1 option B: structure walk, no per-concept SQL loop)."""
        index = {}
        for e in st.enrollments.list_for_student(sid):
            if e["status"] not in ("active", "completed"):
                continue
            uid = e["course_uid"]
            course = st.courses.get_course(uid) or st.catalog_courses.get_course(uid)
            if not course:
                continue
            for con in (st.courses.get_flat_concepts(uid)
                        or st.catalog_courses.get_flat_concepts(uid) or []):
                index[con["uid"]] = (uid, con.get("title", ""))
        return index

    @bp.route("/api/children/<student_id>/standards", methods=["GET"])
    @owns_student_required("student_id")
    def api_child_standards(student_id):
        st = _st()
        index = _concept_index(st, student_id)
        progress = {}
        for e in st.enrollments.list_for_student(student_id):
            for p in st.progress.get_course_progress(e["course_uid"], student_id=student_id):
                progress[p["concept_uid"]] = p

        by_standard = {}
        for concept_uid in index:
            for tag in st.standards.standards_for_concept(concept_uid):
                code = tag["standard_code"]
                p = progress.get(concept_uid)
                status = ("mastered" if p and p.get("status") == "completed"
                          else "in_progress" if p else "not_started")
                by_standard.setdefault(code, {"meta": tag, "statuses": []})
                by_standard[code]["statuses"].append(status)

        subjects = {}
        for code, data in by_standard.items():
            statuses = data["statuses"]
            # conservative roll-up: mastered iff ALL concepts mastered
            if all(s == "mastered" for s in statuses):
                rollup = "mastered"
            elif any(s != "not_started" for s in statuses):
                rollup = "in_progress"
            else:
                rollup = "not_started"
            meta = st.standards.get(code) or {}
            subj = meta.get("subject", "other")
            strand = meta.get("strand", "")
            subjects.setdefault(subj, {"strands": {}, "mastered": 0, "total": 0})
            subjects[subj]["strands"].setdefault(strand, []).append({
                "code": code, "text": meta.get("text", ""),
                "is_enrichment": meta.get("is_enrichment", 0),
                "status": rollup, "concepts": len(statuses),
            })
            subjects[subj]["total"] += 1
            if rollup == "mastered":
                subjects[subj]["mastered"] += 1
        for subj in subjects.values():
            subj["coverage_pct"] = round(100 * subj["mastered"] / max(subj["total"], 1))
        _audit("view_progress", subject=student_id, detail={"view": "standards"})
        return jsonify({"subjects": subjects})

    @bp.route("/api/children/<student_id>/bloom", methods=["GET"])
    @owns_student_required("student_id")
    def api_child_bloom(student_id):
        st = _st()
        histogram = {i: 0 for i in range(1, 7)}
        for e in st.enrollments.list_for_student(student_id):
            for p in st.progress.get_course_progress(e["course_uid"], student_id=student_id):
                if p.get("status") == "completed":
                    lvl = int(p.get("bloom_level") or 1)
                    histogram[max(1, min(6, lvl))] += 1
        return jsonify({"bloom_histogram": histogram})

    @bp.route("/api/children/<student_id>/timeline", methods=["GET"])
    @owns_student_required("student_id")
    def api_child_timeline(student_id):
        st = _st()
        days = min(90, int(request.args.get("days", 30)))
        start = (date.today() - timedelta(days=days)).isoformat()
        acts = st.activity.get_activities(start_date=start, student_id=student_id)
        by_day = {}
        for a in acts:
            day = (a.get("created_at") or "")[:10]
            d = by_day.setdefault(day, {"day": day, "events": 0, "seconds": 0,
                                        "grades": []})
            d["events"] += 1
            d["seconds"] += a.get("duration_seconds") or 0
            if a.get("grade") is not None:
                d["grades"].append(a["grade"])
        timeline = []
        for day in sorted(by_day, reverse=True):
            d = by_day[day]
            grades = d.pop("grades")
            d["minutes"] = round(d.pop("seconds") / 60)
            d["avg_grade"] = round(sum(grades) / len(grades), 2) if grades else None
            timeline.append(d)
        _audit("view_progress", subject=student_id, detail={"view": "timeline"})
        return jsonify({"timeline": timeline})

    @bp.route("/api/children/<student_id>/struggles", methods=["GET"])
    @owns_student_required("student_id")
    def api_child_struggles(student_id):
        """≥3 low (grade<3) attempts on one concept in 30d AND ≥50% low
        (spec 06 §3.4 — shared with the B24.4 struggle alert)."""
        st = _st()
        start = (date.today() - timedelta(days=30)).isoformat()
        acts = st.activity.get_activities(start_date=start, student_id=student_id)
        per_concept = {}
        for a in acts:
            if a.get("grade") is None or not a.get("concept_uid"):
                continue
            c = per_concept.setdefault(a["concept_uid"],
                                       {"attempts": 0, "low": 0, "last_seen": ""})
            c["attempts"] += 1
            if a["grade"] < 3:
                c["low"] += 1
            c["last_seen"] = max(c["last_seen"], a.get("created_at") or "")
        index = _concept_index(st, student_id)
        struggles = []
        for uid, c in per_concept.items():
            if c["low"] >= 3 and c["low"] / c["attempts"] >= 0.5:
                course_uid, title = index.get(uid, (None, uid))
                struggles.append({"concept_uid": uid, "title": title,
                                  "course_uid": course_uid, **c})
        struggles.sort(key=lambda s: -s["low"])
        return jsonify({"struggles": struggles})

    @bp.route("/api/children/<student_id>/courses", methods=["GET"])
    @owns_student_required("student_id")
    def api_child_courses(student_id):
        st = _st()
        out = []
        for e in st.enrollments.list_for_student(student_id):
            uid = e["course_uid"]
            course = st.courses.get_course(uid) or st.catalog_courses.get_course(uid) or {}
            stats = (st.courses.get_course_stats(uid)
                     or st.catalog_courses.get_course_stats(uid) or {})
            total = stats.get("concepts", 0) or 0
            pct = st.progress.get_completion_percentage(uid, total, student_id=student_id)
            out.append({"course_uid": uid, "title": course.get("title", uid),
                        "kind": e.get("course_kind"), "status": e["status"],
                        "completion_pct": round(pct)})
        return jsonify({"courses": out})

    # ------------------------------------------ B19.3 elective approvals

    @bp.route("/api/approvals", methods=["GET"])
    @parent_required
    def api_approvals():
        st = _st()
        pid = current_parent_id()
        queue = []
        for s in st.accounts.list_students(pid):
            for e in st.enrollments.list_for_student(s["id"], status="pending_approval"):
                course = (st.courses.get_course(e["course_uid"])
                          or st.catalog_courses.get_course(e["course_uid"]) or {})
                queue.append({"enrollment_id": e["id"], "student_id": s["id"],
                              "student_name": s["display_name"],
                              "course_uid": e["course_uid"],
                              "course_title": course.get("title", e["course_uid"]),
                              "requested_at": e.get("enrolled_at")})
        return jsonify({"approvals": queue})

    def _find_enrollment(st, enrollment_id):
        """Resolve an enrollment id to (student, enrollment) within THIS
        parent's family — foreign families' enrollments look like 404."""
        pid = current_parent_id()
        for s in st.accounts.list_students(pid, include_archived=True):
            for e in st.enrollments.list_for_student(s["id"]):
                if e["id"] == enrollment_id:
                    return s, e
        return None, None

    @bp.route("/api/enrollments/<enrollment_id>/approve", methods=["POST"])
    @parent_required
    def approve_enrollment(enrollment_id):
        st = _st()
        student, enr = _find_enrollment(st, enrollment_id)
        if not enr:
            return jsonify({"error": "not found"}), 404
        if enr["status"] != "pending_approval":
            return jsonify({"error": f"not pending (status={enr['status']})"}), 409
        st.enrollments.update(student["id"], enr["course_uid"], status="active",
                              approved_by=current_parent_id(),
                              approved_at=datetime.utcnow().isoformat())
        st.notifications.create(student["id"], "student", "elective_request",
                                title="Your course was approved!",
                                ref_uid=enr["course_uid"])
        _audit("consent_change", subject=student["id"],
               detail={"approve": enr["course_uid"]})
        return jsonify({"status": "active"})

    @bp.route("/api/enrollments/<enrollment_id>/deny", methods=["POST"])
    @parent_required
    def deny_enrollment(enrollment_id):
        st = _st()
        student, enr = _find_enrollment(st, enrollment_id)
        if not enr:
            return jsonify({"error": "not found"}), 404
        if enr["status"] != "pending_approval":
            return jsonify({"error": f"not pending (status={enr['status']})"}), 409
        st.enrollments.update(student["id"], enr["course_uid"], status="denied")
        _audit("consent_change", subject=student["id"],
               detail={"deny": enr["course_uid"],
                       "reason": (request.get_json(silent=True) or {}).get("reason")})
        return jsonify({"status": "denied"})

    # ------------------------------------------ B19.4 manage students

    @bp.route("/api/students", methods=["GET"])
    @parent_required
    def api_students():
        st = _st()
        pid = current_parent_id()
        students = st.accounts.list_students(pid, include_archived=True)
        seats = st.subscriptions.seats_for(pid)
        return jsonify({"students": students,
                        "seats": {"used": st.accounts.count_active_students(pid),
                                  "max": seats}})

    @bp.route("/api/students/<student_id>", methods=["PATCH"])
    @owns_student_required("student_id")
    def api_update_student(student_id):
        st = _st()
        data = request.get_json(force=True)
        allowed = {}
        if "display_name" in data and data["display_name"].strip():
            allowed["display_name"] = data["display_name"].strip()
        if data.get("grade_band") in GRADE_BANDS:
            allowed["grade_band"] = data["grade_band"]
        if "interests" in data and isinstance(data["interests"], list):
            allowed["interests"] = data["interests"][:20]
        if data.get("status") in ("active", "archived"):
            if data["status"] == "active":
                # un-archive counts against seats
                pid = current_parent_id()
                if (st.accounts.count_active_students(pid)
                        >= st.subscriptions.seats_for(pid)):
                    return jsonify({"error": "seat limit reached"}), 402
            allowed["status"] = data["status"]
        if data.get("pin"):
            pin = str(data["pin"])
            if not (pin.isdigit() and len(pin) == 4):
                return jsonify({"error": "PIN must be 4 digits"}), 400
            allowed["pin_hash"] = hash_secret(pin)
        st.accounts.update_student(student_id, **allowed)
        if "accommodations" in data and isinstance(data["accommodations"], dict):
            st.accommodations.set(student_id, set_by=current_parent_id(),
                                  **data["accommodations"])
        return jsonify({"status": "ok"})

    @bp.route("/api/notifications/unread_count", methods=["GET"])
    @parent_required
    def api_unread_count():
        return jsonify({"unread": _st().notifications.unread_count(current_parent_id())})

    # ------------------------------------------ B19.6 account / consent / data

    @bp.route("/api/consents", methods=["GET"])
    @parent_required
    def api_consents():
        return jsonify({"consents": _st().consent.list_for_parent(current_parent_id())})

    @bp.route("/api/children/<student_id>/export", methods=["POST"])
    @owns_student_required("student_id")
    def api_export_child(student_id):
        """FERPA/Utah data export (B19.6/B21.2): every record we hold on the
        learner, as JSON. The PDF report (B19.5) renders from this payload."""
        st = _st()
        export = {
            "exported_at": datetime.utcnow().isoformat(),
            "student": st.accounts.get_student(student_id),
            "enrollments": st.enrollments.list_for_student(student_id),
            "progress": [],
            "activity": st.activity.get_activities(student_id=student_id),
            "flashcards": st.flashcards.get_due_cards(
                target_date="9999-12-31", student_id=student_id),
            "accommodations": st.accommodations.get(student_id),
            "fsm_session": st.fsm.get(student_id),
        }
        for e in export["enrollments"]:
            export["progress"].extend(
                st.progress.get_course_progress(e["course_uid"], student_id=student_id))
        _audit("export_data", subject=student_id)
        return jsonify(export)

    @bp.route("/api/children/<student_id>/delete", methods=["POST"])
    @owns_student_required("student_id")
    def api_delete_child(student_id):
        """Right-to-delete (B21.2): archive the profile and purge per-student
        rows. Requires explicit confirmation in the body."""
        if (request.get_json(silent=True) or {}).get("confirm") != student_id:
            return jsonify({"error": "confirmation required: pass confirm=<student_id>"}), 400
        st = _st()
        conn = st.progress._get_db()
        for table in ("user_progress", "activity_log", "scheduled_reviews",
                      "flashcards", "fsm_sessions", "enrollments",
                      "exam_attempts", "xp_ledger", "student_badges",
                      "student_quests", "notifications"):
            try:
                col = "recipient_id" if table == "notifications" else "student_id"
                conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (student_id,))
            except Exception as e:
                logger.warning(f"delete from {table} failed: {e}")
        conn.commit()
        st.accounts.update_student(student_id, status="deleted")
        _audit("delete_data", subject=student_id)
        return jsonify({"status": "deleted"})

    return bp
