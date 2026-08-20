"""Centralized LLM client for core-logic service.

Wraps Ollama's OpenAI-compatible API with:
- Chat completions (system + user messages)
- JSON mode for structured output
- Streaming support
- Retry with exponential backoff
- Connection health checking
"""

import json
import logging
import os
import re
import time

import requests

try:
    from gpu_gate import (get_gpu_gate, GpuOverloaded, LLMContext, INTERACTIVE,
                          get_breaker)
except ImportError:
    from services.core.gpu_gate import (get_gpu_gate, GpuOverloaded, LLMContext,
                                        INTERACTIVE, get_breaker)

# A7: import the failure taxonomy from its own home rather than through
# gpu_gate. gpu_gate re-exports it for compatibility, but the two import paths
# above ("gpu_gate" vs "services.core.gpu_gate") can load the module twice in a
# single process, and exception classes that are `except`-ed by identity must
# come from ONE module object or a caller's `except LLMUnavailable` silently
# stops matching.
from services.common.llm_breaker import (
    strict_default, record_failure_reason, last_llm_failure, clear_llm_failure,
    LLMCircuitOpen, LLMTimeout, LLMTransportError, LLMOverloaded,
    LLMBadJSON, LLMEmptyResponse, LLMRequestRejected, LLMError, LLMUnavailable,
)

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://host.docker.internal:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'nail-35b-a3b-ctx')
# How long Ollama keeps the weights resident after a request. The policy
# REVERSED with A6: pinning ("-1") optimised the one number a stopwatch can
# see and spent ~12.7 GB of a ~15.0 GB safe ceiling on the hours nobody is
# studying — the same hours the night audit and host-native TTS/STT want the
# RAM. 30m outlasts a student thinking about a hard question; what it costs is
# one ~2-minute reload on the first question after a genuine break, which the
# UI narrates. This default MUST agree with docker-compose/.env: a per-request
# keep_alive overrides the server's own setting, so one stale "-1" here would
# silently re-pin the model no matter what the host says.
KEEP_ALIVE = os.environ.get('OLLAMA_KEEP_ALIVE', '30m')


