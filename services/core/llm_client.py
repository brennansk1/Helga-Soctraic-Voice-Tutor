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

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://host.docker.internal:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen3.5:9b')


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
             timeout=60, retries=3, ctx=None):
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

        Returns:
            Generated text content, or empty string on failure
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._user_content(user_message, images)}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False
        }
        # Constrained decoding: a JSON schema forces schema-valid output; plain
        # json_mode only nudges toward JSON. Prefer the schema when given.
        if json_schema is not None:
            payload["format"] = json_schema
        elif json_mode:
            payload["format"] = "json"

        for attempt in range(retries):
            try:
                # B27.5: breaker OPEN → fast-fail, no 60s hang / retry storm
                if not get_breaker().allow():
                    logger.warning("Ollama breaker OPEN — fast-failing chat")
                    return ""
                # B23.1: bounded, fair GPU admission. Slot held only for the
                # actual generation; queue-wait overload degrades gracefully.
                try:
                    slot = get_gpu_gate().admit(ctx)
                except GpuOverloaded as e:
                    logger.warning(f"GPU overloaded, shedding request: {e}")
                    return ""
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
                return content

            except requests.exceptions.ConnectionError:
                get_breaker().record_failure()
                if not get_breaker().allow():
                    logger.error("Ollama breaker tripped mid-retry — aborting")
                    return ""
                wait = 2 ** attempt
                logger.warning(f"Ollama connection failed (attempt {attempt + 1}/{retries}), "
                               f"retrying in {wait}s")
                time.sleep(wait)
            except requests.exceptions.Timeout:
                get_breaker().record_failure()
                wait = 2 ** attempt
                logger.warning(f"Ollama timeout after {timeout}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
            except requests.exceptions.HTTPError as e:
                logger.error(f"Ollama HTTP error: {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    return ""
            except Exception as e:
                logger.error(f"LLM client error: {e}")
                return ""

        logger.error(f"LLM request failed after {retries} attempts")
        return ""

    def chat_json(self, system_prompt, user_message, max_tokens=512, ctx=None,
                  temperature=0.6, json_schema=None, timeout=60, retries=3):
        """Chat with JSON output. If json_schema is given, output is schema-
        constrained (Ollama >=0.5). Returns parsed dict/list or None."""
        raw = self.chat(
            system_prompt, user_message,
            max_tokens=max_tokens, temperature=temperature,
            json_mode=True, json_schema=json_schema, timeout=timeout, retries=retries,
            ctx=ctx,
        )
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            match = re.search(r'[\[{].*[\]}]', raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            logger.warning(f"Failed to parse JSON from LLM response: {raw[:200]}")
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
                   "temperature": temperature, "stream": False}
        if tools:
            payload["tools"] = tools
        with get_gpu_gate().admit(ctx):
            resp = requests.post(self.api_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = "utf-8"
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
                    temperature=0.6, timeout=120, ctx=None):
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
            "stream": True
        }

        if not get_breaker().allow():
            logger.warning("Ollama breaker OPEN — fast-failing stream")
            return
        try:
            slot = get_gpu_gate().admit(ctx)
        except GpuOverloaded as e:
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
            get_breaker().record_failure()
            logger.error(f"Streaming connection error: {e}")
        except Exception as e:
            logger.error(f"Streaming error: {e}")
        finally:
            slot.release()

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
