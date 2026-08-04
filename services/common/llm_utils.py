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


def llm_generate(
    prompt: str,
    sys_prompt: str = 'Expert curriculum designer. Response must be a Python list of dictionaries. IMPORTANT: Use double quotes (") for all strings.',
    retries: int = 3,
    max_tokens: int = 800,
    progress_callback=None,
    think: bool = False,
    json_format=None,
    role: str = None,
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
    """
    for attempt in range(retries):
        timeout = max(
            90, min(600, max_tokens * 0.5)
        )  # 90s floor, scale with tokens, 10 min cap

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
            temp = 0.7 + (
                attempt * 0.1
            )  # Standard temperature, slight increase on retry

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
                # — OLLAMA_KEEP_ALIVE on the host is the reliable lever, and
                # LLMClient.warn_if_not_pinned() checks it at startup.
                "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "-1"),
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
                **({"format": json_format} if json_format else {}),
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

            logger.info(
                f"[{req_id}] LLM Response (len={len(content)}, words={len(content.split())})"
            )
            return content
        except requests.exceptions.Timeout as e:
            heartbeat_stop.set()
            logger.warning(
                f"LLM Timeout after {timeout:.0f}s (attempt {attempt + 1}/{retries}, tokens={max_tokens})"
            )
            if progress_callback:
                progress_callback(
                    f"LOG: LLM call timed out after {timeout:.0f}s, retrying ({attempt + 1}/{retries})..."
                )
            if attempt < retries - 1:
                time.sleep(2)
        except Exception as e:
            heartbeat_stop.set()
            logger.error(f"LLM Error (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                time.sleep(2**attempt)
    return ""


def llm_generate_json(
    prompt: str,
    sys_prompt: str = "Expert content assistant. Always return structured JSON data.",
    retries: int = 3,
    max_tokens: int = 800,
    expected_type: str = "list",
    schema: dict = None,
    progress_callback=None,
) -> Any:
    """Wrapper that combines generation and JSON parsing with retries.

    Args:
        schema: Optional schema dict. Used BOTH to grammar-constrain generation
            (Ollama `format`) and to validate the parsed result (LLM-2).
        progress_callback: Optional callback for heartbeat updates during LLM calls.

    Robustness ladder, strongest first — smaller/quantised models produce
    malformed JSON often enough that relying on repair alone loses data:
      1. constrained decoding, so invalid JSON cannot be generated
      2. repair_json() for unconstrained output (trailing commas, quotes, ...)
      3. retry, escalating to constrained JSON mode if the first attempt failed
    """
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
        )
        if not raw:
            continue

        result = parse_llm_json(raw, expected_type=expected_type)
        if result is not None:
            # LLM-2: Validate against schema if provided
            if schema and not validate_schema(result, schema):
                logger.warning(
                    f"Attempt {attempt + 1}/{retries}: Schema validation failed"
                )
                continue
            return result

        logger.warning(
            f"Attempt {attempt + 1}/{retries}: Failed to parse valid {expected_type} from LLM."
        )
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
