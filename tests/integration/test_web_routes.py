"""
Integration tests for Flask web-ui routes.
Tests the proxy routes and page renderers using Flask test client.
Does NOT require Docker services to be running — all external calls are mocked.

NOTE: The web-ui uses gevent + SocketIO which requires the Docker environment.
These tests mock the SocketIO layer and test Flask routing independently.
"""
import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

# Must mock gevent and socketio BEFORE importing app
# app.py line 1 does `from gevent import monkey; monkey.patch_all()`
_mock_gevent = MagicMock()
_mock_gevent.monkey = MagicMock()
_mock_gevent.monkey.patch_all = MagicMock()

sys.modules["gevent"] = _mock_gevent
sys.modules["gevent.monkey"] = _mock_gevent.monkey

# Mock socketio Client used in app.py
_mock_sio_client = MagicMock()
sys.modules["socketio"] = MagicMock()

# Mock flask_socketio to avoid gevent async_mode requirement
_mock_flask_socketio = MagicMock()
_mock_socketio_instance = MagicMock()
_mock_flask_socketio.SocketIO.return_value = _mock_socketio_instance
sys.modules["flask_socketio"] = _mock_flask_socketio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/web-ui")))

# Now import app AFTER mocking
import importlib
if "app" in sys.modules:
    # Force reimport with mocks
    del sys.modules["app"]
import app as web_app


@pytest.fixture
def client():
    """Create Flask test client."""
    web_app.app.config["TESTING"] = True
    web_app.app.config["SECRET_KEY"] = "test-secret-key"
    with web_app.app.test_client() as c:
        yield c


# ─── Page Rendering Tests ─────────────────────────────────────────────
class TestPageRendering:
    """Verify all pages render without errors."""

    PAGES = [
        ("/", 200, b"Helga"),
        ("/courses", 200, b"Courses"),
        ("/learn", 200, b"Learn"),
        ("/test", 200, b"Test"),
        ("/review", 200, b"Review"),
        ("/schedule", 200, b"Schedule"),
        # A3: Memory Palace is reachable again. It previously redirected to
        # home while the FSM kept the full MEMORY_PALACE state and librarian
        # kept /palace/start, /locus/next and /anchor — one of three advertised
        # learning modes, backed by working services, with no way in.
        ("/palace", 200, b"Memory Palace"),
        ("/account", 302, None),
    ]

    @pytest.mark.parametrize("path,expected_status,expected_text", PAGES)
    def test_page_renders(self, client, path, expected_status, expected_text):
        rv = client.get(path)
        assert rv.status_code == expected_status, f"{path} returned {rv.status_code}"
        if expected_text is not None:
            assert expected_text in rv.data, f"'{expected_text}' not found in {path}"

    def test_status_page_renders(self, client):
        """Status page does service health checks — mock them."""
        with patch("app.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "healthy"}
            mock_resp.elapsed = MagicMock()
            mock_resp.elapsed.total_seconds = MagicMock(return_value=0.05)
            mock_get.return_value = mock_resp
            rv = client.get("/status")
            assert rv.status_code == 200
            assert b"System Status" in rv.data


# ─── API Proxy Tests ──────────────────────────────────────────────────
class TestAPIProxies:
    """Verify API proxy routes return correct structure."""

    def test_api_courses_proxy(self, client):
        with patch("app.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "courses": [{"uid": "course_abc", "title": "Test", "status": "ready"}]
            }
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_get.return_value = mock_resp

            rv = client.get("/api/courses")
            assert rv.status_code == 200
            data = json.loads(rv.data)
            assert "courses" in data

    def test_api_fsm_state_proxy(self, client):
        with patch("app.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "state": "IDLE",
                "active_course_uid": None,
            }
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_get.return_value = mock_resp

            rv = client.get("/api/fsm_state")
            assert rv.status_code == 200
            data = json.loads(rv.data)
            assert "state" in data


# ─── Upload Security Tests ────────────────────────────────────────────
class TestUploadSecurity:
    """Verify upload validation at the route level."""

    def test_epub_rejects_no_file(self, client):
        rv = client.post("/api/upload_epub", content_type="multipart/form-data", data={})
        assert rv.status_code == 400

    def test_epub_rejects_wrong_extension(self, client):
        import io
        data = {"file": (io.BytesIO(b"fake"), "malicious.exe")}
        rv = client.post("/api/upload_epub", content_type="multipart/form-data", data=data)
        assert rv.status_code == 400


# ─── Security Config Test ─────────────────────────────────────────────
class TestSecurityConfig:
    def test_secret_key_configured(self, client):
        assert web_app.app.secret_key is not None
        assert len(web_app.app.secret_key) > 0

    def test_max_content_length_configured(self, client):
        assert web_app.app.config.get("MAX_CONTENT_LENGTH") is not None
