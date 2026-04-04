import pytest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../services/common')))

from prompts import (
    get_examiner_question_prompt,
    get_examiner_grade_prompt,
    get_micro_lecture_prompt,
    get_socratic_tutor_prompt,
    get_socratic_grading_prompt,
    get_typed_socratic_prompt,
    get_bridge_prompt,
    get_hint_prompt,
)


def test_get_examiner_question_prompt():
    result = get_examiner_question_prompt("Context about logic gates")
    assert isinstance(result, list)
    assert any("logic gates" in m.get("content", "") for m in result)


def test_get_examiner_grade_prompt():
    result = get_examiner_grade_prompt("What is X?", "X is Y", "Context text")
    assert isinstance(result, list)
    contents = " ".join(m.get("content", "") for m in result)
    assert "What is X?" in contents
    assert "X is Y" in contents


def test_get_socratic_tutor_prompt():
    result = get_socratic_tutor_prompt("Lesson Context", [("Hi", "Hello")])
    assert isinstance(result, list)
    contents = " ".join(m.get("content", "") for m in result)
    assert "Lesson Context" in contents


def test_get_socratic_tutor_prompt_with_bloom():
    result = get_socratic_tutor_prompt("Context", [], bloom_level=3)
    contents = " ".join(m.get("content", "") for m in result)
    assert "Bloom" in contents or "bloom" in contents or "Apply" in contents.lower() or "cognitive" in contents.lower()


def test_get_micro_lecture_prompt():
    result = get_micro_lecture_prompt("Complex Topic", "Deep context")
    assert isinstance(result, list)
    contents = " ".join(m.get("content", "") for m in result)
    assert "Complex Topic" in contents


def test_get_socratic_grading_prompt():
    result = get_socratic_grading_prompt("Photosynthesis", "What is it?", "It converts sunlight")
    assert isinstance(result, list)
    contents = " ".join(m.get("content", "") for m in result)
    assert "Photosynthesis" in contents


def test_get_typed_socratic_prompt():
    result = get_typed_socratic_prompt(
        "clarification", "Context text about science", [("Hello", "Hi there")],
        misconceptions=[], analogies=[]
    )
    assert isinstance(result, list)
    contents = " ".join(m.get("content", "") for m in result)
    assert "clarif" in contents.lower() or "Context text" in contents


def test_get_bridge_prompt():
    result = get_bridge_prompt("Previous Concept", "Next Concept")
    assert isinstance(result, list)
    contents = " ".join(m.get("content", "") for m in result)
    assert "Previous Concept" in contents
    assert "Next Concept" in contents


def test_get_hint_prompt():
    result = get_hint_prompt("What is gravity?", "Context about physics", 1)
    assert isinstance(result, list)
    contents = " ".join(m.get("content", "") for m in result)
    assert "gravity" in contents.lower()


def test_no_llama_tokens():
    """Verify no Llama-2 format tokens in any prompt."""
    for func, args in [
        (get_examiner_question_prompt, ("ctx",)),
        (get_examiner_grade_prompt, ("q", "a", "ctx")),
        (get_micro_lecture_prompt, ("topic", "ctx")),
        (get_socratic_tutor_prompt, ("ctx", [])),
    ]:
        result = func(*args)
        text = str(result)
        assert "<|begin_of_text|>" not in text
        assert "<|start_header_id|>" not in text
        assert "</s>" not in text


def test_prompts_return_message_list():
    """All prompt functions return list of message dicts with role and content."""
    for func, args in [
        (get_examiner_question_prompt, ("ctx",)),
        (get_examiner_grade_prompt, ("q", "a", "ctx")),
        (get_micro_lecture_prompt, ("topic", "ctx")),
        (get_socratic_tutor_prompt, ("ctx", [])),
    ]:
        result = func(*args)
        assert isinstance(result, list), f"{func.__name__} should return list"
        for msg in result:
            assert "role" in msg, f"{func.__name__} messages need 'role'"
            assert "content" in msg, f"{func.__name__} messages need 'content'"
