"""The context window is checked once, loudly, so it cannot be diagnosed twice.

Ollama serves a model at 4096 tokens unless its Modelfile says otherwise. The
one-shot subtree prompt is ~4200 with syllabus evidence attached, so on a
default-context model it 400s for most modules, falls back to the chunked path,
and produces a course a third shorter than its calendar -- while reporting
success. Four wrong hypotheses and several full rebuilds went into finding that,
because the failure is silent by construction: a fallback path is meant to be
quiet.
"""

import os
import sys
import unittest
from unittest import mock

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core import course_builder as cb  # noqa: E402


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class TestDetection(unittest.TestCase):
    def setUp(self):
        os.environ["OLLAMA_MODEL"] = "test-model"

    def tearDown(self):
        os.environ.pop("OLLAMA_MODEL", None)

    def test_an_explicit_num_ctx_is_read(self):
        with mock.patch("requests.post",
                        return_value=_Resp({"parameters": "num_ctx 16384\nstop x"})):
            assert cb._detect_context_window() == 16384

    def test_no_num_ctx_means_ollamas_default_not_the_architecture_maximum(self):
        """The model may support 262144 and still be SERVED at 4096. What the
        architecture could do is not what the server is doing."""
        with mock.patch("requests.post", return_value=_Resp({"parameters": "stop x"})):
            assert cb._detect_context_window() == 4096

    def test_an_unreachable_server_is_unknown_not_too_small(self):
        """None means "we could not determine it", never "it is too small" --
        refusing to build because a probe went unanswered would be the
        absent-vs-zero error in the place that blocks every course."""
        with mock.patch("requests.post", side_effect=OSError("down")):
            assert cb._detect_context_window() is None

    def test_a_non_200_is_also_unknown(self):
        with mock.patch("requests.post", return_value=_Resp({}, status=500)):
            assert cb._detect_context_window() is None


class TestThreshold(unittest.TestCase):
    def test_the_minimum_exceeds_ollamas_default(self):
        """A threshold at or below 4096 would never fire on the exact
        misconfiguration it exists to catch."""
        assert cb.MIN_CONTEXT_TOKENS > 4096

    def test_the_minimum_clears_the_measured_prompt_size(self):
        """The prompt that failed was 4212 tokens, and prompts grow as evidence
        improves — the threshold has to leave room for that."""
        assert cb.MIN_CONTEXT_TOKENS >= 8192


if __name__ == "__main__":
    unittest.main()
