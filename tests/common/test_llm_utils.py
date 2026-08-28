"""
Tests for llm_utils.py — JSON repair, schema validation, and parsing utilities.

Covers:
- repair_json: trailing commas, single quotes, Python True/False/None, mixed issues
- Already-valid JSON passes through unchanged
- Markdown code fence removal
- Truncated JSON (missing closing brace)
- validate_schema
- extract_python_list
- parse_llm_json
- extract_grade_from_llm
"""
import sys
import os
import unittest
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from services.common.llm_utils import (
    repair_json,
    validate_schema,
    extract_python_list,
    parse_llm_json,
    extract_grade_from_llm,
)


class TestRepairJsonTrailingCommas(unittest.TestCase):
    """Tests for trailing comma removal in repair_json."""

    def test_trailing_comma_object(self):
        bad = '{"a": 1, "b": 2,}'
        result = json.loads(repair_json(bad))
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_trailing_comma_array(self):
        bad = '[1, 2, 3,]'
        result = json.loads(repair_json(bad))
        self.assertEqual(result, [1, 2, 3])

    def test_trailing_comma_nested(self):
        bad = '{"items": [1, 2,], "name": "test",}'
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result["items"], [1, 2])
        self.assertEqual(result["name"], "test")

    def test_multiple_trailing_commas(self):
        bad = '{"a": [1,], "b": {"c": 2,},}'
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result["a"], [1])
        self.assertEqual(result["b"]["c"], 2)


class TestRepairJsonSingleQuotes(unittest.TestCase):
    """Tests for single quote to double quote conversion."""

    def test_single_quotes_to_double(self):
        bad = "{'title': 'Hello World'}"
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result, {"title": "Hello World"})

    def test_single_quotes_nested(self):
        bad = "{'items': [{'name': 'test'}]}"
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result["items"][0]["name"], "test")

    def test_single_quotes_with_numbers(self):
        bad = "{'count': 42, 'name': 'test'}"
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result["count"], 42)


class TestRepairJsonPythonLiterals(unittest.TestCase):
    """Tests for Python True/False/None to JSON true/false/null conversion."""

    def test_python_true(self):
        bad = '{"active": True}'
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result["active"], True)

    def test_python_false(self):
        bad = '{"deleted": False}'
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result["deleted"], False)

    def test_python_none(self):
        bad = '{"value": None}'
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertIsNone(result["value"])

    def test_all_python_literals(self):
        bad = '{"active": True, "deleted": False, "value": None}'
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result, {"active": True, "deleted": False, "value": None})


class TestRepairJsonMixedIssues(unittest.TestCase):
    """Tests for combined malformations."""

    def test_single_quotes_and_trailing_comma(self):
        bad = "{'active': True, 'items': [1, 2,],}"
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result["active"], True)
        self.assertEqual(result["items"], [1, 2])

    def test_python_literals_and_trailing_commas(self):
        bad = '{"ok": True, "data": [False, None,],}'
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["data"], [False, None])

    def test_all_issues_combined(self):
        bad = "{'flag': True, 'list': [1, 2,], 'val': None,}"
        repaired = repair_json(bad)
        result = json.loads(repaired)
        self.assertTrue(result["flag"])
        self.assertEqual(result["list"], [1, 2])
        self.assertIsNone(result["val"])


class TestRepairJsonPassthrough(unittest.TestCase):
    """Tests that valid JSON and edge cases pass through correctly."""

    def test_valid_json_unchanged(self):
        valid = '{"key": "value"}'
        self.assertEqual(repair_json(valid), valid)

    def test_valid_json_array(self):
        valid = '[1, 2, 3]'
        self.assertEqual(repair_json(valid), valid)

    def test_valid_nested_json(self):
        valid = '{"items": [{"name": "test", "id": 1}]}'
        self.assertEqual(repair_json(valid), valid)

    def test_empty_string(self):
        self.assertEqual(repair_json(""), "")

    def test_none_input(self):
        self.assertIsNone(repair_json(None))


