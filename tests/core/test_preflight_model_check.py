"""The startup preflight must not report a model Ollama cannot serve.

The check was `any(OLLAMA_MODEL in m for m in installed)` — a substring test.
Asking for 'qwen3:14b' with only 'qwen3:14b-q4_K_M' pulled produced a green
preflight, and then every generation call 404'd. A preflight that passes while
the system is broken is worse than none: it sends you debugging the wrong
layer.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from main import model_is_available  # noqa: E402


class TestExactTagRequired(unittest.TestCase):

    def test_exact_match_passes(self):
        ok, _ = model_is_available("qwen3.5:9b", ["qwen3.5:9b", "llama3:8b"])
        self.assertTrue(ok)

    def test_quantisation_suffix_is_a_different_model(self):
        ok, near = model_is_available("qwen3:14b", ["qwen3:14b-q4_K_M"])
        self.assertFalse(
            ok,
            "Ollama serves tags, not prefixes. 'qwen3:14b' 404s when only "
            "'qwen3:14b-q4_K_M' is pulled — the old substring test called this "
            "green."
        )
        self.assertEqual(near, "qwen3:14b-q4_K_M")

    def test_larger_sibling_does_not_satisfy_a_smaller_request(self):
        ok, _ = model_is_available("qwen3.5:9b", ["qwen3.5:9b-instruct"])
        self.assertFalse(ok)

    def test_missing_model_reports_the_near_miss(self):
        ok, near = model_is_available("qwen3.5:27b", ["qwen3.5:9b"])
        self.assertFalse(ok)
        self.assertEqual(
            near, "qwen3.5:9b",
            "'not found' next to a list containing a lookalike reads as a "
            "display quirk unless the near miss is named"
        )

    def test_near_miss_does_not_cross_a_version_boundary(self):
        """Against the real installed set, 'qwen3:14b' must point at
        'qwen3:14b-q4_K_M' — not at 'qwen3.5:9b', which a base-name prefix
        match reaches because 'qwen3.5:9b'.startswith('qwen3')."""
        installed = ["qwen3.5:9b", "qwen3.5:4b", "qwen3:14b-q4_K_M",
                     "qwen2.5-coder:14b"]
        ok, near = model_is_available("qwen3:14b", installed)
        self.assertFalse(ok)
        self.assertEqual(near, "qwen3:14b-q4_K_M")

    def test_no_near_miss_when_nothing_is_related(self):
        ok, near = model_is_available("qwen3.5:9b", ["llama3:8b"])
        self.assertFalse(ok)
        self.assertIsNone(near)


class TestLatestAliasIsHonoured(unittest.TestCase):
    """Ollama genuinely resolves a bare name to ':latest' — that alias is real
    and must not be broken by tightening the check."""

    def test_bare_name_matches_latest_tag(self):
        ok, _ = model_is_available("qwen3.5", ["qwen3.5:latest"])
        self.assertTrue(ok)

    def test_tagged_request_does_not_fall_back_to_latest(self):
        ok, _ = model_is_available("qwen3.5:9b", ["qwen3.5:latest"])
        self.assertFalse(ok)


class TestDegenerateInput(unittest.TestCase):

    def test_empty_installed_list(self):
        ok, near = model_is_available("qwen3.5:9b", [])
        self.assertFalse(ok)
        self.assertIsNone(near)

    def test_none_installed_list(self):
        ok, _ = model_is_available("qwen3.5:9b", None)
        self.assertFalse(ok)

    def test_blank_entries_are_ignored(self):
        ok, near = model_is_available("qwen3.5:9b", ["", None, "qwen3.5:9b"])
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
