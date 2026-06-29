"""Tests for the TTS service routes (B10). Kokoro is faked, so this runs with no
model installed; CACHE_DIR falls back to a temp dir off-container."""
import sys
import os
import tempfile

os.environ.setdefault("TTS_CACHE_DIR", os.path.join(tempfile.gettempdir(), "helga_tts_test"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/tts'))
import numpy as np
import tts_server  # noqa: E402


class _FakePipeline:
    def __call__(self, text, voice=None):
        yield (None, None, np.zeros(2400, dtype="float32"))


def setup_function(_):
    tts_server.pipeline = _FakePipeline()  # bypass the lazy Kokoro load


def test_health():
    body = tts_server.app.test_client().get('/health').get_json()
    assert body['status'] == 'healthy'
    assert body['engine'] == 'kokoro'


def test_voices_list():
    body = tts_server.app.test_client().get('/api/voices').get_json()
    assert 'af_heart' in body['voices']


def test_tts_missing_text_400():
    resp = tts_server.app.test_client().post('/api/tts', json={})
    assert resp.status_code == 400


def test_tts_synthesizes_wav():
    resp = tts_server.app.test_client().post('/api/tts', json={"text": "hello there"})
    assert resp.status_code == 200
    assert resp.mimetype == 'audio/wav'
    assert resp.data[:4] == b'RIFF'  # WAV header


def test_tts_unknown_voice_falls_back():
    resp = tts_server.app.test_client().post('/api/tts', json={"text": "hi", "voice": "not_a_voice"})
    assert resp.status_code == 200
