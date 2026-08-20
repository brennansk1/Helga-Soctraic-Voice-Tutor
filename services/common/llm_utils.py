"""
Shared LLM parsing utilities for Helga.

Extracted from course_builder.py to be reusable across services.
Handles JSON parsing with:
- Regex-first extraction
- Retry logic
- Fallback generation
- Validation + sanitization
"""

import os
import re
import ast
import json
import time
import logging
import threading
import requests
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

# B23.2: llm_utils callers are the build pipelines (course_builder, wizard
# drafting, librarian flashcard/quiz gen), so calls admit at BACKGROUND
# priority: at most 1 in-flight slot and never granted while a live tutoring
# turn waits. In processes without the core gate (RAG container), the import
# fails and the gate is a no-op — those callers are never blocked.
try:
    from services.core.gpu_gate import get_gpu_gate, GpuOverloaded, LLMContext, BACKGROUND
    _GATE_AVAILABLE = True
except ImportError:
    _GATE_AVAILABLE = False

    class GpuOverloaded(Exception):
        pass

# A7: the circuit breaker lives in services/common precisely so THIS module gets
# one. It is the build path — dozens of sequential calls, each with a 90-600 s
# timeout — so it is the caller that suffers most when the host dies, and until
# now it was the only major caller with no breaker at all.
from services.common.llm_breaker import (
    get_breaker, strict_default, record_failure_reason, last_llm_failure,
    clear_llm_failure, LLMCircuitOpen, LLMTimeout, LLMTransportError,
    LLMOverloaded, LLMBadJSON, LLMSchemaMismatch, LLMEmptyResponse,
    LLMRequestRejected, LLMUnavailable, LLMError, LLMBadOutput,
)


class _NoopSlot:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _admit_background():
    """Acquire a BACKGROUND slot on the GPU gate (no-op outside core)."""
    if not _GATE_AVAILABLE:
        return _NoopSlot()
    return get_gpu_gate().admit(LLMContext(BACKGROUND, "_system"))


LLM_API_URL = os.getenv(
    "LLM_API_URL", "http://host.docker.internal:11434/v1/chat/completions"
)

from services.common.model_roles import BUILD as ROLE_BUILD, resolve as _resolve_role


def resolve_role(role):
    """Endpoint + model for a role, honouring an explicit LLM_API_URL override.

    LLM_API_URL predates the role seam and some deployments set it, so it still
    wins when present — otherwise the role's own base URL is used, which is
    what lets the build role point at a different server (mlx_lm.server) from
    the tutor role.
    """
    base, model = _resolve_role(role or ROLE_BUILD)
    explicit = os.getenv("LLM_API_URL")
    return (explicit or base), model


def _escape_inner_quotes(text: str) -> str:
    """Escape unescaped double quotes inside JSON string values.

    Walks the text tracking whether we are inside a string. Inside a string, a
    `"` only genuinely terminates it when the next non-space character is one
    of `, } ] :` or end-of-input; anything else means the model failed to
    escape an inner quote, so we escape it.

    Deliberately conservative: if the result still doesn't parse, the caller
    falls back to its other strategies. Never raises.
    """
    if not text or '"' not in text:
        return text
    try:
        out = []
        in_string = False
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\\" and in_string and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            if ch == '"':
                if not in_string:
                    in_string = True
                    out.append(ch)
                else:
                    j = i + 1
                    while j < n and text[j] in " \t\r\n":
                        j += 1
                    if j >= n or text[j] in ",}]:":
                        in_string = False
                        out.append(ch)
                    else:
                        out.append('\\"')  # inner quote the model didn't escape
                i += 1
                continue
            out.append(ch)
            i += 1
        return "".join(out)
    except Exception:
        return text


def _requote_strings(text: str) -> str:
    """Convert single-quoted string literals to double-quoted ones, safely.

    The regex pairs below only catch `'key':` and `: 'value'`. They miss single
    quotes inside ARRAYS — `['Mon','Tue']` — which a small model produces
    constantly, and which then fails to parse for want of two characters.

    A global `'` -> `"` substitution is worse than useless: it destroys every
    apostrophe ("Newton's law" becomes a syntax error). So this scans character
    by character, tracks whether it is inside a double-quoted string, and only
    treats `'` as a delimiter outside one. Returns the input unchanged if it
    hits an unterminated quote, so a failed repair never makes things worse.
    """
    out, i, n, in_double = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_double:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            j, buf = i + 1, []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == "'":
                    break
                buf.append(text[j])
                j += 1
            if j >= n:
                return text                      # unterminated — leave it alone
            out.append('"' + "".join(buf).replace('"', '\\"') + '"')
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Curly quotes from models that "stylise" their output. json.loads sees these as
# ordinary letters, so a single smart quote invalidates the whole response.
_SMART_QUOTES = {
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "«": '"', "»": '"', "′": "'", "″": '"',
}


