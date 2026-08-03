"""A1/A6 regression: reasoning must be disabled by default on the /v1 shim.

qwen3.5 is a reasoning model. Against Ollama's OpenAI-compatible endpoint, a
request that leaves reasoning enabled spends the whole token budget on the
thinking block and returns an EMPTY `content`. Measured on a representative
course-building prompt:

    max_tokens=400, reasoning on   ->   0 chars, finish_reason=length, 34.4s
    max_tokens=800, reasoning on   ->   0 chars, finish_reason=length
    max_tokens=600, effort="none"  -> 780 chars, finish_reason=stop,    8.8s

Every structured-generation call in course_builder.py uses 400-800 tokens, and
llm_client.chat/chat_stream default to 512 — so both course building AND live
tutoring were silently returning blank text.

Two traps this test locks down:
  1. The /v1 shim IGNORES the native `think` field. Only `reasoning_effort`
     works, so a "fix" that sets `think: False` here is a no-op.
  2. Only the value "none" takes effect; "low" still returned 0 chars.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)


def _captured_payload(post_mock):
    assert post_mock.called, "no HTTP call was made"
    return post_mock.call_args.kwargs.get('json') or post_mock.call_args[0][1]


class TestLlmUtilsReasoningEffort(unittest.TestCase):
    def _post_mock(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}]
        }
        return resp

    def test_default_disables_reasoning(self):
        from services.common import llm_utils
        with patch.object(llm_utils.requests, 'post',
                          return_value=self._post_mock()) as post:
            llm_utils.llm_generate("hi", max_tokens=400, retries=1)
        payload = _captured_payload(post)
        self.assertEqual(
            payload.get("reasoning_effort"), "none",
            "Without reasoning_effort='none' the /v1 shim returns empty content "
            "at these token budgets. Note `think: False` does NOT work here."
        )

    def test_think_true_opts_back_in(self):
        from services.common import llm_utils
        with patch.object(llm_utils.requests, 'post',
                          return_value=self._post_mock()) as post:
            llm_utils.llm_generate("hi", max_tokens=400, retries=1, think=True)
        payload = _captured_payload(post)
        self.assertNotIn(
            "reasoning_effort", payload,
            "think=True must let the model deliberate normally."
        )


class TestLlmClientReasoningEffort(unittest.TestCase):
    def setUp(self):
        """Close the Ollama circuit breaker.

        It is process-global and other tests in a full-suite run trip it open,
        which makes chat() return early without issuing a request — so this
        regression test would silently stop testing anything.
        """
        from services.core.gpu_gate import CLOSED
        from services.core.llm_client import get_breaker
        b = get_breaker()
        with b._lock:
            b._state = CLOSED
            b._fails = 0
            b._probing = False

    def _client(self):
        from services.core.llm_client import LLMClient
        return LLMClient(base_url="http://localhost:11434", model="qwen3.5:9b")

    def _post_mock(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return resp

    def _capture(self, **kwargs):
        """Capture the outgoing payload.

        No try/except around the call: the sys.modules['requests'] pollution
        from tests/integration/test_full_e2e.py that once made this necessary
        is fixed at its source, so a raise here now means a real defect and
        should fail loudly rather than be swallowed.
        """
        from services.core import llm_client
        c = self._client()
        with patch.object(llm_client.requests, 'post',
                          return_value=self._post_mock()) as post:
            c.chat("sys", "user", **kwargs)
        return _captured_payload(post)

    def test_chat_defaults_to_no_reasoning(self):
        payload = self._capture(max_tokens=512)
        self.assertEqual(
            payload.get("reasoning_effort"), "none",
            "chat() defaults to max_tokens=512, which returns empty content "
            "with reasoning enabled — this silently broke live tutoring."
        )

    def test_chat_think_true_opts_back_in(self):
        self.assertNotIn("reasoning_effort", self._capture(max_tokens=512, think=True))


class TestNoIneffectiveThinkFieldOnV1(unittest.TestCase):
    """Guard against 'fixing' this with the field the /v1 shim ignores."""

    def test_sources_do_not_send_think_field_to_v1(self):
        for rel in ('services/common/llm_utils.py', 'services/core/llm_client.py'):
            with open(os.path.join(_root, rel)) as f:
                for i, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith('#'):
                        continue
                    self.assertNotIn(
                        '"think"', stripped,
                        f"{rel}:{i} sends a `think` field to the /v1 shim, which "
                        f"ignores it. Use reasoning_effort='none'."
                    )


if __name__ == '__main__':
    unittest.main()
