"""Context sizing must never hand back a number the machine cannot pay for.

The failure this guards against is not an exception — it is a plausible
number. Ask for 131,072 tokens on a box that can afford 16,384 and nothing
raises; the model loads, the KV cache is allocated, and the machine OOMs
during a course build with the cause three layers away from the symptom.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from services.common import model_context as mc  # noqa: E402


GLM = {                                    # MLA — a compact cache
    "model_info": {
        "deepseek2.context_length": 202752,
        "deepseek2.block_count": 32,
        "deepseek2.attention.key_length_mla": 256,
        "deepseek2.attention.value_length_mla": 256,
        "deepseek2.attention.head_count_kv": 16,
    }
}
GEMMA = {                                  # no MLA — an expensive cache
    "model_info": {
        "gemma3.context_length": 131072,
        "gemma3.block_count": 48,
        "gemma3.attention.key_length": 256,
        "gemma3.attention.value_length": 256,
        "gemma3.attention.head_count_kv": 8,
    }
}
SMALL_CTX = {
    "model_info": {"qwen3.context_length": 4096, "qwen3.block_count": 40,
                   "qwen3.attention.key_length": 128,
                   "qwen3.attention.value_length": 128,
                   "qwen3.attention.head_count_kv": 8}
}


def _with(info):
    return patch.object(mc, "_show", return_value=info)


class TestMaxContext(unittest.TestCase):
    def setUp(self):
        mc._cache.clear()

    def test_reads_context_regardless_of_architecture_prefix(self):
        """The key is namespaced by arch, so it cannot be looked up literally."""
        with _with(GLM):
            self.assertEqual(mc.max_context("m"), 202752)
        with _with(GEMMA):
            self.assertEqual(mc.max_context("m"), 131072)

    def test_unknown_model_returns_none_rather_than_a_guess(self):
        with _with({}):
            self.assertIsNone(mc.max_context("m"))


class TestKvSizing(unittest.TestCase):
    def setUp(self):
        mc._cache.clear()

    def test_mla_models_are_cheaper_per_token(self):
        """MLA keeps one latent per layer; using the uncompressed figure would
        overstate the cost several-fold and refuse a context that fits."""
        with _with(GLM):
            glm = mc.kv_bytes_per_token("m", "f16")
        with _with(GEMMA):
            gemma = mc.kv_bytes_per_token("m", "f16")
        self.assertLess(glm, gemma)

    def test_q8_halves_f16(self):
        with _with(GEMMA):
            self.assertEqual(mc.kv_bytes_per_token("m", "q8_0"),
                             mc.kv_bytes_per_token("m", "f16") // 2)

    def test_missing_geometry_returns_none_not_zero(self):
        """Zero would divide into an infinite context."""
        with _with({"model_info": {"x.context_length": 8192}}):
            self.assertIsNone(mc.kv_bytes_per_token("m"))


class TestOptimalContext(unittest.TestCase):
    def setUp(self):
        mc._cache.clear()

    def test_never_exceeds_what_the_model_supports(self):
        with _with(SMALL_CTX):
            self.assertLessEqual(mc.optimal_context("m", available_gb=64.0),
                                 4096)

    def test_does_not_chase_the_largest_that_fits(self):
        """A huge machine must still not allocate context the builder will
        never send — that memory is not free, it is just not yet spent."""
        with _with(GLM):
            self.assertEqual(mc.optimal_context("m", available_gb=200.0),
                             mc.TARGET_CONTEXT)

    def test_shrinks_when_memory_is_tight(self):
        with _with(GEMMA):
            roomy = mc.optimal_context("m", available_gb=12.0)
            tight = mc.optimal_context("m", available_gb=3.5)
        self.assertLess(tight, roomy)

    def test_no_headroom_still_returns_a_usable_floor(self):
        with _with(GEMMA):
            self.assertGreaterEqual(mc.optimal_context("m", available_gb=0.0),
                                    mc.LADDER[0])

    def test_quantised_cache_buys_more_context_for_the_same_ram(self):
        with _with(GEMMA):
            f16 = mc.optimal_context("m", available_gb=6.0, cache_type="f16")
            q8 = mc.optimal_context("m", available_gb=6.0, cache_type="q8_0")
        self.assertGreater(q8, f16)

    def test_warns_when_the_prompt_will_be_truncated(self):
        """The GLM failure mode: a context too small for the prompt produces
        fluent output missing every requested section, and nothing errors."""
        with _with(SMALL_CTX), self.assertLogs(mc.logger, "WARNING") as log:
            mc.optimal_context("m", available_gb=64.0)
        self.assertIn("TRUNCATED", " ".join(log.output))


class TestRecommendedEnv(unittest.TestCase):
    def setUp(self):
        mc._cache.clear()

    def test_recommends_quantised_cache_and_flash_attention_together(self):
        """llama.cpp silently ignores a quantised KV cache without flash
        attention, which looks identical to it working."""
        with _with(GLM):
            env, _ = mc.recommended_env("m", available_gb=12.0)
        self.assertEqual(env["OLLAMA_KV_CACHE_TYPE"], "q8_0")
        self.assertEqual(env["OLLAMA_FLASH_ATTENTION"], "1")
        self.assertEqual(env["OLLAMA_MAX_LOADED_MODELS"], "1")

    def test_context_recommendation_clears_the_builder_prompt(self):
        with _with(GLM):
            env, _ = mc.recommended_env("m", available_gb=12.0)
        self.assertGreaterEqual(int(env["OLLAMA_CONTEXT_LENGTH"]),
                                mc.BUILDER_PROMPT_TOKENS)


if __name__ == "__main__":
    unittest.main()
