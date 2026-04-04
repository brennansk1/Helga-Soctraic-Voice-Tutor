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

LLM_API_URL = os.getenv(
    "LLM_API_URL", "http://host.docker.internal:11434/v1/chat/completions"
)


def repair_json(text: str) -> str:
    """LLM-1: Repair common JSON malformations from LLM output.

    Fixes:
    - Trailing commas before ] or }
    - Single quotes -> double quotes
    - Python literals (True/False/None) -> JSON equivalents
    - Unquoted keys
    """
    if not text:
        return text

    # Replace Python literals with JSON equivalents
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)

    # Remove trailing commas before ] or }
    text = re.sub(r",\s*([\]\}])", r"\1", text)

    # Replace single quotes with double quotes (careful with apostrophes)
    # Only do this if the text doesn't already parse as valid JSON
    try:
        json.loads(text)
        return text  # Already valid
    except (json.JSONDecodeError, ValueError):
        pass

    # Smart single-quote replacement: convert 'key': 'value' patterns
    text = re.sub(r"(?<=[\[{,\s])'([^']*?)'\s*:", r'"\1":', text)
    text = re.sub(r":\s*'([^']*?)'", r': "\1"', text)

    # Truncated JSON repair: close unclosed brackets/braces
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    if open_braces > 0 or open_brackets > 0:
        # Strip trailing comma before closing
        text = text.rstrip().rstrip(",")
        text += "}" * max(0, open_braces)
        text += "]" * max(0, open_brackets)

    return text


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
) -> str:
    """Call LLM with retry logic.

    Llama 3.1 8B Instruct adaptations:
    - Standard temperature (0.7) with slight increase on retry
    - Generous timeouts scaled to max_tokens
    - Heartbeat thread for progress feedback during long calls
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
            temp = 0.7 + (
                attempt * 0.1
            )  # Standard temperature, slight increase on retry

            data = {
                "model": os.getenv("LLM_MODEL", "qwen2.5:14b"),
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temp,
            }
            logger.info(
                f"[{req_id}] LLM Call (tokens:{max_tokens}, temp:{temp:.1f}): sys='{sys_prompt[:60]}...'"
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

            resp = requests.post(LLM_API_URL, json=data, timeout=timeout)
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
        schema: Optional schema dict for validation (LLM-2). See validate_schema().
        progress_callback: Optional callback for heartbeat updates during LLM calls.
    """
    for attempt in range(retries):
        raw = llm_generate(
            prompt,
            sys_prompt=sys_prompt,
            retries=1,
            max_tokens=max_tokens,
            progress_callback=progress_callback,
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
