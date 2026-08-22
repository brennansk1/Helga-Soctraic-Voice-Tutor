"""A timeout while the model is loading must not open the breaker.

THE MOMENT THIS PROTECTS
------------------------
`warm_up` is backgrounded from SET_CONTEXT and pays a model load of roughly two
minutes warm, and about nine after the Mac has slept. A learner who asks their
first question before that finishes lands in `chat()`: Ollama queues the
request behind the load, the 60s timeout fires, and three of those open the
circuit breaker.

The result is that the learner's FIRST question of the session returns nothing,
which is the worst possible moment for it — and the host was healthy the whole
time.

Measured 2026-08-22 on this machine: a cold load of nail-35b-a3b-ctx took
1m58s against a 60s per-call timeout, tripping the breaker on every attempt.

WHY IT STAYS CONSERVATIVE
-------------------------
Retrying a genuinely dead host for fifteen minutes is far worse than the
failure being prevented, so anything ambiguous — /api/ps unreachable, a
non-200, an unparseable body — falls back to the normal breaker path.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "services/core"))

from services.core.llm_client import LLMClient  # noqa: E402


def _client():
    return LLMClient(base_url="http://localhost:11434", model="nail-35b-a3b-ctx")


def _ps(models, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"models": [{"name": n} for n in models]}
    return r


def test_model_absent_means_loading():
    with patch("requests.get", return_value=_ps([])):
        assert _client()._model_is_loading() is True


def test_model_resident_means_not_loading():
    with patch("requests.get", return_value=_ps(["nail-35b-a3b-ctx:latest"])):
        assert _client()._model_is_loading() is False


def test_a_different_model_resident_still_means_ours_is_loading():
    with patch("requests.get", return_value=_ps(["qwen3.5:9b"])):
        assert _client()._model_is_loading() is True


@pytest.mark.parametrize("side", [
    requests.exceptions.ConnectionError("refused"),
    requests.exceptions.Timeout("slow"),
])
def test_an_unreachable_host_is_not_reported_as_loading(side):
    """A dead host must take the breaker path, not the patient one."""
    with patch("requests.get", side_effect=side):
        assert _client()._model_is_loading() is False


def test_a_non_200_is_not_reported_as_loading():
    with patch("requests.get", return_value=_ps([], status=503)):
        assert _client()._model_is_loading() is False


def test_a_malformed_body_is_not_reported_as_loading():
    bad = MagicMock()
    bad.status_code = 200
    bad.json.side_effect = ValueError("not json")
    with patch("requests.get", return_value=bad):
        assert _client()._model_is_loading() is False


def test_a_loading_timeout_does_not_record_a_breaker_failure():
    """The behaviour that matters: the learner's first question survives."""
    client = _client()
    breaker = MagicMock()
    breaker.allow.return_value = True

    with patch("requests.post", side_effect=requests.exceptions.Timeout()), \
         patch.object(client, "_model_is_loading", return_value=True), \
         patch("services.core.llm_client.get_breaker", return_value=breaker), \
         patch("time.sleep"):
        client.chat("sys", "hello", retries=2)

    assert breaker.record_failure.call_count == 0, (
        "a model load in progress was counted as host failure, which is what "
        "opens the breaker on a learner's first question")


def test_a_real_timeout_still_records_a_breaker_failure():
    """The guard must not disarm the breaker for genuine failures."""
    client = _client()
    breaker = MagicMock()
    breaker.allow.return_value = True

    with patch("requests.post", side_effect=requests.exceptions.Timeout()), \
         patch.object(client, "_model_is_loading", return_value=False), \
         patch("services.core.llm_client.get_breaker", return_value=breaker), \
         patch("time.sleep"):
        client.chat("sys", "hello", retries=2)

    assert breaker.record_failure.call_count >= 1