class LLMClient:
    """Client for Ollama's OpenAI-compatible chat API."""

    def __init__(self, base_url=None, model=None):
        self.base_url = base_url or OLLAMA_URL
        self.model = model or OLLAMA_MODEL
        self.api_url = f"{self.base_url}/v1/chat/completions"

    @staticmethod
    def _user_content(user_message, images):
        """Build the user message content. Plain string when no images; otherwise
        the OpenAI-compat multimodal array (text + image_url parts) that Ollama's
        vision models (e.g. qwen3.5:9b) accept. `images` items may be data URIs or
        bare base64 (assumed PNG)."""
        if not images:
            return user_message
        parts = [{"type": "text", "text": user_message}]
        for img in images:
            url = img if str(img).startswith("data:") else f"data:image/png;base64,{img}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        return parts

    def chat(self, system_prompt, user_message, max_tokens=512,
             temperature=0.6, json_mode=False, json_schema=None, images=None,
             timeout=60, retries=3, ctx=None, think=False, strict=None):
        """Send a chat completion request to Ollama.

        Args:
            system_prompt: System role message
            user_message: User role message
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            json_mode: If True, request plain JSON output (format="json")
            json_schema: Optional JSON Schema dict. When provided, Ollama (>=0.5)
                grammar-constrains the output to the schema — far more reliable
                than plain json_mode. Takes precedence over json_mode.
            images: Optional list of images (data URIs or base64) for a multimodal
                model (qwen3.5:9b). Enables vision — diagrams/figures in tutoring.
            timeout: Request timeout in seconds
            retries: Number of retry attempts
            strict: A7 — raise the NAMED failure instead of returning "".
                Defaults to LLM_STRICT_ERRORS. "" was the only signal this
                method ever gave, and it meant five different things: circuit
                open, GPU shed, timeout, HTTP error, or a model that genuinely
                returned nothing. Callers keeping the "" contract can still read
                which one it was from `last_llm_failure()`.

        Returns:
            Generated text content, or empty string on failure
        """
        strict = strict_default() if strict is None else strict
        clear_llm_failure()
        failure = None
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._user_content(user_message, images)}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            # Ask the server to keep the weights resident. Ollama unloads after
            # five idle minutes by default, and a student reading a question
            # and thinking about it routinely exceeds that — so the next answer
            # pays a cold load of several seconds before generating a token.
            #
            # This is belt-and-braces, not the primary mechanism: the /v1
            # OpenAI-compatible shim may ignore the field depending on the
            # Ollama version, and it ignores unknown fields harmlessly either
            # way. The reliable lever is OLLAMA_KEEP_ALIVE — set both on the
            # host and here, because whichever request runs last resets the
            # timer with ITS value. `warn_residency()` checks at startup.
            "keep_alive": KEEP_ALIVE,
            # A1/A6: qwen3.5 is a reasoning model and this is Ollama's /v1
            # shim. With reasoning on, the thinking block consumes the whole
            # budget and `content` comes back EMPTY at these token counts —
            # measured 0 chars at both max_tokens=400 and 800, and this
            # method defaults to 512. That silently emptied live tutoring and
            # grading responses, not just course building.
            # The /v1 shim ignores the native `think` field; `reasoning_effort`
            # is what it honors, and only "none" takes effect ("low" still
            # returned 0 chars). Also ~4x faster (8.8s vs 34.4s).
            # Pass think=True where deliberation is worth the latency.
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
        }
        # Constrained decoding: a JSON schema forces schema-valid output; plain
        # json_mode only nudges toward JSON. Prefer the schema when given.
        if json_schema is not None:
            # See llm_utils: `format` is the NATIVE field and is ignored by /v1.
            # Send both so one payload constrains on either endpoint.
            payload["format"] = json_schema
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "helga_schema", "schema": json_schema},
            }
        elif json_mode:
            payload["format"] = "json"

        for attempt in range(retries):
            try:
                # B27.5: breaker OPEN → fast-fail, no 60s hang / retry storm
                if not get_breaker().allow():
                    failure = record_failure_reason(
                        LLMCircuitOpen(f"retry in "
                                       f"{get_breaker().retry_after():.0f}s"))
                    logger.warning("Ollama breaker OPEN — fast-failing chat")
                    break
                # B23.1: bounded, fair GPU admission. Slot held only for the
                # actual generation; queue-wait overload degrades gracefully.
                try:
                    slot = get_gpu_gate().admit(ctx)
                except GpuOverloaded as e:
                    # Our own backpressure, not the host's health — named
                    # separately so it never reads as "Ollama is down", and
                    # never recorded against the breaker.
                    failure = record_failure_reason(LLMOverloaded(str(e)))
                    logger.warning(f"GPU overloaded, shedding request: {e}")
                    break
                _gen_start = time.monotonic()
                try:
                    resp = requests.post(
                        self.api_url,
                        json=payload,
                        timeout=timeout
                    )
                finally:
                    slot.release()
                resp.raise_for_status()
                get_breaker().record_success()
                # B27.4: per-student token / GPU-second usage
                try:
                    from usage_tracker import record as _record_usage
                except ImportError:
                    from services.core.usage_tracker import record as _record_usage
                try:
                    usage = resp.json().get("usage") or {}
                    _record_usage((ctx.student_id if ctx else None),
                                  usage.get("prompt_tokens", 0),
                                  usage.get("completion_tokens", 0),
                                  time.monotonic() - _gen_start)
                except Exception:
                    pass
                # Force UTF-8 — Ollama emits UTF-8 but some setups omit the
                # charset parameter, causing requests to fall back to latin-1
                # and produce mojibake on smart quotes / em dashes.
                resp.encoding = "utf-8"
                content = (resp.json()
                           .get("choices", [{}])[0]
                           .get("message", {})
                           .get("content", ""))

                # Strip <think>...</think> tags from reasoning models
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

                logger.debug(f"LLM response ({len(content)} chars)")
                if not content:
                    # Transport fine, model said nothing — the reasoning-block
                    # trap. Named, so it is never mistaken for an outage.
                    failure = record_failure_reason(
                        LLMEmptyResponse(f"tokens={max_tokens}, think={think}"))
                    logger.warning("Ollama returned empty content: %s", failure)
                    break
                clear_llm_failure()
                return content

            except requests.exceptions.ConnectionError as e:
                failure = record_failure_reason(LLMTransportError(str(e)[:200]))
                get_breaker().record_failure(failure)
                if not get_breaker().allow():
                    logger.error("Ollama breaker tripped mid-retry — aborting")
                    break
                wait = 2 ** attempt
                logger.warning(f"Ollama connection failed (attempt {attempt + 1}/{retries}), "
                               f"retrying in {wait}s")
                time.sleep(wait)
            except requests.exceptions.Timeout:
                failure = record_failure_reason(
                    LLMTimeout(f"no response in {timeout}s"))
                get_breaker().record_failure(failure)
                if not get_breaker().allow():
                    logger.error("Ollama breaker tripped mid-retry — aborting")
                    break
                wait = 2 ** attempt
                logger.warning(f"Ollama timeout after {timeout}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
            except requests.exceptions.HTTPError as e:
                # 4xx is the server refusing OUR payload and 5xx is the server
                # failing. Only the second is evidence the host is unhealthy, so
                # only the second counts toward the breaker — a bad schema must
                # not pause LLM calls for every other student.
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500:
                    failure = record_failure_reason(
                        LLMRequestRejected(f"HTTP {status}"))
                    logger.error(f"Ollama rejected the request: {e}")
                    break
                failure = record_failure_reason(
                    LLMTransportError(f"HTTP {status}" if status else str(e)[:200]))
                get_breaker().record_failure(failure)
                logger.error(f"Ollama HTTP error: {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    break
            except Exception as e:
                failure = record_failure_reason(LLMError(str(e)[:200]))
                logger.error(f"LLM client error: {e}")
                break

        if failure is None:
            failure = LLMTransportError(f"failed after {retries} attempts")
            logger.error(f"LLM request failed after {retries} attempts")
        record_failure_reason(failure)
        if strict:
            raise failure
        return ""

    def chat_json(self, system_prompt, user_message, max_tokens=512, ctx=None,
                  temperature=0.6, json_schema=None, timeout=60, retries=3,
                  strict=None):
        """Chat with JSON output. If json_schema is given, output is schema-
        constrained (Ollama >=0.5). Returns parsed dict/list or None.

        A7: `None` used to mean either "never reached the model" or "the model's
        JSON was garbage". Those are opposite problems, so the failure is now
        named — `LLMUnavailable` (or its subclasses) versus `LLMBadJSON` — and
        readable from `last_llm_failure()` even when None is still returned.
        """
        strict = strict_default() if strict is None else strict
        raw = self.chat(
            system_prompt, user_message,
            max_tokens=max_tokens, temperature=temperature,
            json_mode=True, json_schema=json_schema, timeout=timeout, retries=retries,
            ctx=ctx, strict=False,   # classified here so the name survives
        )
        if not raw:
            failure = last_llm_failure() or LLMEmptyResponse("no content")
            if strict:
                raise failure
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Reuse the shared repair ladder instead of this method's old one-line
        # regex. repair_json() already handles the malformations that actually
        # occur here (unescaped inner quotes, truncation, smart quotes); keeping
        # a weaker private copy meant core-service calls threw away responses
        # the build path would have recovered. Imported lazily: llm_utils
        # imports gpu_gate, and gpu_gate is mid-import when this module loads.
        try:
            from services.common.llm_utils import repair_json
            return json.loads(repair_json(raw))
        except Exception:
            pass
        failure = record_failure_reason(LLMBadJSON(f"{raw[:120]!r}"))
        logger.warning(f"Failed to parse JSON from LLM response: {raw[:200]}")
        if strict:
            raise failure
        return None

    def describe_image(self, image, instruction=None, max_tokens=400, timeout=90, ctx=None):
        """Vision helper (qwen3.5:9b): caption/analyse an image. `image` is a data
        URI or base64 string. Returns the model's description — for alt text,
        relevance filtering of scraped figures, or grounding a Socratic question
        about a diagram. Empty string on failure / non-vision model."""
        instruction = instruction or (
            "Describe this image for an educational context. State what it depicts, "
            "any labels or key features, and whether it is a diagram, photo, or chart."
        )
        return self.chat(
            "You are a precise visual analyst for a tutoring system.",
            instruction, images=[image], max_tokens=max_tokens, timeout=timeout,
            ctx=ctx,
        )

    def _raw_chat(self, messages, tools=None, temperature=0.4, timeout=60, ctx=None):
        """Single chat round returning the full assistant MESSAGE dict (so callers
        can see tool_calls). Used by chat_with_tools."""
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "stream": False,
                   "keep_alive": KEEP_ALIVE}
        if tools:
            payload["tools"] = tools
        # A7: the tool loop was the one LLM path with no breaker, so a dead host
        # still cost it max_rounds x timeout per turn. It raises rather than
        # returning a sentinel because chat_with_tools already treats an
        # exception as "end the loop with best-effort text".
        get_breaker().raise_if_open()
        try:
            with get_gpu_gate().admit(ctx):
                resp = requests.post(self.api_url, json=payload, timeout=timeout)
            resp.raise_for_status()
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            failure = record_failure_reason(LLMTransportError(str(e)[:200]))
            get_breaker().record_failure(failure)
            raise failure from e
        resp.encoding = "utf-8"
        get_breaker().record_success()
        return resp.json().get("choices", [{}])[0].get("message", {}) or {}

    def chat_with_tools(self, system_prompt, user_message, registry,
                        max_rounds=3, max_tool_calls=3, temperature=0.4, timeout=60,
                        ctx=None):
        """Agentic tool-use loop (B14). The model may call tools from `registry`
        (tier-gated for this model — the registry enforces that and the output
        caps); each call is executed with failsafes and fed back. Loops until the
        model stops calling tools or `max_rounds` is hit (both scaled to the model
        tier by the caller). Returns {"text": final, "tool_calls": [...]}. Never
        raises — a tool/transport failure ends the loop with best-effort text."""
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}]
        tools = registry.to_ollama_tools(self.model)
        used = []
        last_text = ""
        for _ in range(max(1, max_rounds)):
            try:
                msg = self._raw_chat(messages, tools=tools, temperature=temperature, timeout=timeout, ctx=ctx)
            except Exception as e:
                logger.warning(f"tool chat round failed: {e}")
                break
            content = re.sub(r'<think>.*?</think>', '', msg.get("content", "") or "", flags=re.DOTALL).strip()
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return {"text": content, "tool_calls": used}
            last_text = content
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc in tool_calls[:max(1, max_tool_calls)]:
                fn = tc.get("function", {}) or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                res = registry.execute(self.model, name, args)
                used.append({"name": name, "args": args, "result": res})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", name),
                    "content": res.get("result") if res.get("ok") else ("ERROR: " + str(res.get("error", ""))),
                })
        return {"text": last_text, "tool_calls": used}

    def chat_stream(self, system_prompt, user_message, max_tokens=512,
                    temperature=0.6, timeout=120, ctx=None, think=False):
        """Stream a chat completion. Yields text chunks.

        Args:
            system_prompt: System role message
            user_message: User role message
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            timeout: Request timeout in seconds

        Yields:
            Text chunks as they arrive
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "keep_alive": KEEP_ALIVE,      # see chat()
            # See chat(): reasoning must be off or the thinking block eats the
            # budget and the stream yields nothing usable. Streaming makes this
            # worse — the user watches an empty response arrive.
            **({} if think else {"reasoning_effort": "none"}),
        }

        clear_llm_failure()
        if not get_breaker().allow():
            record_failure_reason(
                LLMCircuitOpen(f"retry in {get_breaker().retry_after():.0f}s"))
            logger.warning("Ollama breaker OPEN — fast-failing stream")
            return
        try:
            slot = get_gpu_gate().admit(ctx)
        except GpuOverloaded as e:
            record_failure_reason(LLMOverloaded(str(e)))
            logger.warning(f"GPU overloaded, shedding stream request: {e}")
            return

        try:
            resp = requests.post(
                self.api_url,
                json=payload,
                timeout=timeout,
                stream=True
            )
            resp.raise_for_status()
            # Force UTF-8 so multi-byte characters (smart quotes, em dashes)
            # don't get split and misdecoded mid-chunk.
            resp.encoding = "utf-8"

            in_think_block = False
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        # Filter out <think> blocks from streaming
                        if '<think>' in content:
                            in_think_block = True
                            content = content.split('<think>')[0]
                            if content:
                                yield content
                            continue
                        if '</think>' in content:
                            in_think_block = False
                            content = content.split('</think>')[-1]
                            if content:
                                yield content
                            continue
                        if not in_think_block:
                            yield content
                except json.JSONDecodeError:
                    continue

            get_breaker().record_success()
        except requests.exceptions.ConnectionError as e:
            failure = record_failure_reason(LLMTransportError(str(e)[:200]))
            get_breaker().record_failure(failure)
            logger.error(f"Streaming connection error: {e}")
        except requests.exceptions.Timeout as e:
            failure = record_failure_reason(LLMTimeout(f"no response in {timeout}s"))
            get_breaker().record_failure(failure)
            logger.error(f"Streaming timeout: {e}")
        except Exception as e:
            record_failure_reason(LLMError(str(e)[:200]))
            logger.error(f"Streaming error: {e}")
        finally:
            slot.release()

    def residency(self):
        """Is the model actually RESIDENT, and for how much longer?

        `health_check` reads /api/tags, which lists models that are INSTALLED —
        it says nothing about whether the weights are in memory. The difference
        is a cold load on the next request, which for a 9B is several seconds
        and is paid by whoever is waiting: a student mid-lesson.

        Ollama unloads after five idle minutes by default, and a student
        reading a question and thinking about it routinely exceeds that. So
        every answer after a pause pays the load, on top of the generation.

        /api/ps is the endpoint that knows: it lists loaded models with an
        `expires_at`. A far-future (or absent) expiry means keep-alive is
        pinning the model; a few minutes out means it is not.

        Returns {"loaded": bool, "expires_at": str|None, "pinned": bool|None}
        or {"error": ...}. Never raises — this is diagnostics.
        """
        try:
            resp = requests.get(f"{self.base_url}/api/ps", timeout=5)
            if not resp.ok:
                return {"error": f"HTTP {resp.status_code}"}
            for entry in resp.json().get("models", []):
                if self.model.split(":")[0] not in entry.get("name", ""):
                    continue
                expires = entry.get("expires_at")
                pinned = None
                remaining = None
                if expires:
                    try:
                        from datetime import datetime, timezone
                        when = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                        remaining = (when - datetime.now(timezone.utc)).total_seconds()
                        # Ollama writes a year-out expiry for keep_alive=-1.
                        pinned = remaining > 3600
                    except (ValueError, TypeError):
                        pinned = None
                # expires_in_s is what the too-short-window warning reads; it
                # was computed here and thrown away, which made that warning
                # structurally impossible to fire.
                return {"loaded": True, "expires_at": expires,
                        "expires_in_s": remaining, "pinned": pinned}
            return {"loaded": False, "expires_at": None,
                    "expires_in_s": None, "pinned": None}
        except Exception as e:
            return {"error": str(e)}

    def warn_residency(self):
        """Log the exact fix when the model will idle out from under us."""
        state = self.residency()
        if state.get("error"):
            return state
        # The A6 policy is an idle WINDOW, not a pin. Both extremes are now
        # wrong ways: pinned means ~12.7 GB held through every idle hour on a
        # 24 GB machine with a measured ~15.0 GB safe ceiling, and a window
        # under ~10 minutes means a student who stops to think pays a
        # ~2-minute reload mid-lesson. Warn on either; say which.
        if state.get("pinned") is True:
            logger.warning(
                "Ollama is PINNING %s in memory (expires_at=%s). That holds "
                "the weights through every idle hour and starves the night "
                "audit and host TTS/STT. Fix on the HOST: "
                "`launchctl setenv OLLAMA_KEEP_ALIVE 30m` then restart "
                "`ollama serve` — and check no container overrides it, since "
                "a per-request keep_alive wins over the server setting.",
                self.model, state.get("expires_at"))
        elif state.get("loaded") and state.get("expires_in_s") is not None                 and state["expires_in_s"] < 600:
            logger.warning(
                "Ollama keep-alive for %s is under 10 minutes (%.0fs). A "
                "student who pauses to think will pay a ~2-minute cold load "
                "mid-lesson. Raise OLLAMA_KEEP_ALIVE toward 30m.",
                self.model, state["expires_in_s"])
        return state

    # The old name asserted the old policy in its very grammar. Kept callable
    # so callers keep working whatever order changes land in.
    def warn_if_not_pinned(self):
        return self.warn_residency()

    def health_check(self):
        """Check if Ollama is reachable and model is loaded."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.ok:
                models = resp.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                return {
                    "status": "healthy",
                    "url": self.base_url,
                    "model": self.model,
                    "model_loaded": any(self.model in n for n in model_names),
                    "available_models": model_names
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "url": self.base_url,
                "error": str(e)
            }


# Module-level singleton
_client = None


def get_llm_client():
    """Get or create the module-level LLM client singleton."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
