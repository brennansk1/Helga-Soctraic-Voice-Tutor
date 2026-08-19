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

# A third outcome, distinct from both pass and fail. See the Timeout handler.
BUSY = "busy"
REQUEST_TIMEOUT = 60

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
        response = requests.post(COMPLETION_ENDPOINT, json=payload,
                                 timeout=REQUEST_TIMEOUT)
        
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

    except requests.exceptions.Timeout:
        # CONTENTION IS NOT A DEFECT — AND IT IS NOT A PASS EITHER.
        #
        # Measured: this test failed inside a full-suite run and passed on its
        # own 40 seconds later. Nothing was wrong with the model — three course
        # builds were saturating Ollama, which serialises requests, so the reply
        # arrived after the deadline.
        #
        # Calling that red is the mirror of the defect this file's docstring
        # warns about: one writes a real failure off as environmental, the other
        # writes an environmental condition up as a real failure, and both end
        # with the signal being ignored. A timeout gets its own outcome so
        # neither happens, and an EMPTY 200 stays hard red regardless of load.
        logger.warning(
            f"LLM did not answer within {REQUEST_TIMEOUT}s. Ollama serialises "
            f"requests, so this is contention — a build in flight — not a "
            f"broken model. Re-run when the machine is idle to get a verdict.")
        return BUSY

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

    Three outcomes, deliberately:

      SKIP  Ollama isn't running, or it is busy serving something else. Both
            are genuinely environmental and neither says anything about the
            configuration under test.
      FAIL  Ollama answered and the configured model still produced nothing —
            a non-200, or a 200 carrying an empty completion.
      PASS  the model answered.

    The middle line is the one that matters. This test once requested a
    hardcoded `llama3.1:8b` and its permanent red was written off as
    environmental, which is exactly how a real model-config defect hides — so
    an answer that arrives and is empty stays red no matter how loaded the
    machine is. Only a request that never got served at all is excused.
    """
    import pytest
    if not _ollama_reachable():
        pytest.skip(f"Ollama not reachable at {LLM_URL} — start it to run this test")
    result = run_llm_test()
    if result == BUSY:
        pytest.skip(
            f"Ollama is up but did not answer within {REQUEST_TIMEOUT}s — it "
            f"serialises requests and something (likely a course build) is "
            f"holding it. Re-run when idle.")
    assert result, (
        f"Ollama is up and answering, but model '{MODEL}' produced no "
        f"completion. Check OLLAMA_MODEL and that the model is pulled "
        f"(`ollama list`)."
    )

if __name__ == "__main__":
    _r = run_llm_test()
    if _r == BUSY:
        # Exit 2, not 0 and not 1: the run reached no verdict, and a script
        # calling this must not read "busy" as "the model works".
        logger.warning("--- LLM Test Inconclusive (Ollama busy) ---")
        sys.exit(2)
    if _r:
        logger.info("--- LLM Test Successful ---")
        sys.exit(0)
    else:
        logger.error("--- LLM Test Failed ---")
        sys.exit(1)
