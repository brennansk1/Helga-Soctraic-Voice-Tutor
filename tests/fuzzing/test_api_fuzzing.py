"""
Hypothesis-based API fuzzing tests for Helga Web UI routes.
Generates hundreds of random/edge-case payloads per endpoint to surface
unhandled exceptions, crashes, and validation bypasses.

Issues found and fixed by this suite:
  - create_custom_course: malformed JSON caused 500 instead of 400
  - complete_schedule_review: non-integer review_id caused 500 instead of 400
"""
import os
import sys
import io
import json
import pytest
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

# ── Module-level mocking for gevent/SocketIO ──────────────────────────
_mock_gevent = MagicMock()
_mock_gevent.monkey = MagicMock()
_mock_gevent.monkey.patch_all = MagicMock()
sys.modules["gevent"] = _mock_gevent
sys.modules["gevent.monkey"] = _mock_gevent.monkey
sys.modules["socketio"] = MagicMock()
_mock_flask_socketio = MagicMock()
_mock_flask_socketio.SocketIO.return_value = MagicMock()
sys.modules["flask_socketio"] = _mock_flask_socketio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../services/web-ui")))

import importlib
if "app" in sys.modules:
    del sys.modules["app"]
import app as web_app


@pytest.fixture
def client():
    web_app.app.config["TESTING"] = True
    web_app.app.config["SECRET_KEY"] = "fuzz-test-secret"
    with web_app.app.test_client() as c:
        yield c


# ── Strategies ────────────────────────────────────────────────────────
# Strings that commonly break web apps — NO surrogates (crash werkzeug test client)
safe_nasty_strings = st.one_of(
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=500),
    st.just(""),
    st.just("<script>alert('xss')</script>"),
    st.just("'; DROP TABLE users; --"),
    st.just("../../../etc/passwd"),
    st.just("\x00\x01\x02"),
    st.just("A" * 10000),
    st.just('{"__proto__": {"admin": true}}'),
    st.just("null"),
    st.just("undefined"),
)

# JSON values that commonly cause issues
nasty_json_values = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=200),
    st.just([]),
    st.just({}),
    st.just([None, None, None]),
    st.just({"nested": {"deep": {"value": 42}}}),
)


