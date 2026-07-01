"""B15.4/B15.5/B15.8 tests: auth flows, role gating, PIN lockout, forged
student_id trust, and Socket.IO room scoping (two-session leakage).

Runs the real web-ui Flask app with a temp StorageManager; core-logic HTTP
calls are mocked at the `requests` boundary.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, '../../../'))
_webui = os.path.join(_root, 'services/web-ui')
for p in (_root, _webui):
    if p not in sys.path:
        sys.path.insert(0, p)

# fresh data dir for this module's storage
_data_dir = tempfile.mkdtemp(prefix="helga_auth_")
os.environ['DATA_ROOT'] = _data_dir

# tests/fuzzing/test_api_fuzzing.py replaces gevent/socketio/flask_socketio in
# sys.modules with MagicMocks at import time and imports `app` under them.
# When that module loads first, the cached `app` has a mocked SocketIO and
# room emits go nowhere. Purge the mocks and re-import a REAL app.
_purged = False
for _mod in ("gevent", "gevent.monkey", "socketio", "flask_socketio"):
    if isinstance(sys.modules.get(_mod), MagicMock):
        del sys.modules[_mod]
        _purged = True
if _purged:
    for _mod in ("app", "auth"):
        sys.modules.pop(_mod, None)

import app as webui_app  # noqa: E402
from services.common.storage import DEFAULT_STUDENT_ID  # noqa: E402
import auth as helga_auth  # noqa: E402

app = webui_app.app
socketio = webui_app.socketio
app.config['TESTING'] = True  # skips CSRF (existing csrf_protect behavior)


def _fresh_parent(client, email, password="hunter2secure"):
    r = client.post('/signup', json={'email': email, 'password': password,
                                     'display_name': 'P'})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()['parent_id']


def _add_student(client, name="Kid", band="3-5", pin=None):
    r = client.post('/api/students', json={'display_name': name,
                                           'grade_band': band, 'pin': pin})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()['student_id']


class TestParentAuth(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_signup_login_logout_roundtrip(self):
        _fresh_parent(self.client, "roundtrip@example.com")
        # signed in — picker reachable
        assert self.client.get('/students').status_code == 200
        self.client.post('/logout')
        assert self.client.get('/students').status_code in (302, 401)
        r = self.client.post('/login', json={'email': 'roundtrip@example.com',
                                             'password': 'hunter2secure'})
        assert r.status_code == 200

    def test_wrong_password_generic_error(self):
        _fresh_parent(self.client, "wrongpw@example.com")
        self.client.post('/logout')
        r = self.client.post('/login', json={'email': 'wrongpw@example.com',
                                             'password': 'not-it-at-all'})
        assert r.status_code == 401
        assert r.get_json()['error'] == 'invalid credentials'

    def test_duplicate_signup_no_enumeration(self):
        _fresh_parent(self.client, "dupe@example.com")
        self.client.post('/logout')
        r = self.client.post('/signup', json={'email': 'dupe@example.com',
                                              'password': 'anotherpass123'})
        assert r.status_code == 400
        assert 'dupe' not in r.get_data(as_text=True)

    def test_short_password_rejected(self):
        r = self.client.post('/signup', json={'email': 'short@example.com',
                                              'password': 'short'})
        assert r.status_code == 400


class TestStudentLaunchAndOwnership(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_launch_flips_role_and_exit_restores(self):
        _fresh_parent(self.client, "launch@example.com")
        sid = _add_student(self.client)
        r = self.client.post(f'/students/{sid}/launch')
        assert r.status_code == 200
        info = self.client.get('/api/auth/session').get_json()
        assert info['role'] == 'student' and info['student_id'] == sid
        assert info['parent_id']  # retained for exit
        self.client.post('/students/exit')
        info = self.client.get('/api/auth/session').get_json()
        assert info['role'] == 'parent' and not info['student_id']

    def test_cross_family_launch_forbidden(self):
        _fresh_parent(self.client, "famA@example.com")
        sid_a = _add_student(self.client, "KidA")
        self.client.post('/logout')
        _fresh_parent(self.client, "famB@example.com")
        r = self.client.post(f'/students/{sid_a}/launch')
        assert r.status_code == 403  # known-but-foreign → 403

    def test_unknown_student_404(self):
        _fresh_parent(self.client, "unknown@example.com")
        r = self.client.post('/students/stu_deadbeef/launch')
        assert r.status_code == 404


class TestPinLogin(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        helga_auth._pin_state.clear()

    def test_pin_login_and_cross_family_rejection(self):
        pid_a = _fresh_parent(self.client, "pinA@example.com")
        sid = _add_student(self.client, "PinKid", pin="1234")
        self.client.post('/logout')

        # right PIN, wrong family scope → 404 (never selects sibling family)
        r = self.client.post(f'/students/{sid}/pin',
                             json={'pin': '1234', 'parent_id': 'par_other'})
        assert r.status_code == 404

        # right PIN, right family → student session
        r = self.client.post(f'/students/{sid}/pin',
                             json={'pin': '1234', 'parent_id': pid_a})
        assert r.status_code == 200
        info = self.client.get('/api/auth/session').get_json()
        assert info['role'] == 'student' and info['student_id'] == sid

    def test_pin_lockout_after_five_failures(self):
        pid = _fresh_parent(self.client, "pinlock@example.com")
        sid = _add_student(self.client, "LockKid", pin="9999")
        self.client.post('/logout')
        for _ in range(5):
            r = self.client.post(f'/students/{sid}/pin',
                                 json={'pin': '0000', 'parent_id': pid})
        assert r.status_code == 401
        # 6th attempt — even the CORRECT pin is now locked out
        r = self.client.post(f'/students/{sid}/pin',
                             json={'pin': '9999', 'parent_id': pid})
        assert r.status_code == 423

    def test_pinless_student_never_pin_logs_in(self):
        pid = _fresh_parent(self.client, "nopin@example.com")
        sid = _add_student(self.client, "NoPinKid", pin=None)
        self.client.post('/logout')
        r = self.client.post(f'/students/{sid}/pin',
                             json={'pin': '1234', 'parent_id': pid})
        assert r.status_code == 404


class TestStudentIdInjectionTrust(unittest.TestCase):
    """Forged student_id in the /api/event body must be ignored — the
    authenticated session value wins (spec 03 §9 row 9)."""

    def setUp(self):
        self.client = app.test_client()

    def test_forged_student_id_overwritten(self):
        _fresh_parent(self.client, "forge@example.com")
        sid = _add_student(self.client, "ForgeKid")
        self.client.post(f'/students/{sid}/launch')

        captured = {}

        def fake_post(url, json=None, timeout=None, **kw):
            captured['body'] = json
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {'status': 'ok'}
            return resp

        def fake_get(url, params=None, timeout=None, **kw):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {'active_course_uid': None}
            resp.raise_for_status.return_value = None
            return resp

        with patch.object(webui_app.requests, 'post', side_effect=fake_post), \
             patch.object(webui_app.requests, 'get', side_effect=fake_get):
            r = self.client.post('/api/event',
                                 json={'type': 'TEXT_INPUT',
                                       'payload': {'text': 'hi'},
                                       'student_id': 'stu_victim99'})
        assert r.status_code == 200
        assert captured['body']['student_id'] == sid  # session wins

    def test_anonymous_legacy_event_still_works(self):
        """R0 posture: no login → the legacy student drives the event."""
        with patch.object(webui_app.requests, 'post') as mock_post, \
             patch.object(webui_app.requests, 'get') as mock_get:
            mock_post.return_value = MagicMock(status_code=200,
                                               json=lambda: {'status': 'ok'})
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {'active_course_uid': None},
                raise_for_status=lambda: None)
            r = self.client.post('/api/event', json={'type': 'TEXT_INPUT',
                                                     'payload': {'text': 'x'}})
        assert r.status_code == 200
        assert mock_post.call_args.kwargs['json']['student_id'] == DEFAULT_STUDENT_ID


class TestSocketRoomScoping(unittest.TestCase):
    """Two-session leakage (spec 03 §9 row 3): a status emit stamped for A
    reaches A's socket and never B's."""

    def _connect(self, flask_client):
        return socketio.test_client(app, flask_test_client=flask_client)

    def test_status_update_scoped_to_owner_room(self):
        client_a = app.test_client()
        _fresh_parent(client_a, "sockA@example.com")
        sid_a = _add_student(client_a, "SockKidA")
        client_a.post(f'/students/{sid_a}/launch')

        client_b = app.test_client()
        _fresh_parent(client_b, "sockB@example.com")
        sid_b = _add_student(client_b, "SockKidB")
        client_b.post(f'/students/{sid_b}/launch')

        with patch.object(webui_app.requests, 'get') as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {'active_course_uid': None},
                raise_for_status=lambda: None)
            sock_a = self._connect(client_a)
            sock_b = self._connect(client_b)
            assert sock_a.is_connected() and sock_b.is_connected()
            sock_a.get_received()   # drain connect-time snapshots
            sock_b.get_received()

            # core posts a status stamped for A
            r = app.test_client().post('/api/update_thinking_status',
                                       json={'message': 'Grading...',
                                             'student_id': sid_a})
            assert r.status_code == 200

            got_a = [m for m in sock_a.get_received() if m['name'] == 'status_update']
            got_b = [m for m in sock_b.get_received() if m['name'] == 'status_update']
            assert len(got_a) >= 1, "owner did not receive their status"
            assert got_b == [], "cross-student leakage!"
            sock_a.disconnect()
            sock_b.disconnect()

    def test_unowned_status_dropped_not_broadcast(self):
        client_a = app.test_client()
        _fresh_parent(client_a, "sockC@example.com")
        sid_a = _add_student(client_a, "SockKidC")
        client_a.post(f'/students/{sid_a}/launch')

        with patch.object(webui_app.requests, 'get') as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {'active_course_uid': None},
                raise_for_status=lambda: None)
            sock_a = self._connect(client_a)
            sock_a.get_received()
            r = app.test_client().post('/api/update_thinking_status',
                                       json={'message': 'orphan payload'})
            assert r.status_code == 202  # dropped, fail closed
            assert [m for m in sock_a.get_received()
                    if m['name'] == 'status_update'] == []
            sock_a.disconnect()

    def test_stream_tokens_scoped(self):
        client_a = app.test_client()
        _fresh_parent(client_a, "sockD@example.com")
        sid_a = _add_student(client_a, "SockKidD")
        client_a.post(f'/students/{sid_a}/launch')

        client_b = app.test_client()
        _fresh_parent(client_b, "sockE@example.com")
        sid_b = _add_student(client_b, "SockKidE")
        client_b.post(f'/students/{sid_b}/launch')

        with patch.object(webui_app.requests, 'get') as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: {'active_course_uid': None},
                raise_for_status=lambda: None)
            sock_a = self._connect(client_a)
            sock_b = self._connect(client_b)
            sock_a.get_received()
            sock_b.get_received()
            app.test_client().post('/api/update_thinking_status',
                                   json={'type': 'stream_token', 'token': 'Hel',
                                         'done': False, 'student_id': sid_b})
            assert [m for m in sock_a.get_received() if m['name'] == 'stream_token'] == []
            got_b = [m for m in sock_b.get_received() if m['name'] == 'stream_token']
            assert len(got_b) == 1 and got_b[0]['args'][0]['token'] == 'Hel'
            sock_a.disconnect()
            sock_b.disconnect()


if __name__ == '__main__':
    unittest.main()
