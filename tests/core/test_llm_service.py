#!/usr/bin/env python3
"""
LLM Functional Test Script

This script sends a simple test prompt to the running LLM inference service
to verify that it is functional and generating text as expected.
"""

import requests
import logging
import sys
import json
import os

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# --- Configuration ---
# A3: this test used to hardcode `llama3.1:8b` and a Llama-3 chat template, so
# it failed permanently against the actual stack (Ollama + Qwen). Read the same
# env var the services read, and let Ollama apply the model's own template.
LLM_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
COMPLETION_ENDPOINT = f"{LLM_URL}/api/generate"
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")

TEST_PROMPT = "Hello! Who are you?"

def run_llm_test():
    """
    Sends a test prompt to the LLM and validates the response.
    """
    logger.info(f"--- Running LLM Functional Test ---")
    logger.info(f"Targeting LLM service at: {COMPLETION_ENDPOINT}")

    payload = {
        "model": MODEL,
        "prompt": TEST_PROMPT,
        "stream": False,
        # qwen3.5 is a reasoning model: with thinking on, a small num_predict
        # is consumed entirely by the thinking block and `response` comes back
        # empty. This is the NATIVE endpoint, which honors `think` (the /v1
        # shim used by the services ignores it and needs reasoning_effort
        # instead — see llm_utils.py / llm_client.py).
        "think": False,
        "options": {
            "num_predict": 128,
            "temperature": 0.3
        }
    }

    try:
        # 1. Send the request
        logger.info("Sending test prompt to the LLM...")
        response = requests.post(COMPLETION_ENDPOINT, json=payload, timeout=60)
        
        # 2. Check the HTTP status code
        if response.status_code == 200:
            logger.info("LLM service returned HTTP 200 OK.")
        else:
            logger.error(f"LLM service returned a non-200 status code: {response.status_code}")
            logger.error(f"Response body: {response.text}")
            return False

        # 3. Validate the response content
        data = response.json()
        completion_text = data.get('response', '').strip()
        
        if completion_text:
            logger.info("Test Passed: LLM generated a response.")
            logger.info(f"LLM Response: '{completion_text[:100]}...'")
            return True
        else:
            logger.error("Test Failed: LLM response was empty.")
            logger.error(f"Full response JSON: {data}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Test Failed: Could not connect to the LLM service at {LLM_URL}.")
        logger.error(f"Error: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        return False

def _ollama_reachable():
    try:
        return requests.get(f"{LLM_URL}/api/tags", timeout=3).status_code == 200
    except requests.exceptions.RequestException:
        return False


def test_llm_functional():
    """Pytest wrapper for the LLM functional test.

    Skips when Ollama simply isn't running (a genuinely environmental
    condition), but FAILS when Ollama is up and the configured model still
    doesn't answer. That distinction matters: this test previously requested a
    hardcoded `llama3.1:8b` and its permanent red was written off as
    environmental, which is exactly how a real model-config defect hides.
    """
    import pytest
    if not _ollama_reachable():
        pytest.skip(f"Ollama not reachable at {LLM_URL} — start it to run this test")
    assert run_llm_test(), (
        f"Ollama is up but model '{MODEL}' did not produce a response. "
        f"Check OLLAMA_MODEL and that the model is pulled (`ollama list`)."
    )

if __name__ == "__main__":
    if run_llm_test():
        logger.info("--- LLM Test Successful ---")
        sys.exit(0)
    else:
        logger.error("--- LLM Test Failed ---")
        sys.exit(1)