def _try_libraries(text: str):
    """Third-party repair, tried only after the stdlib passes have failed.

    Backends in preference order — all optional, all the same call shape, so a
    missing one costs nothing and the stdlib result stands:

      1. `fast-json-repair` — Rust/PyO3 port of json_repair (MIT). Ships wheels
         for linux x86-64/ARM64 and macOS x86-64/ARM64, so it covers both the
         M4 host and the containers. CAVEAT: it requires **Python 3.11+**, while
         this repo targets 3.10+. On 3.10 there is no wheel and pip would try to
         build it from source (needs a Rust toolchain) — hence the guarded
         import and the json_repair fallback rather than a hard dependency.
         Its headline 10-30x speedup is real but irrelevant here: repair runs a
         handful of times per ~30 s LLM call. The reason to prefer it is that it
         is a maintained implementation, not the speed.
      2. `json-repair` — pure Python, the reference implementation, no version
         constraint. This is the one that actually has to work.
      3. `json5` — JSON5 superset: comments, unquoted keys, trailing commas.
      4. `ast.literal_eval` — stdlib, for models that emit repr() output.
    """
    for module_name in ("fast_json_repair", "json_repair"):
        try:
            module = __import__(module_name)
            fixed = module.repair_json(text)
            # Every backend signals total failure by returning an empty
            # container; treat that as "no repair" rather than as valid output,
            # or a broken response silently becomes {} downstream.
            if fixed and fixed not in ("", '""', "{}", "[]"):
                json.loads(fixed)
                return fixed
        except Exception:
            continue
    try:
        import json5
        return json.dumps(json5.loads(text))
    except Exception:
        pass
    # Python dict/list literal — models sometimes emit repr() output wholesale.
    try:
        import ast
        return json.dumps(ast.literal_eval(text))
    except Exception:
        pass
    return None


def repair_json(text: str) -> str:
    """LLM-1: Repair common JSON malformations from LLM output.

    Escalating repair — each stage is a strictly more aggressive rewrite, and
    the FIRST candidate that actually parses is returned. That ordering matters:
    the old version applied every transform unconditionally, so a late, blunt
    pass could damage a string an earlier, gentler pass had already fixed.

    Handles, in order of how often a quantised model trips on them:
    - already-valid JSON (returned untouched — this must stay idempotent)
    - markdown ```json fences wrapped around the object
    - curly/smart quotes
    - // and /* */ comments
    - Python literals (True/False/None) and NaN/Infinity
    - trailing commas before ] or }
    - unquoted keys  ({key: 1} -> {"key": 1})
    - single-quoted keys AND values, including inside arrays
    - unescaped double quotes inside a string value
    - truncated output — unclosed brackets are balanced
    - finally: json-repair / json5 / ast.literal_eval, if installed

    Always returns a string (never raises), so every existing caller keeps
    working; callers still json.loads() the result themselves.
    """
    if not text:
        return text

    def _ok(candidate):
        try:
            json.loads(candidate)
            return True
        except (json.JSONDecodeError, ValueError, TypeError):
            return False

    if _ok(text):
        return text                              # idempotent on valid input

    work = text

    # Markdown fence around the payload.
    fence = re.search(r"```(?:json|JSON)?\s*\n?(.*?)```", work, re.DOTALL)
    if fence:
        work = fence.group(1).strip()
        if _ok(work):
            return work

    for bad, good in _SMART_QUOTES.items():
        work = work.replace(bad, good)
    if _ok(work):
        return work

    # Comments are invalid JSON but a model that has seen a lot of JS emits them.
    work = re.sub(r"/\*.*?\*/", "", work, flags=re.DOTALL)
    work = re.sub(r"(?m)//(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$).*$", "", work)

    work = re.sub(r"\bTrue\b", "true", work)
    work = re.sub(r"\bFalse\b", "false", work)
    work = re.sub(r"\bNone\b", "null", work)
    work = re.sub(r"\b(NaN|Infinity|-Infinity)\b", "null", work)
    work = re.sub(r",\s*([\]\}])", r"\1", work)
    if _ok(work):
        return work

    # Unquoted keys — claimed by the old docstring but never actually done.
    work = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', work)
    if _ok(work):
        return work

    # Targeted single-quote passes first (cheap, precise) …
    work = re.sub(r"(?<=[\[{,\s])'([^']*?)'\s*:", r'"\1":', work)
    work = re.sub(r":\s*'([^']*?)'", r': "\1"', work)
    if _ok(work):
        return work

    # … then the general scanner, which also reaches inside arrays.
    requoted = _requote_strings(work)
    if _ok(requoted):
        return requoted
    work = requoted

    # Unescaped double quotes INSIDE a string value. This is the single most
    # common malformation from smaller/quantised models — they quote the
    # student verbatim or use scare quotes and never escape them, e.g.
    #     {"evidence": "the tutor said "excellent" here"}
    # It is also the one failure repair used to give up on, so the whole
    # response was discarded. json.loads cannot recover it, but the structure
    # is predictable: a value run between the opening quote and the true
    # closing quote (the one followed by , } ] or EOL).
    work = _escape_inner_quotes(work)
    if _ok(work):
        return work

    # Truncated JSON repair: close unclosed brackets/braces. This is what a
    # max_tokens cut-off looks like, and it is very common on long generations.
    open_braces = work.count("{") - work.count("}")
    open_brackets = work.count("[") - work.count("]")
    if open_braces > 0 or open_brackets > 0:
        work = work.rstrip().rstrip(",")
        work += "}" * max(0, open_braces)
        work += "]" * max(0, open_brackets)
        if _ok(work):
            return work

    # Last resort: purpose-built libraries, on the ORIGINAL text so they are not
    # handed the damage of a failed rewrite.
    for candidate in (text, work):
        salvaged = _try_libraries(candidate)
        if salvaged and _ok(salvaged):
            return salvaged

    return work                                  # best effort; caller decides


