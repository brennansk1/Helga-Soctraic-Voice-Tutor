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

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://host.docker.internal:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen3:14b')


class LLMClient:
    """Client for Ollama's OpenAI-compatible chat API."""

    def __init__(self, base_url=None, model=None):
        self.base_url = base_url or OLLAMA_URL
        self.model = model or OLLAMA_MODEL
        self.api_url = f"{self.base_url}/v1/chat/completions"

    def chat(self, system_prompt, user_message, max_tokens=512,
             temperature=0.6, json_mode=False, timeout=60, retries=3):
        """Send a chat completion request to Ollama.

        Args:
            system_prompt: System role message
            user_message: User role message
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            json_mode: If True, request JSON format output
            timeout: Request timeout in seconds
            retries: Number of retry attempts

        Returns:
            Generated text content, or empty string on failure
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False
        }
        if json_mode:
            payload["format"] = "json"

        for attempt in range(retries):
            try:
                resp = requests.post(
                    self.api_url,
                    json=payload,
                    timeout=timeout
                )
                resp.raise_for_status()
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
                wait = 2 ** attempt
                logger.warning(f"Ollama connection failed (attempt {attempt + 1}/{retries}), "
                               f"retrying in {wait}s")
                time.sleep(wait)
            except requests.exceptions.Timeout:
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

    def chat_json(self, system_prompt, user_message, max_tokens=512,
                  temperature=0.6, timeout=60, retries=3):
        """Chat with JSON mode enabled. Returns parsed dict/list or None."""
        raw = self.chat(
            system_prompt, user_message,
            max_tokens=max_tokens, temperature=temperature,
            json_mode=True, timeout=timeout, retries=retries
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

    def chat_stream(self, system_prompt, user_message, max_tokens=512,
                    temperature=0.6, timeout=120):
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

        except Exception as e:
            logger.error(f"Streaming error: {e}")

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
