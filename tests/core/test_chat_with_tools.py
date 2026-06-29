"""Tests for the LLMClient agentic tool-use loop (B14 / Task #14)."""
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/core'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/common'))

from llm_client import LLMClient
from tutor_tools import ToolRegistry, Tool


def _registry():
    r = ToolRegistry()
    r.register(Tool("add_one", "add 1 to n",
                    {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
                    lambda n: {"sum": n + 1}))
    return r


def test_executes_tool_then_returns_final_answer():
    client = LLMClient(model="qwen3.5:9b")
    rounds = [
        {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "add_one", "arguments": '{"n": 4}'}}]},
        {"content": "The result is 5.", "tool_calls": []},
    ]
    with patch.object(client, "_raw_chat", side_effect=rounds):
        out = client.chat_with_tools("sys", "use the tool", _registry(), max_rounds=3, max_tool_calls=3)
    assert out["text"] == "The result is 5."
    assert len(out["tool_calls"]) == 1
    call = out["tool_calls"][0]
    assert call["name"] == "add_one"
    assert call["result"]["ok"] and '"sum": 5' in call["result"]["result"]


def test_no_tool_calls_returns_immediately():
    client = LLMClient(model="qwen3.5:9b")
    with patch.object(client, "_raw_chat", return_value={"content": "hello", "tool_calls": []}):
        out = client.chat_with_tools("s", "u", _registry())
    assert out["text"] == "hello"
    assert out["tool_calls"] == []


def test_loop_is_bounded_by_max_rounds():
    client = LLMClient(model="qwen3.5:9b")
    always = {"content": "", "tool_calls": [{"id": "c", "function": {"name": "add_one", "arguments": '{"n": 1}'}}]}
    with patch.object(client, "_raw_chat", return_value=always) as raw:
        out = client.chat_with_tools("s", "u", _registry(), max_rounds=2, max_tool_calls=1)
    assert raw.call_count == 2            # never infinite-loops
    assert len(out["tool_calls"]) == 2


def test_bad_tool_arguments_do_not_crash():
    client = LLMClient(model="qwen3.5:9b")
    rounds = [
        {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "add_one", "arguments": "NOT JSON"}}]},
        {"content": "done", "tool_calls": []},
    ]
    with patch.object(client, "_raw_chat", side_effect=rounds):
        out = client.chat_with_tools("s", "u", _registry())
    # malformed args -> empty dict -> validation error, but the loop survives
    assert out["text"] == "done"
    assert out["tool_calls"][0]["result"]["ok"] is False