def validate_schema(data: Any, schema: dict) -> bool:
    """LLM-2: Validate LLM output against a simple schema.

    Schema format:
    {
        'type': 'list',  # or 'dict'
        'items': {       # for lists
            'required_keys': ['title', 'uid'],  # required dict keys
            'optional_keys': ['description']
        }
    }
    """
    if not schema:
        return True

    expected_type = schema.get("type", "any")

    if expected_type == "list":
        if not isinstance(data, list):
            return False
        items_schema = schema.get("items", {})
        required_keys = items_schema.get("required_keys", [])
        if required_keys and data:
            for item in data:
                if not isinstance(item, dict):
                    return False
                for key in required_keys:
                    if key not in item:
                        logger.warning(
                            f"Schema validation: missing required key '{key}' in item"
                        )
                        return False
    elif expected_type == "dict":
        if not isinstance(data, dict):
            return False
        required_keys = schema.get("required_keys", [])
        for key in required_keys:
            if key not in data:
                logger.warning(f"Schema validation: missing required key '{key}'")
                return False

    return True


# A COLD LOAD IS NOT A HANG.
#
# The timeout floor is 90s. Loading nail-35b-a3b-ctx from disk was MEASURED at
# 3m31s on this machine -- so the first call after Ollama has evicted the
# weights times out three times, the breaker opens on three "transport
# failures", and a build that was about to work fails before the model has
# finished reading itself into memory. Watched it happen to the golden matrix.
#
# The two cases look identical from the client (no bytes, no response) and are
# told apart by asking Ollama what is resident: if the model we want is NOT in
# /api/ps, nothing is wrong, it is still loading. That timeout then buys a
# longer one instead of a strike against the breaker.
COLD_LOAD_TIMEOUT = 420          # > the 211s measured, with room for a slower disk


def _weights_resident(model, base_url, timeout=5):
    """True/False if Ollama answers, None if we cannot tell.

    None matters: an unreachable Ollama must NOT be read as "still loading" or
    a genuinely dead host would retry forever on the long timeout.
    """
    try:
        root = (base_url or "").split("/v1/")[0].rstrip("/")
        if not root:
            return None
        r = requests.get(root + "/api/ps", timeout=timeout)
        if r.status_code != 200:
            return None
        names = {m.get("name", "") for m in (r.json() or {}).get("models", [])}
    except Exception:
        return None
    return any(n == model or n.split(":")[0] == str(model).split(":")[0]
               for n in names)


