"""A cold model load must not be mistaken for a dead LLM.

MEASURED on this machine: `nail-35b-a3b-ctx` takes 3m31s to load from disk.
`llm_generate`'s timeout floor is 90s, so the first call after Ollama has
evicted the weights times out, retries, times out, retries, times out -- and
the circuit breaker opens on three "transport failures" while the model is
quietly finishing its read. The golden matrix hit exactly this: three
`LLM Timeout after 90s` lines and `breaker OPEN after 3 consecutive transport
failures` before a single course had been attempted.

The two situations are indistinguishable from the client -- no bytes either way
-- so the client has to ask: is the model resident? If it is not, nothing is
wrong. If Ollama cannot be reached at all, we must NOT assume "still loading",
or a genuinely dead host retries on the long timeout forever; that is the third
case below.
"""
import requests

from services.common import llm_utils


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_absent_weights_read_as_still_loading(monkeypatch):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _Resp({"models": []}))
    assert llm_utils._weights_resident(
        "nail-35b-a3b-ctx", "http://x:11434") is False


def test_present_weights_read_as_loaded(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(
        {"models": [{"name": "nail-35b-a3b-ctx:latest"}]}))
    assert llm_utils._weights_resident(
        "nail-35b-a3b-ctx", "http://x:11434") is True


def test_a_different_model_resident_is_not_ours(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(
        {"models": [{"name": "qwen3.5:9b"}]}))
    assert llm_utils._weights_resident(
        "nail-35b-a3b-ctx", "http://x:11434") is False


def test_unreachable_ollama_is_unknown_not_loading(monkeypatch):
    """None, not False -- the difference between 'wait' and 'give up'."""
    def _boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", _boom)
    assert llm_utils._weights_resident(
        "nail-35b-a3b-ctx", "http://x:11434") is None


def test_a_non_200_is_unknown(monkeypatch):
    class _Bad:
        status_code = 503

        def json(self):
            return {}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Bad())
    assert llm_utils._weights_resident(
        "nail-35b-a3b-ctx", "http://x:11434") is None


def test_the_cold_load_budget_exceeds_the_measured_load():
    """211s measured; the budget must clear it with room for a slower disk."""
    assert llm_utils.COLD_LOAD_TIMEOUT > 211
