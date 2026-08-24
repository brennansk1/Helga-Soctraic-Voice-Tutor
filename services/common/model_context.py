"""Pick a context length that fits the prompt AND the machine.

WHY THIS EXISTS
---------------
`num_ctx` was set nowhere in this codebase, so every call ran at Ollama's
default. Verified in the running runner's own command line:

    llama-server ... -c 4096

while the models in use are trained for far more:

    GLM-4.7-Flash-REAP-23B     202,752
    Qwen3.6-27B-MTP            262,144
    qwen3.5:9b                 262,144
    qwen3:14b                   40,960

The builder prompt — instructions, research brief, and hydrated source text —
runs to roughly 4,800 tokens before the model writes a word. At 4,096 the tail
is discarded, and the tail is where the required-section spec lives. That is
consistent with what the model gate observed on GLM: fluent prose containing
NONE of the required sections, as though it had never been asked for them.

So this is not a per-model tuning knob. It is a pipeline-wide defect that
every model has been running under, and the effect is worst on exactly the
prompts that matter most — the long, well-grounded ones.

WHY NOT JUST SET IT HUGE
------------------------
KV cache is allocated from the same pool as the weights. On a 24 GB box
already holding 13.5 GB of them, an unbounded context is how the OOM comes
back — and the builder would not use it anyway, since it sends ~6,000 tokens.

So the target is the SMALLEST rung that comfortably clears the prompt
(TARGET_CONTEXT), capped by what the model supports and what the machine can
actually spare, sized from the model's own published geometry rather than
guessed. Quantising the cache to q8_0 then buys the same context for half the
memory, which on the tighter models is the difference between fitting and not.
"""

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# What the builder actually needs. Measured: the per-concept prompt is ~4,800
# tokens of input, and the depth contract asks for 500+ words out, which with
# markdown structure runs to ~1,200 tokens. 8,192 clears both with room for a
# longer research brief; below ~6,000 the prompt itself starts being cut.
BUILDER_PROMPT_TOKENS = 4_800
BUILDER_OUTPUT_TOKENS = 1_200
MIN_USEFUL_CONTEXT = 8_192

# What we actually aim for. Roughly 2.7x the measured prompt, which absorbs a
# longer research brief or a book excerpt without paying for headroom the
# builder will never use.
TARGET_CONTEXT = 16_384

# KV CACHE QUANTISATION — the cheapest context there is.
#
# llama.cpp stores the cache as f16 by default: 2 bytes per element. Ollama
# exposes `OLLAMA_KV_CACHE_TYPE`, and q8_0 halves that for a quality cost that
# is negligible next to a Q4_K_M weight quantisation already in play — the
# cache is being rounded far less than the weights it attends over.
#
# It is the difference between fitting the builder's context and not. Measured
# per-token on the models here:
#
#     gemma-3-12b   393 KB/tok f16  ->  196 KB/tok q8_0   (16k: 6.0 -> 3.0 GB)
#     Qwen3.6-27B   266 KB/tok f16  ->  133 KB/tok q8_0
#     GLM-4.7       48 KB/tok f16   ->   24 KB/tok q8_0   (MLA already compact)
#
# NOTE: llama.cpp requires flash attention for quantised KV. Ollama enables it
# automatically on Metal for supported models, but OLLAMA_FLASH_ATTENTION=1
# makes it explicit — without it the cache type is silently ignored, which
# looks identical to it working.
_KV_BYTES = {"f16": 2.0, "f32": 4.0, "q8_0": 1.0, "q4_0": 0.5}
KV_CACHE_TYPE = os.getenv("OLLAMA_KV_CACHE_TYPE", "f16").lower()

# Ladder rather than a continuous value: llama.cpp allocates the KV cache up
# front, and round numbers make the memory arithmetic legible in logs.
LADDER = (4_096, 8_192, 16_384, 32_768, 65_536, 131_072)

# Leave this much RAM to the rest of the machine — the containers, the web UI,
# and whatever the learner is doing in a browser.
RESERVE_GB = 3.0

_cache = {}


