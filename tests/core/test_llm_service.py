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
LLM_URL = "http://localhost:11434"
COMPLETION_ENDPOINT = f"{LLM_URL}/api/generate"

TEST_PROMPT = "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\nHello! Who are you?<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n"

def run_llm_test():
    """
    Sends a test prompt to the LLM and validates the response.
    """
    logger.info(f"--- Running LLM Functional Test ---")
    logger.info(f"Targeting LLM service at: {COMPLETION_ENDPOINT}")

    payload = {
        "model": "llama3.1:8b",
        "prompt": TEST_PROMPT,
        "stream": False,
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

def test_llm_functional():
    """
    Pytest wrapper for LLM functional test.
    """
    assert run_llm_test(), "LLM test failed"

if __name__ == "__main__":
    if run_llm_test():
        logger.info("--- LLM Test Successful ---")
        sys.exit(0)
    else:
        logger.error("--- LLM Test Failed ---")
        sys.exit(1)