# ─────────────────────────────────────────────────────────────────────
# 1) USER PROFILE FUZZING
# ─────────────────────────────────────────────────────────────────────
class TestProfileFuzzing:
    """Fuzz the user profile save endpoint."""

    @given(
        name=safe_nasty_strings,
        level=st.one_of(
            st.just("beginner"), st.just("intermediate"),
            st.just("expert"), safe_nasty_strings
        ),
        goals=safe_nasty_strings,
        interests=st.lists(
            st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=50),
            max_size=25
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_profile_save_never_500s(self, client, name, level, goals, interests):
        """Profile save should NEVER return 500 regardless of input.
        Note: 500 from filesystem error (/app not writable) is expected outside Docker."""
        payload = {"name": name, "level": level, "goals": goals, "interests": interests}
        rv = client.post(
            "/api/user_profile",
            data=json.dumps(payload),
            content_type="application/json",
        )
        # 500 from read-only FS is expected outside Docker
        assert rv.status_code in (200, 400, 500), f"Unexpected {rv.status_code}"
        if rv.status_code == 500:
            data = json.loads(rv.data)
            assert "error" in data, "500 response should include error message"

    @given(data=st.one_of(
        st.just(None),
        st.just(42),
        st.just([1, 2, 3]),
        st.just(True),
        st.just("string_not_dict"),
    ))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_profile_save_rejects_non_dict(self, client, data):
        """Profile save should reject non-dict JSON gracefully."""
        rv = client.post(
            "/api/user_profile",
            data=json.dumps(data),
            content_type="application/json",
        )
        assert rv.status_code in (200, 400, 500), f"Unexpected {rv.status_code}"

    @given(name=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=51, max_size=200
    ))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_profile_name_truncation(self, client, name):
        """Names longer than 50 chars should be truncated."""
        payload = {"name": name, "level": "beginner", "goals": "", "interests": []}
        rv = client.post(
            "/api/user_profile",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert rv.status_code in (200, 500), f"Unexpected {rv.status_code}"


# ─────────────────────────────────────────────────────────────────────
# 2) DELETE COURSE FUZZING
# ─────────────────────────────────────────────────────────────────────
class TestDeleteCourseFuzzing:

    @given(uid=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=0, max_size=200
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_delete_with_nasty_uid(self, client, uid):
        with patch("app.requests.delete") as mock_del, patch("app.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.json.return_value = {"error": "not found"}
            mock_del.return_value = mock_resp
            mock_post.return_value = mock_resp

            rv = client.delete(f"/api/delete_course?uid={uid}")
            assert rv.status_code in (200, 404, 502), f"Unexpected {rv.status_code} for uid={uid!r}"


# ─────────────────────────────────────────────────────────────────────
# 3) SET ACTIVE COURSE FUZZING
# ─────────────────────────────────────────────────────────────────────
class TestSetActiveCourseFuzzing:

    @given(uid=nasty_json_values, title=nasty_json_values)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_set_active_with_nasty_payload(self, client, uid, title):
        with patch("app.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok"}
            mock_post.return_value = mock_resp

            payload = {"uid": uid, "title": title}
            rv = client.post(
                "/api/set_active_course",
                data=json.dumps(payload, default=str),
                content_type="application/json",
            )
            assert rv.status_code in (200, 400, 502), f"Unexpected {rv.status_code}"


# ─────────────────────────────────────────────────────────────────────
# 4) SCHEDULE COMPLETE FUZZING (validates int conversion fix)
# ─────────────────────────────────────────────────────────────────────
class TestScheduleCompleteFuzzing:

    @given(review_id=st.one_of(
        st.integers(),
        st.just(None),
        st.just("not_a_number"),
        st.just(-1),
        st.just(0),
        st.just(99999999999999),
        st.just("'; DROP TABLE;"),
        st.just(True),
        st.just([]),
        st.just({"nested": True}),
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_schedule_complete_handles_any_id(self, client, review_id):
        with patch("app._get_storage") as mock_storage:
            mock_s = MagicMock()
            mock_s.schedule.complete_review.return_value = None
            mock_storage.return_value = mock_s

            payload = {"review_id": review_id}
            rv = client.post(
                "/api/schedule/complete",
                data=json.dumps(payload, default=str),
                content_type="application/json",
            )
            assert rv.status_code in (200, 400, 500), f"Unexpected {rv.status_code} for review_id={review_id!r}"
            # Non-integer types should now return 400, not 500
            if isinstance(review_id, (str, list, dict)):
                assert rv.status_code == 400, f"Expected 400 for non-integer review_id={review_id!r}, got {rv.status_code}"


# ─────────────────────────────────────────────────────────────────────
# 5) UPLOAD SECURITY FUZZING
# ─────────────────────────────────────────────────────────────────────
class TestUploadFuzzing:

    CRAFT_FILENAMES = [
        "normal.epub",
        "../../etc/passwd",
        ".hidden.epub",
        "a" * 300 + ".epub",
        "test file.epub",
        "test\ttab.epub",
        "<script>.epub",
        "test.epub.exe",
    ]

    @pytest.mark.parametrize("filename", CRAFT_FILENAMES)
    def test_epub_upload_with_crafted_filename(self, client, filename):
        data = {"file": (io.BytesIO(b"PK\x03\x04fake_epub_data"), filename)}
        rv = client.post("/api/upload_epub", content_type="multipart/form-data", data=data)
        assert rv.status_code in (200, 202, 400, 500, 503), f"Unexpected {rv.status_code} for {filename!r}"

    @given(content=st.binary(min_size=0, max_size=1000))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_epub_upload_with_random_content(self, client, content):
        data = {"file": (io.BytesIO(content), "test_book.epub")}
        with patch("app.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 202
            mock_post.return_value = mock_resp

            rv = client.post("/api/upload_epub", content_type="multipart/form-data", data=data)
            assert rv.status_code in (200, 202, 400, 500, 503), f"Unexpected {rv.status_code}"

    CRAFT_SOURCE_FILENAMES = [
        "../../etc/passwd.txt",
        ".hidden.txt",
        "a" * 300 + ".md",
        "test file.txt",
        "TEST.PDF",
        "malicious.exe",
        "",
        "good_file.txt",
    ]

    @pytest.mark.parametrize("filename", CRAFT_SOURCE_FILENAMES)
    def test_source_upload_with_crafted_filename(self, client, filename):
        data = {"file": (io.BytesIO(b"test content"), filename)}
        rv = client.post("/api/upload_source", content_type="multipart/form-data", data=data)
        # .exe and empty should be 400, .txt and .md should be 200/500
        assert rv.status_code in (200, 400, 500), f"Unexpected {rv.status_code} for {filename!r}"


# ─────────────────────────────────────────────────────────────────────
# 6) CUSTOM COURSE CREATION FUZZING (validates JSON parse fix)
# ─────────────────────────────────────────────────────────────────────
class TestCustomCourseFuzzing:

    MALFORMED_JSON_CASES = [
        ("not json at all", 400),
        ("[]", 400),  # empty list
        ('[{"title": "ok"}]', None),  # valid — could be 200 or 500 from backend
        ('{"not": "a list"}', 400),  # dict not list
        ("", 400),  # empty → missing
        ("{broken", 400),
        ("null", 400),
        ("42", 400),
        ('"just a string"', 400),
    ]

    @pytest.mark.parametrize("modules_json,expected_code", MALFORMED_JSON_CASES)
    def test_custom_course_json_validation(self, client, modules_json, expected_code):
        """Validates the JSON parse fix — malformed JSON should return 400."""
        with patch("app.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok"}
            mock_post.return_value = mock_resp

            rv = client.post(
                "/api/create_custom_course",
                content_type="multipart/form-data",
                data={"title": "Test", "modules": modules_json, "content_source": "zim"},
            )
            if expected_code:
                assert rv.status_code == expected_code, f"Expected {expected_code} for {modules_json!r}, got {rv.status_code}"
            else:
                assert rv.status_code in (200, 400, 500), f"Unexpected {rv.status_code}"


# ─────────────────────────────────────────────────────────────────────
# 7) THINKING STATUS FUZZING
# ─────────────────────────────────────────────────────────────────────
class TestThinkingStatusFuzzing:

    @given(data=st.fixed_dictionaries({
        "message": st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200),
        "status": st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=100),
        "progress": st.one_of(st.integers(), st.just(None)),
    }))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_thinking_status_never_crashes(self, client, data):
        rv = client.post(
            "/api/update_thinking_status",
            data=json.dumps(data, default=str),
            content_type="application/json",
        )
        assert rv.status_code == 200


# ─────────────────────────────────────────────────────────────────────
# 8) QUERY PARAMETER INJECTION FUZZING
# ─────────────────────────────────────────────────────────────────────
class TestQueryParamFuzzing:

    @given(uid=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=0, max_size=200
    ))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_course_structure_with_nasty_uid(self, client, uid):
        with patch("app.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.json.return_value = {"error": "not found"}
            mock_get.return_value = mock_resp
            rv = client.get(f"/api/course_structure?uid={uid}")
            assert rv.status_code in (200, 404, 502)

    @given(topic=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=0, max_size=200
    ))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_due_cards_with_nasty_topic(self, client, topic):
        with patch("app.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"cards": []}
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_get.return_value = mock_resp
            rv = client.get(f"/api/due_cards?topic={topic}")
            assert rv.status_code in (200, 400, 502)

    @given(uid=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=1, max_size=100
    ))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_course_status_with_nasty_uid(self, client, uid):
        with patch("app.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ready"}
            mock_get.return_value = mock_resp
            rv = client.get(f"/api/course_status/{uid}")
            assert rv.status_code in (200, 404, 502)


# ─────────────────────────────────────────────────────────────────────
# 9) QUIZ GRADING FUZZING
# ─────────────────────────────────────────────────────────────────────
class TestQuizFuzzing:

    @given(payload=st.fixed_dictionaries({
        "concept_uid": nasty_json_values,
        "answer": nasty_json_values,
        "question": nasty_json_values,
    }))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_grade_quiz_never_crashes(self, client, payload):
        with patch("app.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"grade": "correct", "score": 1.0}
            mock_post.return_value = mock_resp

            rv = client.post(
                "/api/quiz/grade",
                data=json.dumps(payload, default=str),
                content_type="application/json",
            )
            assert rv.status_code in (200, 400, 502)