def _show(model, timeout=15):
    """Ollama's /api/show for one model. HTTP, not the CLI, so it works from
    inside a container where the binary is absent."""
    if model in _cache:
        return _cache[model]
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/show",
            data=json.dumps({"model": model}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            _cache[model] = json.load(fh)
    except Exception as e:
        logger.warning(f"model_context: /api/show failed for {model}: {e}")
        _cache[model] = {}
    return _cache[model]


def max_context(model):
    """The context the model was TRAINED for, or None if unknown.

    Read from `<architecture>.context_length` rather than a hardcoded table:
    the key is namespaced by architecture (`deepseek2.context_length`,
    `qwen3.context_length`), so a lookup table would need editing for every new
    model, which is precisely the maintenance that gets skipped.
    """
    info = _show(model).get("model_info", {})
    for key, value in info.items():
        if key.endswith(".context_length"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def kv_bytes_per_token(model, cache_type=None):
    """Bytes of KV cache each token costs, from the model's own geometry.

    Returns None when the fields are absent, because a wrong estimate here
    spends real memory — a caller that cannot size the cache should fall back
    to a conservative context rather than to a confident guess.
    """
    info = _show(model).get("model_info", {})

    def field(suffix):
        for key, value in info.items():
            if key.endswith(suffix):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    layers = field(".block_count")
    if not layers:
        return None

    # MLA architectures (deepseek2) compress the cache and publish the
    # compressed dimensions separately; prefer those when present, since using
    # the uncompressed figure overstates the cost several-fold and would refuse
    # a context that fits comfortably.
    k_len = field(".attention.key_length_mla") or field(".attention.key_length")
    v_len = field(".attention.value_length_mla") or field(".attention.value_length")
    if not k_len or not v_len:
        return None

    heads_kv = field(".attention.head_count_kv") or 1
    if field(".attention.key_length_mla"):
        heads_kv = 1                      # MLA keeps one latent per layer

    per_element = _KV_BYTES.get((cache_type or KV_CACHE_TYPE), 2.0)
    return int(layers * heads_kv * (k_len + v_len) * per_element)


def optimal_context(model, available_gb=None, want_tokens=None,
                    cache_type=None):
    """Largest sensible context for this model on this machine, in tokens.

    Never returns less than the model's own maximum would allow AND less than
    the prompt needs, so a small-context model is reported honestly rather than
    padded to a number it cannot honour.
    """
    want = want_tokens or (BUILDER_PROMPT_TOKENS + BUILDER_OUTPUT_TOKENS)
    ceiling = max_context(model)

    if available_gb is None:
        try:
            from services.common import memory_guard
            available_gb = memory_guard.snapshot().available_gb
        except Exception:
            available_gb = 4.0            # conservative when we cannot tell

    budget_bytes = max(0.0, available_gb - RESERVE_GB) * 2 ** 30
    per_token = kv_bytes_per_token(model, cache_type)

    # SMALLEST THAT COMFORTABLY FITS, NOT LARGEST THAT WOULD FIT.
    #
    # An earlier version took the biggest rung the memory allowed and produced
    # 131,072 tokens for GLM at 5.9 GB of KV — on a 24 GB box that is also
    # holding 13.5 GB of weights. Two things wrong with it:
    #
    #   * `available_gb` is measured BEFORE the weights load, so the budget it
    #     divides up is one the model is about to consume most of;
    #   * the builder never uses that context. It sends ~6,000 tokens. Paying
    #     gigabytes for 131k of headroom buys nothing and is how the OOM comes
    #     back.
    #
    # So aim at TARGET_CONTEXT — comfortably above the real prompt, with room
    # for a longer research brief — and only fall BELOW it when the model or
    # the machine cannot manage that.
    best = LADDER[0]
    for rung in LADDER:
        if rung > TARGET_CONTEXT:
            break
        if ceiling and rung > ceiling:
            break
        if per_token and rung * per_token > budget_bytes:
            break
        best = rung

    # Do not claim a context the model cannot serve.
    if ceiling:
        best = min(best, ceiling)

    if best < want:
        logger.warning(
            f"model_context: {model} can only take {best:,} tokens here, but "
            f"the builder prompt needs ~{want:,}. Output will be generated "
            f"from a TRUNCATED prompt — expect missing sections.")
    return best


def describe(model):
    """One line for logs and the preflight."""
    ceiling = max_context(model)
    per_token = kv_bytes_per_token(model)
    chosen = optimal_context(model)
    kv_gb = (chosen * per_token / 2 ** 30) if per_token else None
    return (f"{model}: max {ceiling:,} tok, using {chosen:,}"
            + (f" (~{kv_gb:.1f} GB KV @ {KV_CACHE_TYPE})" if kv_gb
               else " (KV size unknown)"))


def recommended_env(model, available_gb=None):
    """The environment Ollama should run with for this model.

    Returned rather than applied: these are server-start variables, so setting
    them is a restart, and that is the caller's decision to make — not a side
    effect of asking what the right value is.
    """
    f16 = optimal_context(model, available_gb=available_gb)
    q8 = optimal_context(model, available_gb=available_gb, cache_type="q8_0")
    env = {
        "OLLAMA_CONTEXT_LENGTH": str(q8),
        "OLLAMA_KV_CACHE_TYPE": "q8_0",
        "OLLAMA_FLASH_ATTENTION": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
    }
    return env, {"context_f16": f16, "context_q8_0": q8}


def running_context():
    """What the SERVER is actually using right now, or None.

    The configured value and the effective value are different things — an
    env var the app ignored looks identical to one it honoured until you read
    the runner's command line, which is the only place the truth appears.
    """
    try:
        import subprocess
        out = subprocess.run(["pgrep", "-lf", "llama-server"],
                             capture_output=True, text=True, timeout=5).stdout
        for token in out.split():
            pass
        parts = out.split()
        for i, part in enumerate(parts):
            if part == "-c" and i + 1 < len(parts):
                return int(parts[i + 1])
            if part.startswith("--ctx-size=") or part.startswith("-c="):
                return int(part.split("=", 1)[1])
    except Exception:
        pass
    return None