def llm_generate(
    prompt: str,
    sys_prompt: str = 'Expert curriculum designer. Response must be a Python list of dictionaries. IMPORTANT: Use double quotes (") for all strings.',
    retries: int = 3,
    max_tokens: int = 800,
    progress_callback=None,
    think: bool = False,
    json_format=None,
    role: str = None,
    strict: bool = None,
) -> str:
    """Call LLM with retry logic.

    `role` selects which model serves the call — "build" (the default for this
    helper, whose callers are course_builder and asset_collector) or "tutor".
    Unset role variables resolve both to OLLAMA_MODEL, so the default
    configuration is unchanged. See services/common/model_roles.py.

    Adaptations for Ollama + Qwen3.5:
    - Standard temperature (0.7) with slight increase on retry
    - Generous timeouts scaled to max_tokens
    - Heartbeat thread for progress feedback during long calls
    - `think` defaults to False: qwen3.5 is a reasoning model whose thinking
      block otherwise consumes the whole token budget and returns empty
      content. See the comment at the request body below for measurements.

    A7 — failure naming. `strict=True` (or `LLM_STRICT_ERRORS=1`) raises the
    named exception for whatever actually went wrong instead of returning "".
    The default stays "" so no existing caller changes behaviour — but the name
    is recorded either way and is readable afterwards via `last_llm_failure()`.
    A build that dies can therefore always say WHICH thing happened: the host
    was unreachable, our own gate shed the call, the server rejected the
    payload, or the model answered with nothing.
    """
    strict = strict_default() if strict is None else strict
    breaker = get_breaker()
    clear_llm_failure()
    failure = None
    _cold_load_grace = False
    for attempt in range(retries):
        # A7: fast-fail BEFORE the heartbeat thread, the GPU slot and the socket.
        # While the circuit is open the answer is already known, and the whole
        # point is to not spend a 90-600 s timeout rediscovering it — a build
        # forty concepts long would otherwise take an hour to report a host that
        # died in its first minute.
        if not breaker.allow():
            failure = record_failure_reason(
                LLMCircuitOpen(f"retry in {breaker.retry_after():.0f}s"))
            logger.error("LLM call skipped — %s", failure)
            if progress_callback:
                progress_callback(f"LOG: {failure.user_message}")
            break
        timeout = max(
            90, min(600, max_tokens * 0.5)
        )  # 90s floor, scale with tokens, 10 min cap
        if _cold_load_grace:
            # One attempt already timed out with the weights not resident.
            timeout = max(timeout, COLD_LOAD_TIMEOUT)

        # Heartbeat: send periodic "still working" updates while LLM is blocked
        heartbeat_stop = threading.Event()

        def _heartbeat(cb, stop_event, req_id, attempt_num, max_tok):
            elapsed = 0
            while not stop_event.is_set():
                stop_event.wait(15)  # Every 15 seconds
                if stop_event.is_set():
                    break
                elapsed += 15
                if cb:
                    cb(
                        f"LOG: Waiting for LLM response... ({elapsed}s elapsed, attempt {attempt_num})"
                    )

        try:
            req_id = f"req_{int(time.time())}_{attempt}"
            _role_url, _role_model = resolve_role(role)
            # TEMPERATURE POLICY.
            # Prose generation benefits from a nudge upward on retry — a second
            # identical sample is wasted work. STRUCTURED generation is the
            # opposite: when a schema pins the shape, the retry exists because
            # the CONTENT was wrong, and raising temperature makes a
            # shape-conforming-but-wrong answer *more* likely, not less. So
            # schema-constrained calls start cooler and get cooler still.
            if isinstance(json_format, dict):
                temp = max(0.15, 0.35 - attempt * 0.1)
            else:
                temp = 0.7 + attempt * 0.1

            data = {
                # Resolved per ROLE, not from one global variable. This helper
                # is the build-time path (course_builder, asset_collector), and
                # building has the opposite objective to tutoring: it is batch,
                # so quality is worth latency. Unset role variables resolve to
                # OLLAMA_MODEL, so the default configuration is unchanged.
                # See services/common/model_roles.py.
                "model": _role_model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temp,
                # Keep the weights resident between calls. A build runs dozens
                # of these with research fetches in between; without this the
                # model can idle out mid-pipeline and each phase pays a cold
                # load. The /v1 shim may ignore it depending on Ollama version
                # — OLLAMA_KEEP_ALIVE must agree here and on the host, and
                # LLMClient.warn_if_not_pinned() checks it at startup.
                "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "30m"),
                # A1/A6: qwen3.5 is a reasoning model. Left enabled, it spends
                # the ENTIRE token budget on its thinking block and returns an
                # empty `content` — measured on this exact prompt:
                #   max_tokens=400 -> 0 chars   (finish_reason=length)
                #   max_tokens=800 -> 0 chars   (finish_reason=length)
                # Every structured-generation call in course_builder uses
                # 400-800, so skeletons, units, lessons and concepts were
                # routinely coming back BLANK and falling through to retries
                # and generic fallbacks. That is the likely root cause of both
                # the long-blamed "~30% JSON failure rate" and the degenerate
                # lessons (7 of 21 with <=1 concept) in the sample course.
                #
                # This endpoint is Ollama's OpenAI-compatible /v1 shim, which
                # IGNORES the native `think` field — verified: think=False
                # still returned 0 chars. The field it honors is
                # `reasoning_effort`, and only the value "none" works
                # ("low" still returned 0 chars):
                #   reasoning_effort="none" -> 780 chars, finish=stop,  8.8s
                #   (default/thinking)      ->   0 chars, finish=length, 34.4s
                # So this is both the correctness fix and a ~4x speedup.
                # Pass think=True to restore deliberation where it earns its
                # latency; default off for build-time structured output.
                **({} if think else {
                    # Two different servers, two different levers —
                    # each ignores the other's field, so send both:
                    #   Ollama /v1  : reasoning_effort="none"
                    #   mlx_lm /v1  : chat_template_kwargs.enable_thinking
                    # Measured on mlx_lm with the ternary 27B: thinking ON
                    # returned 539 chars of reasoning and took 13s; OFF
                    # returned the answer in 1s. Same trap as Ollama, and
                    # the Ollama field alone does NOT disable it here.
                    "reasoning_effort": "none",
                    "chat_template_kwargs": {"enable_thinking": False},
                }),
                # Grammar-constrained decoding. Smaller/quantised models emit
                # malformed JSON often enough that post-hoc repair is a losing
                # game — unescaped quotes inside string values are the common
                # killer and are not reliably repairable. Passing a schema (or
                # "json") makes Ollama constrain generation so invalid JSON
                # cannot be produced in the first place. repair_json() stays as
                # a backstop for callers that don't pass one.
                # CONSTRAINED DECODING. `format` is Ollama's NATIVE-API field and is
                # SILENTLY IGNORED on the OpenAI-compatible /v1 endpoint we post to —
                # the correct field there is `response_format`. Verified 2026-08-18 on
                # nail-35b-a3b with a nested schema: `format` -> 2408 chars of
                # free-form shape that failed a strict parse; `response_format` -> 202
                # chars matching the schema exactly. Send BOTH so the same payload
                # works against a native endpoint and against /v1 (and mlx_lm).
                # ...but the two endpoints do not accept the same SCHEMA.
                # Ollama's native `format` honours minItems (verified: minItems=5
                # took a 8-unit answer to 10). The /v1 json_schema validator
                # REJECTS it with a 400 in ~0.3 s. Sending one schema to both
                # therefore turned a working call into an instant failure — and
                # because the caller treats a failure as "empty", it silently
                # disabled the whole one-shot path for 5 of 6 modules and made
                # every course a third shorter, with no error surfaced.
                #
                # So: full schema to `format`, a /v1-safe reduction to
                # `response_format`. The strictest endpoint must not get to
                # dictate what the permissive one is allowed to enforce.
                # CONTEXT WINDOW. Ollama defaults to 4096 unless told otherwise,
                # and nothing here ever told it. Measured: the one-shot subtree
                # prompt is ~4212 tokens, so it 400'd with
                # "exceeds the available context size (4096)" for 5 of 6 modules
                # in EVERY build, silently falling back to the chunked path and
                # making every course a third shorter than its calendar.
                #
                # It also explains why the failure got worse as the prompts got
                # better: adding real syllabus detail to a module's scope pushed
                # more prompts over a line nobody knew was there. Three separate
                # hypotheses (prompt wording, token budget, schema keywords) were
                # tested and discarded before the error BODY was logged and
                # answered it in one line.
                "options": {"num_ctx": _num_ctx()},
                **({"format": json_format,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "helga_schema",
                                        "schema": _v1_safe_schema(json_format)},
                    }} if isinstance(json_format, dict) else
                   {"format": json_format} if json_format else {}),
            }
            logger.info(
                f"[{req_id}] LLM Call (tokens:{max_tokens}, temp:{temp:.1f}, "
                f"think={think}, constrained={bool(json_format)}): "
                f"sys='{sys_prompt[:60]}...'"
            )

            # Start heartbeat if we have a callback
            if progress_callback:
                hb_thread = threading.Thread(
                    target=_heartbeat,
                    args=(
                        progress_callback,
                        heartbeat_stop,
                        req_id,
                        attempt + 1,
                        max_tokens,
                    ),
                    daemon=True,
                )
                hb_thread.start()

            with _admit_background():
                resp = requests.post(
                    _role_url.rstrip("/") + "/v1/chat/completions"
                    if not _role_url.endswith("/chat/completions") else _role_url,
                    json=data, timeout=timeout)
            heartbeat_stop.set()  # Stop heartbeat on response
            resp.raise_for_status()
            content = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            # The transport worked, so the host is healthy regardless of what it
            # said — close the circuit here, not after the content check below.
            breaker.record_success()

            logger.info(
                f"[{req_id}] LLM Response (len={len(content)}, words={len(content.split())})"
            )
            if not content:
                # A healthy host that returns nothing is its own failure mode
                # (the thinking-block trap), and it used to be invisible: the
                # caller got "" and could not tell it apart from a dead host.
                failure = record_failure_reason(
                    LLMEmptyResponse(f"tokens={max_tokens}, think={think}"))
                logger.warning("[%s] %s", req_id, failure)
                if attempt < retries - 1:
                    continue
                break
            # A later attempt succeeding must erase an earlier attempt's name,
            # or `last_llm_failure()` reports a failure that did not happen.
            clear_llm_failure()
            return content
        except GpuOverloaded as e:
            # OUR gate shed this call; the model service is fine. It must not
            # touch the breaker — tripping on self-inflicted backpressure would
            # turn a queue into an outage — but it IS worth retrying, because
            # overload is transient by definition and this is the background
            # build path, which is shed first and can afford to wait. Retrying
            # is also what the old generic handler did; only the name is new.
            heartbeat_stop.set()
            failure = record_failure_reason(LLMOverloaded(str(e)))
            logger.warning(f"LLM call shed by GPU gate (attempt "
                           f"{attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
        except requests.exceptions.Timeout:
            heartbeat_stop.set()
            if not _cold_load_grace and attempt < retries - 1:
                resident = _weights_resident(_role_model, _role_url)
                if resident is False:
                    # Still reading the weights off disk. Not a failure, and
                    # emphatically not a breaker strike: three of these would
                    # open the breaker on a machine where nothing is wrong.
                    _cold_load_grace = True
                    logger.warning(
                        "LLM timeout after %.0fs, but %s is not resident yet -- "
                        "treating as a cold load and allowing %ds",
                        timeout, _role_model, COLD_LOAD_TIMEOUT)
                    if progress_callback:
                        progress_callback(
                            "LOG: the model is still loading into memory; "
                            "this first call can take a few minutes")
                    time.sleep(2)
                    continue
            failure = record_failure_reason(
                LLMTimeout(f"no response in {timeout:.0f}s"))
            breaker.record_failure(failure)
            logger.warning(
                f"LLM Timeout after {timeout:.0f}s (attempt {attempt + 1}/{retries}, tokens={max_tokens})"
            )
            if progress_callback:
                progress_callback(
                    f"LOG: LLM call timed out after {timeout:.0f}s, retrying ({attempt + 1}/{retries})..."
                )
            if attempt < retries - 1:
                time.sleep(2)
        except requests.exceptions.ConnectionError as e:
            heartbeat_stop.set()
            failure = record_failure_reason(LLMTransportError(str(e)[:200]))
            breaker.record_failure(failure)
            logger.warning(f"LLM connection failed (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
        except Exception as e:
            heartbeat_stop.set()
            # A 4xx carries its reason in the BODY, and logging only the status
            # line made this undiagnosable: 24 identical "400 Client Error" lines
            # per build, disabling the one-shot subtree path for 5 of 6 modules
            # and shortening every course by a third, with nothing to act on.
            # Hours were spent bisecting payload fields that a single logged
            # body would have answered immediately.
            _body = ""
            _resp = getattr(e, "response", None)
            if _resp is not None:
                try:
                    _body = (_resp.text or "")[:600]
                except Exception:
                    _body = "<unreadable response body>"
            _status = getattr(_resp, "status_code", None)
            # 4xx and 5xx are opposite facts and only one of them is the host's.
            # A 400 means the server is up and refused OUR payload (the context
            # overflow above is exactly this) — counting it would trip the
            # breaker on a healthy Ollama and pause every other caller for a bug
            # that lives in our request. A 5xx is the host failing, so it counts.
            if _status is not None and 400 <= _status < 500:
                failure = record_failure_reason(
                    LLMRequestRejected(f"HTTP {_status}: {_body[:200]}"))
            elif isinstance(e, requests.exceptions.RequestException):
                failure = record_failure_reason(
                    LLMTransportError(f"HTTP {_status}: {str(e)[:200]}"
                                      if _status else str(e)[:200]))
                breaker.record_failure(failure)
            else:
                failure = record_failure_reason(LLMError(str(e)[:200]))
            logger.error(f"LLM Error (attempt {attempt + 1}) [{failure.reason}]: {e}"
                         + (f" | body: {_body}" if _body else ""))
            if attempt < retries - 1:
                time.sleep(2**attempt)
    if failure is not None:
        record_failure_reason(failure)
        if strict:
            raise failure
    return ""



def _describe_schema_mismatch(result, schema, path="root"):
    """Name the FIRST concrete way `result` violates `schema`, in words a model
    can act on. Deliberately shallow and cheap: the point is a usable hint for
    the next attempt, not a full JSON-Schema validator."""
    try:
        exp = schema.get("type")
        if exp == "object":
            if not isinstance(result, dict):
                return f"{path} must be a JSON object, got {type(result).__name__}"
            for key in schema.get("required", []):
                if key not in result:
                    return f"{path} is missing the required key '{key}'"
            for key, sub in (schema.get("properties") or {}).items():
                if key in result:
                    deeper = _describe_schema_mismatch(result[key], sub, f"{path}.{key}")
                    if deeper:
                        return deeper
        elif exp == "array":
            if not isinstance(result, list):
                return f"{path} must be a JSON array, got {type(result).__name__}"
            if not result:
                return f"{path} is an empty array; it must contain at least one item"
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for i, item in enumerate(result[:3]):
                    deeper = _describe_schema_mismatch(item, item_schema, f"{path}[{i}]")
                    if deeper:
                        return deeper
        elif exp == "string" and not isinstance(result, str):
            return f"{path} must be a string, got {type(result).__name__}"
        elif exp in ("number", "integer") and not isinstance(result, (int, float)):
            return f"{path} must be a number, got {type(result).__name__}"
    except Exception:
        pass
    return "" if schema else ""

# JSON Schema keywords the OpenAI-compatible /v1 json_schema validator rejects.
# Ollama's native `format` accepts them, so they are kept there and stripped only
# from the /v1 copy.
_V1_UNSUPPORTED = ("minItems", "maxItems", "minLength", "maxLength", "minimum",
                   "maximum", "pattern", "uniqueItems", "minProperties",
                   "maxProperties")


def _num_ctx():
    """Context window to request. Ollama's default of 4096 is smaller than this
    project's real prompts, which carry syllabus evidence and module scope.

    32768 IS CHOSEN FROM MEASUREMENT. See docs/MEMORY_ALLOCATION_PLAN.md.

    KV on this model is cheap, predicted from its own config and then verified
    by loading at each size: `full_attention_interval 4` over 40 blocks means
    only 10 layers hold a KV cache, at 2 KV heads x (256+256) = 20 KB/token
    FP16. Predicted deltas from 16k of +0.31/+0.94/+2.19 GB for 32k/64k/128k
    came back measured at +0.33/+0.99/+1.91.

    So why 32k rather than 64k, when both fit? Because the ceiling is really
    about what can be CO-RESIDENT. Measured totals against a ~15.0 GB ceiling
    (past ~16 GB this machine falls off a cliff -- throughput is flat at
    ~31 tok/s and then generation stops returning usable output at all):

        @32k  13.51 GB  + MiniCheck fp16 14.97  + bge-m3 14.57  -> both fit
        @64k  14.17 GB  + MiniCheck fp16 15.63  + bge-m3 15.23  -> neither fits

    At 32k the planned verifier and embedder run WITHOUT unloading and reloading
    a 12.7 GB model between phases, at ~142 s of cold load each way. That is
    worth more than a window nothing currently needs: a lesson-batched hydration
    is 5-10 concepts, and the ledger context is deliberately a few hundred
    tokens.

    Raise via OLLAMA_NUM_CTX for a generation-only pass with nothing else
    resident; do not raise it globally without re-measuring.
    """
    try:
        return max(4096, int(os.getenv("OLLAMA_NUM_CTX", "32768")))
    except (TypeError, ValueError):
        return 32768


def _v1_safe_schema(schema):
    """A copy of `schema` with keywords /v1 refuses removed.

    Returns a COPY: mutating the caller's schema would strip the constraint from
    the native `format` field too, which is the one place it works.
    """
    if isinstance(schema, dict):
        return {k: _v1_safe_schema(v) for k, v in schema.items()
                if k not in _V1_UNSUPPORTED}
    if isinstance(schema, list):
        return [_v1_safe_schema(v) for v in schema]
    return schema


def llm_generate_json(
    prompt: str,
    sys_prompt: str = "Expert content assistant. Always return structured JSON data.",
    retries: int = 3,
    max_tokens: int = 800,
    expected_type: str = "list",
    schema: dict = None,
    progress_callback=None,
    strict: bool = None,
) -> Any:
    """Wrapper that combines generation and JSON parsing with retries.

    Args:
        schema: Optional schema dict. Used BOTH to grammar-constrain generation
            (Ollama `format`) and to validate the parsed result (LLM-2).
        progress_callback: Optional callback for heartbeat updates during LLM calls.
        strict: raise the named failure instead of returning None. Defaults to
            `LLM_STRICT_ERRORS`.

    Robustness ladder, strongest first — smaller/quantised models produce
    malformed JSON often enough that relying on repair alone loses data:
      1. constrained decoding, so invalid JSON cannot be generated
      2. repair_json() for unconstrained output (trailing commas, quotes, ...)
      3. retry, escalating to constrained JSON mode if the first attempt failed

    A7 — this function used to return None for two unrelated facts: "the model
    service is unreachable" and "the model returned unusable JSON". They have
    nothing in common. The first means stop the build and start Ollama; the
    second means the prompt or the schema is wrong and retrying is the right
    move. Collapsing them is why a dead host produced a course full of stub
    concepts marked "ready" instead of a build that failed and said why.

    The retry ladder is deliberately NOT duplicated: `llm_generate` owns the
    transport retries and the breaker owns how many of them are worth making.
    This loop retries only what a retry can actually fix — output the model got
    wrong — and abandons the moment the breaker says the host is gone.
    """
    strict = strict_default() if strict is None else strict
    failure = None
    _base_prompt = prompt   # retries append corrective notes; keep the original
    for attempt in range(retries):
        # Constrain ONLY with a caller-supplied schema.
        #
        # An earlier version escalated to Ollama's generic `format:"json"` on
        # retry. That is actively harmful: generic JSON mode does not just make
        # the output parseable, it changes the SHAPE the model chooses. Measured
        # on the module-generation prompt:
        #     no format   -> [{"title":"...","level":1}]                (208 ch)
        #     format=json -> [{"module_1":{"title":...,"content":[...]}}] (1630 ch)
        # Both are valid JSON; only the first matches what the builder consumes.
        # It broke course creation outright — "LLM consistently failed to
        # generate 3 modules after 3 attempts".
        #
        # A schema constrains shape as well as syntax, so it is safe. Generic
        # JSON mode is not, and repair_json() already covers plain syntax
        # errors on the unconstrained path.
        fmt = schema if schema else None
        raw = llm_generate(
            prompt,
            sys_prompt=sys_prompt,
            retries=1,
            max_tokens=max_tokens,
            progress_callback=progress_callback,
            json_format=fmt,
            strict=False,   # classified here instead, so the name survives
        )
        if not raw:
            # Whatever llm_generate hit, it named it. Keep that name rather than
            # flattening it into "no JSON" — they need different responses.
            failure = last_llm_failure() or LLMEmptyResponse("no content")
            if isinstance(failure, (LLMCircuitOpen, LLMRequestRejected)):
                # Neither gets better by asking again in this loop: the circuit
                # is open (every further call is a no-op that returns instantly),
                # or the server rejected a payload we would send verbatim again.
                # Overload is NOT in this list — llm_generate already backs off
                # and retries it, because it is transient by definition.
                logger.error("Aborting JSON generation — %s", failure)
                break
            continue

        result = parse_llm_json(raw, expected_type=expected_type)
        if result is not None:
            # LLM-2: Validate against schema if provided
            if schema and not validate_schema(result, schema):
                # Retry against the NAMED mismatch rather than re-rolling blind.
                # This is the pattern the depth contract already proved on this
                # project: regenerating "against the named missing element"
                # converges, while an identical re-roll mostly reproduces the
                # same defect and burns a call.
                _detail = _describe_schema_mismatch(result, schema)
                failure = record_failure_reason(LLMSchemaMismatch(_detail))
                logger.warning(
                    f"Attempt {attempt + 1}/{retries}: Schema validation failed "
                    f"({_detail})"
                )
                prompt = (
                    f"{_base_prompt}\n\n"
                    f"YOUR PREVIOUS RESPONSE WAS REJECTED: {_detail}\n"
                    f"Return the SAME content, corrected to the required shape. "
                    f"Do not add commentary."
                )
                continue
            clear_llm_failure()
            return result

        failure = record_failure_reason(
            LLMBadJSON(f"expected {expected_type}, got {len(raw)} chars: "
                       f"{raw[:120]!r}"))
        logger.warning(
            f"Attempt {attempt + 1}/{retries}: Failed to parse valid {expected_type} from LLM."
        )

    if failure is not None:
        record_failure_reason(failure)
        logger.error("JSON generation failed [%s]: %s",
                     failure.reason, failure.user_message)
        if strict:
            raise failure
    return None


def extract_python_list(text: str) -> Optional[List[Dict]]:
    """
    Robust extraction of a Python/JSON list from LLM output.

    Tries in order:
    1. Extract from ```json ... ``` code blocks
    2. Extract from ``` ... ``` code blocks
    3. JSON parse of full text
    4. Python literal_eval of full text
    5. Regex for [...] bracket matching
    6. Returns None if all fail
    """
    if not text:
        return None
    try:
        text = text.strip()

        # Safety net: strip any <think>...</think> tags if present
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # 1. Extract from markdown code blocks
        code_block_match = re.search(
            r"```(?:json|python)?\s*\n?(.*?)\n?```", text, re.DOTALL
        )
        if code_block_match:
            block_content = code_block_match.group(1).strip()
            try:
                return json.loads(block_content)
            except json.JSONDecodeError:
                try:
                    return ast.literal_eval(block_content)
                except (ValueError, SyntaxError):
                    pass

        # 2. Clean markdown wrappers if present
        cleaned = text
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(\w+)?\n", "", cleaned)
            cleaned = re.sub(r"\n```$", "", cleaned)

        # 3. JSON parsing (primary) — try with repair first
        repaired = repair_json(cleaned)
        try:
            result = json.loads(repaired)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
        except json.JSONDecodeError:
            pass

        # 3b. Try original without repair
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # 4. Python literal parsing (fallback)
        try:
            result = ast.literal_eval(cleaned)
            if isinstance(result, list):
                return result
        except (ValueError, SyntaxError):
            pass

        # 5. Regex fallback for JSON array
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            candidate = repair_json(match.group(0))
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    return ast.literal_eval(candidate)
                except (ValueError, SyntaxError):
                    pass

        # 6. Try to find JSON object (single dict, wrap in list)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            candidate = match.group(0)
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return [obj]
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to extract list from: {text[:100]}")
    except Exception as e:
        logger.error(f"Extraction Error: {e}")
    return None


def parse_llm_json(raw_text: str, expected_type: str = "list") -> Any:
    """
    Defensive LLM JSON parsing pipeline.

    Args:
        raw_text: Raw LLM output
        expected_type: "list", "dict", or "any"

    Returns:
        Parsed structure, or None if all parsing fails
    """
    if not raw_text:
        return None

    # Safety net: strip any <think>...</think> tags if present
    raw_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()

    result = extract_python_list(raw_text)

    if result is not None:
        if expected_type == "dict" and isinstance(result, list) and len(result) == 1:
            return result[0]
        return result

    # For dict type, try direct object extraction
    if expected_type == "dict":
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

    # XML-tagged fallback: extract <item><title>...</title></item> patterns
    xml_result = _parse_xml_fallback(raw_text, expected_type)
    if xml_result is not None:
        logger.info("Recovered data from XML-tagged fallback format")
        return xml_result

    return None


def _parse_xml_fallback(text: str, expected_type: str = "list") -> Any:
    """Extract structured data from XML-tagged LLM output.

    Handles patterns like:
        <item><title>Foo</title><description>Bar</description></item>
        <module><title>Foo</title></module>
    """
    if not text or "<" not in text:
        return None

    try:
        # Find all XML-like item blocks
        tag_names = ["item", "module", "unit", "lesson", "concept", "card", "entry"]
        items = []
        for tag in tag_names:
            pattern = rf"<{tag}>(.*?)</{tag}>"
            blocks = re.findall(pattern, text, re.DOTALL)
            if blocks:
                for block in blocks:
                    item = {}
                    # Extract key-value pairs from nested tags
                    kvs = re.findall(r"<(\w+)>(.*?)</\1>", block, re.DOTALL)
                    for key, val in kvs:
                        item[key] = val.strip()
                    if item:
                        items.append(item)
                break  # Use first matching tag type

        if items:
            if expected_type == "dict" and len(items) == 1:
                return items[0]
            return items
    except Exception as e:
        logger.debug(f"XML fallback parse error: {e}")
    return None


def extract_grade_from_llm(text: str) -> Optional[int]:
    """Extract a numeric grade (1-5) from LLM grading response."""
    if not text:
        return None

    # Try JSON extraction first
    try:
        match = re.search(r"\{.*?\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            grade = data.get("grade")
            if grade is not None:
                return max(1, min(5, int(grade)))
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Regex for "Grade: N", "grade: N", "N/5" patterns
    patterns = [
        r"grade[:\s]*(\d)",
        r"(\d)\s*/\s*5",
        r"score[:\s]*(\d)",
        r"rating[:\s]*(\d)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            g = int(match.group(1))
            if 1 <= g <= 5:
                return g

    # Last resort: find any single digit
    digits = re.findall(r"\b(\d)\b", text)
    for d in digits:
        g = int(d)
        if 1 <= g <= 5:
            return g

    return None