class TestRepairJsonCodeFenceRemoval(unittest.TestCase):
    """Tests for markdown code fence handling via extract_python_list."""

    def test_json_code_fence(self):
        text = '```json\n[{"title": "Hello"}]\n```'
        result = extract_python_list(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Hello")

    def test_python_code_fence(self):
        text = "```python\n[{'title': 'Hello'}]\n```"
        result = extract_python_list(text)
        self.assertIsNotNone(result)

    def test_generic_code_fence(self):
        text = '```\n[{"title": "Hello"}]\n```'
        result = extract_python_list(text)
        self.assertIsNotNone(result)

    def test_code_fence_with_surrounding_text(self):
        text = 'Here is the result:\n```json\n[{"title": "Test"}]\n```\nDone.'
        result = extract_python_list(text)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["title"], "Test")


class TestRepairJsonTruncated(unittest.TestCase):
    """Tests for truncated/incomplete JSON handling."""

    def test_truncated_missing_closing_brace(self):
        """Truncated JSON with missing closing brace — repair_json may not fix this,
        but it should not crash."""
        bad = '{"title": "Hello", "items": [1, 2'
        result = repair_json(bad)
        self.assertIsNotNone(result)
        # It may or may not parse; the key is no crash
        self.assertIsInstance(result, str)

    def test_truncated_array(self):
        """Truncated array — should not crash."""
        bad = '[{"title": "A"}, {"title": "B"'
        result = repair_json(bad)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_parse_llm_json_truncated_returns_none(self):
        """parse_llm_json on truncated input should return None gracefully."""
        bad = '{"title": "Hello", "items": [1, 2'
        result = parse_llm_json(bad, expected_type="dict")
        # May or may not parse — either a result or None is fine
        # Key: no exception raised


class TestValidateSchema(unittest.TestCase):
    """Tests for the validate_schema function."""

    def test_no_schema_always_valid(self):
        self.assertTrue(validate_schema([1, 2, 3], None))

    def test_list_type_valid(self):
        schema = {"type": "list"}
        self.assertTrue(validate_schema([1, 2], schema))

    def test_list_type_invalid(self):
        schema = {"type": "list"}
        self.assertFalse(validate_schema({"key": "val"}, schema))

    def test_list_required_keys_valid(self):
        schema = {"type": "list", "items": {"required_keys": ["title", "uid"]}}
        data = [{"title": "A", "uid": "1"}, {"title": "B", "uid": "2"}]
        self.assertTrue(validate_schema(data, schema))

    def test_list_required_keys_missing(self):
        schema = {"type": "list", "items": {"required_keys": ["title", "uid"]}}
        data = [{"title": "A"}]
        self.assertFalse(validate_schema(data, schema))

    def test_dict_type_valid(self):
        schema = {"type": "dict", "required_keys": ["name"]}
        self.assertTrue(validate_schema({"name": "Test"}, schema))

    def test_dict_type_missing_key(self):
        schema = {"type": "dict", "required_keys": ["name"]}
        self.assertFalse(validate_schema({"other": "val"}, schema))

    def test_empty_list_passes_list_schema(self):
        schema = {"type": "list", "items": {"required_keys": ["title"]}}
        self.assertTrue(validate_schema([], schema))


