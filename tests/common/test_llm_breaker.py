"""A7 — circuit breaker + named LLM failure taxonomy.

No network and no live Ollama: the state machine is exercised directly and the
integration tests patch `requests.post`.

The load-bearing assertion in this file is not any single transition — it is
that "the model service is unreachable" and "the model returned unusable JSON"
are DIFFERENT, checkable facts. Both used to arrive as `None`, and a build that
hit a dead host wrote a course full of stubs and marked it ready.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.common import llm_utils
from services.common.llm_breaker import (
    CLOSED, OPEN, HALF_OPEN,
    CircuitBreaker, OllamaBreaker,
    LLMError, LLMUnavailable, LLMCircuitOpen, LLMTimeout, LLMTransportError,
    LLMOverloaded, LLMBadOutput, LLMBadJSON, LLMSchemaMismatch,
    LLMEmptyResponse, LLMRequestRejected,
    get_breaker, reset_breaker, last_llm_failure, clear_llm_failure,
)


@pytest.fixture(autouse=True)
def _fresh_breaker():
    """The breaker is process-global, so one test tripping it open would leak
    into every later test in the run. Reset around each."""
    reset_breaker()
    clear_llm_failure()
    yield
    reset_breaker()
    clear_llm_failure()


def _ok_response(content='[{"title": "A"}]'):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _http_error(status):
    """A response whose raise_for_status() raises like requests does."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = "boom"
    err = requests.exceptions.HTTPError(f"{status} Client Error")
    err.response = resp
    resp.raise_for_status.side_effect = err
    return resp


# ---------------------------------------------------------------------------
# The state machine


class TestBreakerTransitions(unittest.TestCase):
    def test_closed_until_threshold_then_open(self):
        b = CircuitBreaker(trip_after=3, probe_interval=60)
        for _ in range(2):
            b.record_failure()
            self.assertEqual(b.state, CLOSED)
            self.assertTrue(b.allow())
        b.record_failure()
        self.assertEqual(b.state, OPEN)
        self.assertFalse(b.allow())

    def test_success_resets_the_consecutive_count(self):
        b = CircuitBreaker(trip_after=3, probe_interval=60)
        b.record_failure()
        b.record_failure()
        b.record_success()
        b.record_failure()
        b.record_failure()
        self.assertEqual(b.state, CLOSED,
                         "failures must be CONSECUTIVE to trip the circuit")

    def test_half_open_admits_exactly_one_probe(self):
        b = CircuitBreaker(trip_after=1, probe_interval=0.05)
        b.record_failure()
        self.assertFalse(b.allow())
        time.sleep(0.06)
        self.assertTrue(b.allow(), "one probe after the interval")
        self.assertEqual(b.state, HALF_OPEN)
        self.assertFalse(b.allow(), "everything else still fast-fails")

    def test_probe_success_closes(self):
        b = CircuitBreaker(trip_after=1, probe_interval=0.05)
        b.record_failure()
        time.sleep(0.06)
        b.allow()
        b.record_success()
        self.assertEqual(b.state, CLOSED)
        self.assertTrue(b.allow())

    def test_probe_failure_reopens_and_backs_off(self):
        b = CircuitBreaker(trip_after=1, probe_interval=0.05, probe_max=10)
        b.record_failure()                      # open, streak 1 -> 0.05s
        time.sleep(0.06)
        self.assertTrue(b.allow())              # probe
        b.record_failure()                      # failed probe -> streak 2
        self.assertEqual(b.state, OPEN)
        self.assertFalse(b.allow())
        # A host down for an hour must not be probed 240 times: each failed
        # probe costs a full client timeout on whoever draws it.
        self.assertGreater(b.retry_after(), 0.05)

    def test_backoff_is_capped(self):
        b = CircuitBreaker(trip_after=1, probe_interval=1.0, probe_max=2.0)
        for _ in range(8):
            b.record_failure()
        self.assertLessEqual(b.retry_after(), 2.0)

    def test_reset_closes_immediately(self):
        b = CircuitBreaker(trip_after=1, probe_interval=600)
        b.record_failure()
        self.assertFalse(b.allow())
        b.reset()
        self.assertEqual(b.state, CLOSED)
        self.assertTrue(b.allow())

    def test_disabled_breaker_never_blocks(self):
        b = CircuitBreaker(trip_after=1, probe_interval=600, enabled=False)
        for _ in range(10):
            b.record_failure()
        self.assertTrue(b.allow())

    def test_stats_shape(self):
        b = CircuitBreaker(trip_after=2, probe_interval=30)
        b.record_failure()
        b.record_failure()
        stats = b.stats()
        # fsm_logic's /health surface reads these three keys.
        self.assertEqual(stats["state"], OPEN)
        self.assertEqual(stats["consecutive_failures"], 2)
        self.assertEqual(stats["state_changes"], 1)
        self.assertGreater(stats["retry_after_s"], 0)

    def test_legacy_name_still_works(self):
        self.assertIs(OllamaBreaker, CircuitBreaker)

    def test_raise_if_open_is_named(self):
        b = CircuitBreaker(trip_after=1, probe_interval=600)
        b.record_failure()
        with self.assertRaises(LLMCircuitOpen):
            b.raise_if_open()


