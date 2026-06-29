"""Tests for the STT service route logic (Task #5).

The backend is faked so the test runs with no model/deps installed — exactly the
engine-agnostic contract the Learn voice loop depends on.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/stt'))
import stt_server  # noqa: E402


class _FakeBackend:
    name = "fake"

    def transcribe(self, audio_path):
        assert os.path.exists(audio_path)  # route must persist audio to a file
        return "hello world"


def setup_function(_):
    stt_server._backend = _FakeBackend()


def test_no_audio_returns_400():
    client = stt_server.app.test_client()
    resp = client.post('/api/stt', data=b'')
    assert resp.status_code == 400


def test_transcribe_returns_text_and_backend():
    client = stt_server.app.test_client()
    resp = client.post('/api/stt', data=b'FAKEAUDIOBYTES', content_type='audio/webm')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['text'] == 'hello world'
    assert body['backend'] == 'fake'


def test_oversized_audio_returns_413(monkeypatch):
    monkeypatch.setattr(stt_server, 'MAX_AUDIO_BYTES', 4)
    client = stt_server.app.test_client()
    resp = client.post('/api/stt', data=b'waytoolong', content_type='audio/webm')
    assert resp.status_code == 413


def test_temp_file_is_cleaned_up():
    import glob
    import tempfile
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), 'tmp*')))
    client = stt_server.app.test_client()
    client.post('/api/stt', data=b'FAKE', content_type='audio/webm')
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), 'tmp*')))
    # No new lingering temp files from our request.
    assert not (after - before)


def test_health_reports_backend():
    client = stt_server.app.test_client()
    body = client.get('/health').get_json()
    assert body['status'] == 'healthy'
    assert 'backend' in body and 'model' in body
