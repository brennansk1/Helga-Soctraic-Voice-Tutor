"""B19/FE6 tests: children overview aggregates, standards coverage roll-up,
struggles, elective approval workflow, seat enforcement, export/delete.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock

_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, '../../../'))
_webui = os.path.join(_root, 'services/web-ui')
for p in (_root, _webui):
    if p not in sys.path:
        sys.path.insert(0, p)

for _mod in ("gevent", "gevent.monkey", "socketio", "flask_socketio"):
    if isinstance(sys.modules.get(_mod), MagicMock):
        del sys.modules[_mod]
        sys.modules.pop("app", None)
        sys.modules.pop("auth", None)

import app as webui_app  # noqa: E402

app = webui_app.app
app.config['TESTING'] = True
storage = webui_app._get_storage()


def _parent_client(email):
    client = app.test_client()
    r = client.post('/signup', json={'email': email, 'password': 'longenough1'})
    assert r.status_code == 201
    return client, r.get_json()['parent_id']


def _seed_course(uid="course_pd000001"):
    if storage.courses.get_course(uid):
        return uid
    storage.courses.create_course({
        "uid": uid, "title": "Ratios", "status": "ready",
        "modules": [{"uid": "mod_1", "title": "M1", "units": [
            {"uid": "unit_1", "title": "U1", "lessons": [
                {"uid": "less_1", "title": "L1", "concepts": [
                    {"uid": "con_pd_c1", "title": "Ratio Notation"},
                    {"uid": "con_pd_c2", "title": "Unit Rate"}]}]}]}]})
    return uid


class TestChildrenOverview(unittest.TestCase):
    def test_overview_aggregates(self):
        client, pid = _parent_client("pd1@example.com")
        r = client.post('/api/students', json={'display_name': 'Maya', 'grade_band': '6-8'})
        sid = r.get_json()['student_id']
        uid = _seed_course()
        storage.enrollments.enroll(sid, uid)
        storage.progress.update_progress("con_pd_c1", uid, student_id=sid, status="completed")
        storage.activity.log_activity(uid, "session", duration_seconds=600, student_id=sid)

        data = client.get('/parent/api/children').get_json()
        child = next(c for c in data['children'] if c['student_id'] == sid)
        assert child['active_courses'] == 1
        assert child['mastery_pct'] == 50           # 1 of 2 concepts
        assert child['time_on_task_7d_minutes'] == 10
        assert child['streak_days'] == 1

    def test_anon_gets_401(self):
        r = app.test_client().get('/parent/api/children')
        assert r.status_code == 401


class TestStandardsCoverage(unittest.TestCase):
    def test_conservative_rollup(self):
        client, pid = _parent_client("pd2@example.com")
        sid = client.post('/api/students', json={'display_name': 'Sam'}).get_json()['student_id']
        uid = _seed_course("course_pd000002")
        # course structure reuses the same concept uids per _seed_course? use unique
        storage.courses.create_course({
            "uid": "course_pd000003", "title": "R2", "status": "ready",
            "modules": [{"uid": "m", "title": "M", "units": [
                {"uid": "u", "title": "U", "lessons": [
                    {"uid": "l", "title": "L", "concepts": [
                        {"uid": "con_std_a", "title": "A"},
                        {"uid": "con_std_b", "title": "B"}]}]}]}]})
        storage.standards.upsert("6.RP", "math", "Ratios", "Ratio reasoning.",
                                 grade_band="6-8")
        storage.standards.sync_concept_standards(
            [], [{"concept_uid": "con_std_a", "standard_code": "6.RP"},
                 {"concept_uid": "con_std_b", "standard_code": "6.RP"}])
        storage.enrollments.enroll(sid, "course_pd000003")
        # only one of the two concepts mastered → in_progress, NOT mastered
        storage.progress.update_progress("con_std_a", "course_pd000003",
                                         student_id=sid, status="completed")

        data = client.get(f'/parent/api/children/{sid}/standards').get_json()
        stds = data['subjects']['math']['strands']['Ratios']
        assert stds[0]['code'] == '6.RP' and stds[0]['status'] == 'in_progress'
        assert data['subjects']['math']['coverage_pct'] == 0

        # master the second concept → rolls up to mastered
        storage.progress.update_progress("con_std_b", "course_pd000003",
                                         student_id=sid, status="completed")
        data = client.get(f'/parent/api/children/{sid}/standards').get_json()
        assert data['subjects']['math']['strands']['Ratios'][0]['status'] == 'mastered'
        assert data['subjects']['math']['coverage_pct'] == 100

    def test_cross_family_403(self):
        client_a, _ = _parent_client("pd3@example.com")
        sid_a = client_a.post('/api/students', json={'display_name': 'Kid'}).get_json()['student_id']
        client_b, _ = _parent_client("pd4@example.com")
        assert client_b.get(f'/parent/api/children/{sid_a}/standards').status_code == 403
        assert client_b.get('/parent/api/children/stu_nothere0/standards').status_code == 404


class TestStruggles(unittest.TestCase):
    def test_struggle_thresholds(self):
        client, _ = _parent_client("pd5@example.com")
        sid = client.post('/api/students', json={'display_name': 'S'}).get_json()['student_id']
        uid = _seed_course("course_pd000004")
        storage.enrollments.enroll(sid, uid)
        # 3 low grades out of 4 attempts on one concept → flagged
        for g in (1, 2, 2, 3):
            storage.activity.log_activity(uid, "socratic_answer", concept_uid="con_pd_c1",
                                          grade=g, student_id=sid)
        # 2 low of 5 on another → not flagged (below both thresholds)
        for g in (1, 2, 3, 3, 4):
            storage.activity.log_activity(uid, "socratic_answer", concept_uid="con_pd_c2",
                                          grade=g, student_id=sid)
        data = client.get(f'/parent/api/children/{sid}/struggles').get_json()
        flagged = [s['concept_uid'] for s in data['struggles']]
        assert flagged == ['con_pd_c1']


class TestApprovalWorkflow(unittest.TestCase):
    def test_approve_and_deny(self):
        client, pid = _parent_client("pd6@example.com")
        sid = client.post('/api/students', json={'display_name': 'E'}).get_json()['student_id']
        uid = _seed_course("course_pd000005")
        storage.enrollments.enroll(sid, uid, course_kind="elective",
                                   status="pending_approval")
        queue = client.get('/parent/api/approvals').get_json()['approvals']
        assert len(queue) == 1
        eid = queue[0]['enrollment_id']

        r = client.post(f'/parent/api/enrollments/{eid}/approve')
        assert r.status_code == 200
        enr = storage.enrollments.get(sid, uid)
        assert enr['status'] == 'active' and enr['approved_by'] == pid
        # student got an in-app notification
        assert storage.notifications.unread_count(sid) == 1
        # re-approve is a 409
        assert client.post(f'/parent/api/enrollments/{eid}/approve').status_code == 409

    def test_foreign_family_approval_404(self):
        client_a, _ = _parent_client("pd7@example.com")
        sid = client_a.post('/api/students', json={'display_name': 'K'}).get_json()['student_id']
        uid = _seed_course("course_pd000006")
        storage.enrollments.enroll(sid, uid, course_kind="elective",
                                   status="pending_approval")
        eid = client_a.get('/parent/api/approvals').get_json()['approvals'][0]['enrollment_id']
        client_b, _ = _parent_client("pd8@example.com")
        assert client_b.post(f'/parent/api/enrollments/{eid}/approve').status_code == 404


class TestSeatEnforcement(unittest.TestCase):
    def test_add_student_blocked_over_seats(self):
        client, pid = _parent_client("pd9@example.com")
        storage.subscriptions.upsert(pid, seats=2, status='active')
        assert client.post('/api/students', json={'display_name': 'A'}).status_code == 201
        assert client.post('/api/students', json={'display_name': 'B'}).status_code == 201
        r = client.post('/api/students', json={'display_name': 'C'})
        assert r.status_code == 402
        # archive one → seat frees
        sid = storage.accounts.list_students(pid)[0]['id']
        client.patch(f'/parent/api/students/{sid}', json={'status': 'archived'})
        assert client.post('/api/students', json={'display_name': 'C'}).status_code == 201


class TestExportDelete(unittest.TestCase):
    def test_export_contains_all_record_classes(self):
        client, _ = _parent_client("pd10@example.com")
        sid = client.post('/api/students', json={'display_name': 'X'}).get_json()['student_id']
        uid = _seed_course("course_pd000007")
        storage.enrollments.enroll(sid, uid)
        storage.progress.update_progress("con_pd_c1", uid, student_id=sid, status="completed")
        storage.activity.log_activity(uid, "session", student_id=sid)
        data = client.post(f'/parent/api/children/{sid}/export').get_json()
        assert data['student']['id'] == sid
        assert len(data['enrollments']) == 1
        assert len(data['progress']) == 1
        assert len(data['activity']) == 1
        # FERPA audit row written
        assert any(a['action'] == 'export_data' for a in storage.audit.list(subject_student_id=sid))

    def test_delete_requires_confirmation_and_purges(self):
        client, _ = _parent_client("pd11@example.com")
        sid = client.post('/api/students', json={'display_name': 'Y'}).get_json()['student_id']
        uid = _seed_course("course_pd000008")
        storage.enrollments.enroll(sid, uid)
        storage.progress.update_progress("con_pd_c1", uid, student_id=sid, status="completed")

        assert client.post(f'/parent/api/children/{sid}/delete', json={}).status_code == 400
        r = client.post(f'/parent/api/children/{sid}/delete', json={'confirm': sid})
        assert r.status_code == 200
        assert storage.progress.get_course_progress(uid, student_id=sid) == []
        assert storage.enrollments.list_for_student(sid) == []
        assert storage.accounts.get_student(sid)['status'] == 'deleted'
        assert any(a['action'] == 'delete_data' for a in storage.audit.list(subject_student_id=sid))


if __name__ == '__main__':
    unittest.main()