class TestEnvConfiguration:
    def test_thresholds_come_from_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "7")
        monkeypatch.setenv("OLLAMA_BREAKER_PROBE_S", "42")
        reset_breaker()
        b = get_breaker()
        assert b.trip_after == 7
        assert b.probe_interval == 42.0

    def test_garbage_env_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "not-a-number")
        reset_breaker()
        assert get_breaker().trip_after == 3

    def test_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_ENABLED", "0")
        reset_breaker()
        b = get_breaker()
        for _ in range(5):
            b.record_failure()
        assert b.allow()


# ---------------------------------------------------------------------------
# The distinction the rest of the codebase depends on


class TestFailuresAreDistinguishable(unittest.TestCase):
    def test_unreachable_and_bad_json_are_different_types(self):
        unreachable = LLMCircuitOpen("host down")
        bad_json = LLMBadJSON("'[{oops'")

        self.assertIsInstance(unreachable, LLMUnavailable)
        self.assertNotIsInstance(unreachable, LLMBadOutput)

        self.assertIsInstance(bad_json, LLMBadOutput)
        self.assertNotIsInstance(bad_json, LLMUnavailable)

        # Both are still catchable as one family when a caller does not care.
        for e in (unreachable, bad_json):
            self.assertIsInstance(e, LLMError)

    def test_every_reason_code_is_distinct(self):
        codes = [LLMCircuitOpen.reason, LLMTimeout.reason,
                 LLMTransportError.reason, LLMOverloaded.reason,
                 LLMBadJSON.reason, LLMSchemaMismatch.reason,
                 LLMEmptyResponse.reason, LLMRequestRejected.reason]
        self.assertEqual(len(codes), len(set(codes)))

    def test_request_rejected_is_not_an_outage(self):
        # A 400 means the server is up and our payload is wrong. Filing it under
        # "unreachable" would send an operator to restart a healthy Ollama.
        self.assertNotIsInstance(LLMRequestRejected("HTTP 400"), LLMUnavailable)

    def test_overload_is_ours_not_the_hosts(self):
        # Same recovery as an outage (wait), so same family — but its own class,
        # because it must never trip the breaker.
        e = LLMOverloaded("queue full")
        self.assertIsInstance(e, LLMUnavailable)
        self.assertEqual(e.reason, "overloaded")

    def test_message_names_the_fact(self):
        self.assertIn("unreachable", LLMCircuitOpen("x").user_message.lower())
        self.assertIn("json", LLMBadJSON("x").user_message.lower())


# ---------------------------------------------------------------------------
# Integration with the build path (llm_utils), all transport mocked