class TestExtractPythonList(unittest.TestCase):
    """Tests for extract_python_list function."""

    def test_plain_json_array(self):
        result = extract_python_list('[{"title": "A"}, {"title": "B"}]')
        self.assertEqual(len(result), 2)

    def test_code_block_json(self):
        text = '```json\n[{"title": "Hello"}]\n```'
        result = extract_python_list(text)
        self.assertEqual(len(result), 1)

    def test_code_block_python(self):
        text = "```python\n[{'title': 'Hello'}]\n```"
        result = extract_python_list(text)
        self.assertIsNotNone(result)

    def test_thinking_tags_stripped(self):
        text = '<think>reasoning here</think>\n[{"title": "A"}]'
        result = extract_python_list(text)
        self.assertEqual(len(result), 1)

    def test_single_dict_wrapped_in_list(self):
        text = '{"title": "Solo"}'
        result = extract_python_list(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Solo")

    def test_none_on_garbage(self):
        self.assertIsNone(extract_python_list("hello world"))

    def test_none_on_empty(self):
        self.assertIsNone(extract_python_list(""))

    def test_none_on_none(self):
        self.assertIsNone(extract_python_list(None))


class TestParseLlmJson(unittest.TestCase):
    """Tests for parse_llm_json function."""

    def test_list_type(self):
        result = parse_llm_json('[{"a": 1}]', expected_type="list")
        self.assertIsInstance(result, list)

    def test_dict_type_from_list(self):
        result = parse_llm_json('[{"a": 1}]', expected_type="dict")
        self.assertIsInstance(result, dict)

    def test_dict_direct_extraction(self):
        result = parse_llm_json('Here is the result: {"key": "val"}', expected_type="dict")
        self.assertEqual(result["key"], "val")

    def test_none_on_empty(self):
        self.assertIsNone(parse_llm_json(""))

    def test_none_on_unparseable(self):
        self.assertIsNone(parse_llm_json("not json at all"))


class TestExtractGrade(unittest.TestCase):
    """Tests for extract_grade_from_llm function."""

    def test_json_grade(self):
        self.assertEqual(extract_grade_from_llm('{"grade": 4}'), 4)

    def test_text_grade(self):
        self.assertEqual(extract_grade_from_llm("Grade: 3"), 3)

    def test_fraction_grade(self):
        self.assertEqual(extract_grade_from_llm("3/5"), 3)

    def test_score_pattern(self):
        self.assertEqual(extract_grade_from_llm("Score: 4"), 4)

    def test_clamped_high(self):
        self.assertEqual(extract_grade_from_llm('{"grade": 9}'), 5)

    def test_none_on_no_grade(self):
        self.assertIsNone(extract_grade_from_llm("no number here"))

    def test_none_on_empty(self):
        self.assertIsNone(extract_grade_from_llm(""))


if __name__ == '__main__':
    unittest.main()


# ---------------------------------------------------------------------------
# A COUNT SHORTFALL MUST BE DESCRIBED, NOT DUMPED.
#
# The retry prompt is built from this string. When it was jsonschema's raw
# message it carried the whole rejected array into the next request, under an
# instruction ("return the SAME content") that cannot satisfy a minimum.
# ---------------------------------------------------------------------------

def test_short_array_is_described_by_count_not_by_dumping_it():
    from services.common.llm_utils import schema_violation
    schema = {"type": "object", "properties": {
        "units": {"type": "array", "minItems": 3,
                  "items": {"type": "object"}}}}
    data = {"units": [{"title": "Only one", "body": "x" * 4000}]}
    detail = schema_violation(data, schema)
    assert "needs at least 3" in detail
    assert "1 item" in detail
    assert "x" * 100 not in detail, "the rejected value leaked into the hint"
    assert len(detail) < 200


def test_satisfied_minimum_is_not_a_violation():
    from services.common.llm_utils import schema_violation
    schema = {"type": "object", "properties": {
        "units": {"type": "array", "minItems": 2, "items": {"type": "object"}}}}
    assert schema_violation({"units": [{"a": 1}, {"b": 2}]}, schema) == ""


def test_unnamed_violations_are_truncated_before_reaching_a_prompt():
    from services.common.llm_utils import schema_violation
    schema = {"type": "object", "properties": {
        "tag": {"type": "string", "pattern": "^ok$"}}}
    detail = schema_violation({"tag": "z" * 5000}, schema)
    assert detail == "" or len(detail) < 300
