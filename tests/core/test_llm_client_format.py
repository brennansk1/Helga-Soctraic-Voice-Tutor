"""Tests for LLMClient constrained-JSON / format payload handling (Task #2)."""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/core'))
from llm_client import LLMClient


def _mock_resp(content):
    m = MagicMock()
    m.raise_for_status = lambda: None
    m.json = lambda: {"choices": [{"message": {"content": content}}]}
    m.encoding = "utf-8"
    return m


def _sent_payload(mock_post):
    # requests.post(api_url, json=payload, timeout=...)
    return mock_post.call_args.kwargs["json"]


def test_chat_passes_json_schema_as_format():
    schema = {"type": "object", "properties": {"grade": {"type": "integer"}}}
    with patch("llm_client.requests.post", return_value=_mock_resp('{"grade": 3}')) as p:
        LLMClient(model="m").chat("sys", "user", json_schema=schema)
        assert _sent_payload(p)["format"] == schema


def test_schema_takes_precedence_over_json_mode():
    schema = {"type": "object"}
    with patch("llm_client.requests.post", return_value=_mock_resp("{}")) as p:
        LLMClient(model="m").chat("sys", "user", json_mode=True, json_schema=schema)
        assert _sent_payload(p)["format"] == schema


def test_json_mode_without_schema_is_string():
    with patch("llm_client.requests.post", return_value=_mock_resp("{}")) as p:
        LLMClient(model="m").chat("sys", "user", json_mode=True)
        assert _sent_payload(p)["format"] == "json"


def test_plain_chat_has_no_format():
    with patch("llm_client.requests.post", return_value=_mock_resp("hi")) as p:
        LLMClient(model="m").chat("sys", "user")
        assert "format" not in _sent_payload(p)