class TestLlmGenerateBreaker:
    def test_trips_then_fails_fast_without_a_socket(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "3")
        monkeypatch.setenv("OLLAMA_BREAKER_PROBE_S", "600")
        reset_breaker()

        with patch.object(llm_utils.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")) as post:
            for _ in range(3):
                assert llm_utils.llm_generate("hi", retries=1) == ""
            assert post.call_count == 3
            assert get_breaker().state == OPEN

            # The point of the whole exercise: the next call costs nothing.
            assert llm_utils.llm_generate("hi", retries=1) == ""
            assert post.call_count == 3, "breaker OPEN must not issue a request"

        assert last_llm_failure().reason == "circuit_open"

    def test_strict_raises_the_named_failure(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "1")
        monkeypatch.setenv("OLLAMA_BREAKER_PROBE_S", "600")
        reset_breaker()

        with patch.object(llm_utils.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            with pytest.raises(LLMTransportError):
                llm_utils.llm_generate("hi", retries=1, strict=True)
            with pytest.raises(LLMCircuitOpen):
                llm_utils.llm_generate("hi", retries=1, strict=True)

    def test_strict_default_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_STRICT_ERRORS", "1")
        reset_breaker()
        with patch.object(llm_utils.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            with pytest.raises(LLMUnavailable):
                llm_utils.llm_generate("hi", retries=1)

    def test_timeout_is_named_and_counts(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "2")
        reset_breaker()
        with patch.object(llm_utils.requests, "post",
                          side_effect=requests.exceptions.Timeout("slow")):
            llm_utils.llm_generate("hi", retries=1)
            assert last_llm_failure().reason == "timeout"
            assert get_breaker().state == CLOSED
            llm_utils.llm_generate("hi", retries=1)
            assert get_breaker().state == OPEN

    def test_4xx_does_not_trip_the_breaker(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "2")
        reset_breaker()
        with patch.object(llm_utils.requests, "post",
                          return_value=_http_error(400)):
            for _ in range(4):
                llm_utils.llm_generate("hi", retries=1)
        # The server answered; our payload was wrong. Pausing every other
        # caller's LLM access for that would be a self-inflicted outage.
        assert get_breaker().state == CLOSED
        assert last_llm_failure().reason == "request_rejected"

    def test_5xx_does_trip_the_breaker(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "2")
        reset_breaker()
        with patch.object(llm_utils.requests, "post",
                          return_value=_http_error(503)):
            for _ in range(2):
                llm_utils.llm_generate("hi", retries=1)
        assert get_breaker().state == OPEN
        assert last_llm_failure().reason == "transport"

    def test_half_open_probe_recovers_the_pipeline(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "1")
        monkeypatch.setenv("OLLAMA_BREAKER_PROBE_S", "0.05")
        reset_breaker()

        with patch.object(llm_utils.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            llm_utils.llm_generate("hi", retries=1)
        assert get_breaker().state == OPEN

        time.sleep(0.06)
        with patch.object(llm_utils.requests, "post",
                          return_value=_ok_response("recovered")) as post:
            assert llm_utils.llm_generate("hi", retries=1) == "recovered"
            assert post.call_count == 1
        assert get_breaker().state == CLOSED
        assert last_llm_failure() is None, "a success must clear the last failure"

    def test_empty_content_is_its_own_name(self):
        # The reasoning-block trap: transport fine, zero characters back.
        with patch.object(llm_utils.requests, "post",
                          return_value=_ok_response("")):
            assert llm_utils.llm_generate("hi", retries=1) == ""
        assert last_llm_failure().reason == "empty_response"
        assert get_breaker().state == CLOSED, "a healthy host must stay closed"


    def test_our_own_overload_never_trips_the_breaker(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "2")
        reset_breaker()

        def _shed():
            raise llm_utils.GpuOverloaded("queue depth 40")

        with patch.object(llm_utils, "_admit_background", side_effect=_shed):
            for _ in range(4):
                assert llm_utils.llm_generate("hi", retries=1) == ""
        # Shedding load is a decision we made about a HEALTHY host. Counting it
        # would fast-fail a working Ollama and turn a queue into an outage.
        assert get_breaker().state == CLOSED
        assert last_llm_failure().reason == "overloaded"


class TestLlmClientNamesFailures:
    """The tutoring path (services/core/llm_client.py)."""

    def _client(self):
        from services.core.llm_client import LLMClient
        return LLMClient(base_url="http://localhost:11434", model="test-model")

    def test_chat_names_the_outage_and_fast_fails(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "1")
        monkeypatch.setenv("OLLAMA_BREAKER_PROBE_S", "600")
        reset_breaker()
        from services.core import llm_client as lc

        with patch.object(lc.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")) as post:
            assert self._client().chat("sys", "user", retries=1) == ""
            assert get_breaker().state == OPEN
            calls = post.call_count
            assert self._client().chat("sys", "user", retries=1) == ""
            assert post.call_count == calls, "OPEN must not issue a request"
        assert last_llm_failure().reason == "circuit_open"

    def test_chat_json_separates_unreachable_from_bad_json(self, monkeypatch):
        reset_breaker()
        from services.core import llm_client as lc

        with patch.object(lc.requests, "post",
                          return_value=_ok_response("sorry, no JSON here")):
            with pytest.raises(LLMBadJSON):
                self._client().chat_json("sys", "user", retries=1, strict=True)

        reset_breaker()
        with patch.object(lc.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            with pytest.raises(LLMUnavailable) as exc:
                self._client().chat_json("sys", "user", retries=1, strict=True)
        assert not isinstance(exc.value, LLMBadOutput)

    def test_chat_json_uses_the_shared_repair_ladder(self):
        # Trailing comma: the old private regex threw the whole response away.
        reset_breaker()
        from services.core import llm_client as lc
        with patch.object(lc.requests, "post",
                          return_value=_ok_response('{"a": 1,}')):
            assert self._client().chat_json("sys", "user", retries=1) == {"a": 1}


class TestLlmGenerateJsonDistinguishes:
    def test_bad_json_is_not_an_outage(self):
        with patch.object(llm_utils.requests, "post",
                          return_value=_ok_response("I'm afraid I can't do that.")):
            with pytest.raises(LLMBadJSON) as exc:
                llm_utils.llm_generate_json("hi", retries=1, strict=True)
        assert not isinstance(exc.value, LLMUnavailable)
        assert exc.value.reason == "bad_json"

    def test_outage_is_not_bad_json(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "1")
        monkeypatch.setenv("OLLAMA_BREAKER_PROBE_S", "600")
        reset_breaker()
        with patch.object(llm_utils.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            with pytest.raises(LLMUnavailable):
                llm_utils.llm_generate_json("hi", retries=1, strict=True)
            with pytest.raises(LLMCircuitOpen) as exc:
                llm_utils.llm_generate_json("hi", retries=3, strict=True)
        assert not isinstance(exc.value, LLMBadOutput)

    def test_open_circuit_abandons_the_retry_ladder(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BREAKER_TRIP", "1")
        monkeypatch.setenv("OLLAMA_BREAKER_PROBE_S", "600")
        reset_breaker()
        with patch.object(llm_utils.requests, "post",
                          side_effect=requests.exceptions.ConnectionError("refused")) as post:
            llm_utils.llm_generate_json("hi", retries=5)
        # One real attempt trips it; the remaining four must not be made.
        assert post.call_count == 1

    def test_non_strict_still_returns_none_but_names_it(self):
        with patch.object(llm_utils.requests, "post",
                          return_value=_ok_response("not json at all")):
            assert llm_utils.llm_generate_json("hi", retries=1) is None
        assert last_llm_failure().reason == "bad_json"

    def test_schema_mismatch_is_an_output_failure(self):
        schema = {"type": "list", "items": {"required_keys": ["title"]}}
        with patch.object(llm_utils.requests, "post",
                          return_value=_ok_response('[{"wrong_key": 1}]')):
            with pytest.raises(LLMSchemaMismatch) as exc:
                llm_utils.llm_generate_json("hi", retries=1, schema=schema,
                                            strict=True)
        assert isinstance(exc.value, LLMBadOutput)
        assert not isinstance(exc.value, LLMUnavailable)

    def test_success_clears_the_thread_local(self):
        with patch.object(llm_utils.requests, "post",
                          return_value=_ok_response('[{"title": "A"}]')):
            assert llm_utils.llm_generate_json("hi", retries=1) == [{"title": "A"}]
        assert last_llm_failure() is None
