"""model_roles.py — which model serves which job, and on which server.

WHY THIS EXISTS

Course building and live tutoring have opposite objectives, and until now they
shared one model because there was one environment variable.

    BUILD    batch. The user already waits minutes, the artifact is durable and
             read many times, and the measured failure is a QUALITY failure
             that costs time: on a 12-concept mastery-4 build, 12 of 58 LLM
             calls (~469s) were depth-contract retries. A model that meets the
             contract first time is not just better — it is FASTER in wall
             clock, even if it decodes more slowly per token.

    TUTOR    interactive. A student is watching a cursor. Latency is the
             product. Quality above "good enough to question well" buys much
             less than a second saved.

This is SPRINT_PLAN A4.1c ("split the models: large for batch, 9B for
interactive"). The seam is here so trying a candidate is an env var and a
measurement rather than a re-plumbing exercise.

WHAT A ROLE RESOLVES TO

A base URL as well as a model name, because the interesting candidates are not
all served by the same thing. Ollama serves GGUF; the MLX quantisations that
make a 27B fit in 24 GB have no GGUF equivalent and are served by
`mlx_lm.server`, on its own port. Both expose an OpenAI-compatible /v1, which
is why one client can talk to either — the code already sends the union of
their reasoning-disable fields for exactly this reason.

DEFAULTS ARE UNCHANGED

With no role variables set, every role resolves to OLLAMA_MODEL on OLLAMA_URL,
which is what the stack did before this module existed.

BEFORE YOU SWITCH THE BUILDER, READ THIS

Two things this seam cannot decide for you:

  * MEMORY. A 2-bit 27B is ~7 GB and a 9B Q4 is ~6 GB. The target box is 24 GB
    and was measured already swapping 10.3/11.3 GB with Docker down. They
    cannot both stay resident, and the recommended host setting is
    OLLAMA_MAX_LOADED_MODELS=1. In v1 that is mostly fine — a course is locked
    until it is fully built, so building and tutoring are sequential — but a
    tutoring turn DURING a build forces a model swap, which defeats the
    interactive slot the GPU gate reserves.

  * CONSTRAINED DECODING. Skeletons, aid plans and grading all pass a JSON
    schema via `format`, and that is load-bearing: it is why malformed JSON
    stopped being the normal failure. Verify the candidate's server honours it
    before trusting the build path to it.

And one thing the project already learned: Ternary-Bonsai-27B at 1.7-bit
produced clean output on a simplified prompt (4/4) and degenerated into
repetition on the REAL builder prompt (3/3). See docs/MODE_A_STATUS.md §5. Gate
any candidate on the real prompt — `tools/model_gate.py` does exactly that.
"""

import os

BUILD = "build"
TUTOR = "tutor"

_DEFAULT_URL = "http://host.docker.internal:11434"


def _clean(value):
    value = (value or "").strip()
    return value or None


def resolve(role=TUTOR):
    """Return (base_url, model) for `role`.

    Falls back to the single-model configuration for any role that has no
    override, so an unset environment behaves exactly as it did before.
    """
    base = _clean(os.getenv("OLLAMA_URL")) or _DEFAULT_URL
    model = (_clean(os.getenv("LLM_MODEL"))
             or _clean(os.getenv("OLLAMA_MODEL"))
             or "qwen3.5:9b")

    if role == BUILD:
        return (_clean(os.getenv("HELGA_BUILD_URL")) or base,
                _clean(os.getenv("HELGA_BUILD_MODEL")) or model)
    if role == TUTOR:
        return (_clean(os.getenv("HELGA_TUTOR_URL")) or base,
                _clean(os.getenv("HELGA_TUTOR_MODEL")) or model)
    return base, model


def is_split():
    """True when build and tutor resolve differently.

    Worth knowing at startup: a split configuration means model swaps, and on a
    24 GB box a swap is seconds of load, not a context switch.
    """
    return resolve(BUILD) != resolve(TUTOR)


def describe():
    """One line per role, for a health endpoint or a startup log."""
    build_url, build_model = resolve(BUILD)
    tutor_url, tutor_model = resolve(TUTOR)
    lines = [f"build: {build_model} @ {build_url}",
             f"tutor: {tutor_model} @ {tutor_url}"]
    if is_split():
        lines.append(
            "SPLIT: the two roles use different models. If they are served by "
            "the same Ollama with OLLAMA_MAX_LOADED_MODELS=1, a tutoring turn "
            "during a build forces a model swap.")
    return "\n".join(lines)
