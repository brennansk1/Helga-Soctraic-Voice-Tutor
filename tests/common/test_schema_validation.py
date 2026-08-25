"""validate_schema must actually validate the schemas production passes it.

THE BUG THIS FILE EXISTS FOR
----------------------------
`validate_schema` handled `type: 'list'` and `type: 'dict'` and then
`return True`. Every production caller passes REAL JSON Schema — `'object'` /
`'array'` — because the identical dict is handed to Ollama's `format` field for
constrained decoding. So it returned True for everything:

    validate_schema({},        {"type": "object", "required": ["modules"]}) -> True
    validate_schema([1, 2, 3], {"type": "object"})                          -> True

and the named-deficiency retry underneath it in `llm_generate_json` — reachable
only when validation FAILS — had never executed on the path that generates
every concept of every course. Each test below fails against that version.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from services.common import llm_utils                       # noqa: E402
from services.common.llm_utils import (                     # noqa: E402
    validate_schema,
    schema_violation,
)

# The real shape from SkeletonBuilder.SUBTREE_SCHEMA, trimmed to one level and
# with the minItems floor that `subtree_schema()` bakes in at build time.
SUBTREE_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "lessons": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "lessons"],
            },
        }
    },
    "required": ["units"],
}


def _unit(title="Unit"):
    return {"title": title, "lessons": ["L1", "L2"]}


class TestRealJsonSchemaIsEnforced(unittest.TestCase):
    """The cases the old implementation waved through."""

    def test_empty_object_missing_required_key_is_rejected(self):
        self.assertFalse(validate_schema({}, SUBTREE_SCHEMA))

    def test_missing_required_key_is_rejected(self):
        data = {"description": "no units here"}
        self.assertFalse(validate_schema(data, SUBTREE_SCHEMA))

    def test_list_where_object_required_is_rejected(self):
        self.assertFalse(validate_schema([1, 2, 3], SUBTREE_SCHEMA))
        self.assertFalse(validate_schema([{"units": []}], SUBTREE_SCHEMA))

    def test_valid_document_passes(self):
        data = {"units": [_unit("A"), _unit("B")]}
        self.assertTrue(validate_schema(data, SUBTREE_SCHEMA))

    def test_min_items_is_enforced(self):
        too_short = {"units": [_unit("A")]}          # minItems is 2
        self.assertFalse(validate_schema(too_short, SUBTREE_SCHEMA))

    def test_required_key_missing_inside_array_item_is_rejected(self):
        data = {"units": [_unit("A"), {"title": "B"}]}   # no 'lessons'
        self.assertFalse(validate_schema(data, SUBTREE_SCHEMA))

    def test_wrong_scalar_type_inside_array_item_is_rejected(self):
        data = {"units": [_unit("A"), {"title": 7, "lessons": []}]}
        self.assertFalse(validate_schema(data, SUBTREE_SCHEMA))

    def test_array_root_schema(self):
        schema = {"type": "array", "items": {"type": "object",
                                             "required": ["title"]}}
        self.assertTrue(validate_schema([{"title": "ok"}], schema))
        self.assertFalse(validate_schema([{"name": "nope"}], schema))
        self.assertFalse(validate_schema({"title": "ok"}, schema))


class TestLegacySchemaContractStillWorks(unittest.TestCase):
    """tests/core/test_claude_fixes.py depends on this shorthand."""

    def test_legacy_list_required_keys(self):
        schema = {"type": "list", "items": {"required_keys": ["title", "uid"]}}
        self.assertTrue(validate_schema([{"title": "A", "uid": "a"}], schema))
        self.assertFalse(validate_schema([{"title": "A"}], schema))

    def test_legacy_dict_required_keys(self):
        schema = {"type": "dict", "required_keys": ["title"]}
        self.assertTrue(validate_schema({"title": "Course"}, schema))
        self.assertFalse(validate_schema({"other": 1}, schema))

    def test_legacy_wrong_type(self):
        self.assertFalse(validate_schema("not a list", {"type": "list"}))

    def test_no_schema_is_always_valid(self):
        self.assertTrue(validate_schema([1, 2, 3], None))
        self.assertTrue(validate_schema([1, 2, 3], {}))


class TestViolationIsUsableRetryMaterial(unittest.TestCase):
    """The reason string is fed back to the model verbatim."""

    def test_valid_document_has_no_violation(self):
        self.assertEqual(
            schema_violation({"units": [_unit("A"), _unit("B")]}, SUBTREE_SCHEMA), "")

    def test_missing_key_is_named_in_words(self):
        detail = schema_violation({}, SUBTREE_SCHEMA)
        self.assertIn("units", detail)
        self.assertIn("required", detail)

    def test_wrong_root_type_is_named(self):
        detail = schema_violation([1, 2, 3], SUBTREE_SCHEMA)
        self.assertIn("object", detail)
        self.assertIn("list", detail)

    def test_min_items_falls_back_to_jsonschema_wording(self):
        detail = schema_violation({"units": [_unit("A")]}, SUBTREE_SCHEMA)
        self.assertTrue(detail, "a minItems violation must produce a reason")


class TestFailsSafeWithoutJsonschema(unittest.TestCase):
    """A missing optional dependency must not take the service down."""

    def setUp(self):
        self._available = llm_utils._JSONSCHEMA_AVAILABLE
        self._warned = llm_utils._degraded_warned
        llm_utils._JSONSCHEMA_AVAILABLE = False
        llm_utils._degraded_warned = False

    def tearDown(self):
        llm_utils._JSONSCHEMA_AVAILABLE = self._available
        llm_utils._degraded_warned = self._warned

    def test_degrades_to_permissive_and_warns_once(self):
        with self.assertLogs(llm_utils.logger, level="WARNING") as caught:
            self.assertTrue(validate_schema({}, SUBTREE_SCHEMA))
        self.assertTrue(any("jsonschema" in m for m in caught.output))
        # Second call must not warn again (assertLogs fails with no records).
        self.assertTrue(validate_schema({}, SUBTREE_SCHEMA))
        self.assertTrue(llm_utils._degraded_warned)

    def test_legacy_schemas_still_validated_while_degraded(self):
        schema = {"type": "dict", "required_keys": ["title"]}
        self.assertFalse(validate_schema({"other": 1}, schema))

    def test_invalid_schema_is_our_bug_not_the_models(self):
        llm_utils._JSONSCHEMA_AVAILABLE = self._available
        # 'required' must be an array; a broken schema must not fail the build.
        self.assertTrue(validate_schema({"a": 1}, {"type": "object",
                                                  "required": "units"}))


class TestNamedDeficiencyRetryActuallyRuns(unittest.TestCase):
    """The retry loop this bug had silenced end to end."""

    def setUp(self):
        self._real = llm_utils.llm_generate
        self.prompts = []

    def tearDown(self):
        llm_utils.llm_generate = self._real

    def _stub(self, replies):
        def fake(prompt, **kwargs):
            self.prompts.append(prompt)
            return replies[min(len(self.prompts) - 1, len(replies) - 1)]
        llm_utils.llm_generate = fake

    def test_bad_shape_is_retried_against_the_named_deficiency(self):
        self._stub(['{"unit_list": []}',
                    '{"units": [{"title": "A", "lessons": ["L"]},'
                    ' {"title": "B", "lessons": ["L"]}]}'])
        result = llm_utils.llm_generate_json(
            "build the subtree", expected_type="dict",
            schema=SUBTREE_SCHEMA, retries=3)

        self.assertIsInstance(result, dict)
        self.assertEqual(len(result["units"]), 2)
        self.assertEqual(len(self.prompts), 2, "the retry did not happen")
        self.assertIn("REJECTED", self.prompts[1])
        self.assertIn("units", self.prompts[1])

    def test_gives_up_and_returns_none_when_shape_never_conforms(self):
        self._stub(['{"unit_list": []}'])
        result = llm_utils.llm_generate_json(
            "build the subtree", expected_type="dict",
            schema=SUBTREE_SCHEMA, retries=2)
        self.assertIsNone(result)
        self.assertEqual(len(self.prompts), 2)

    def test_object_schema_result_is_not_wrapped_in_a_list(self):
        # extract_python_list wraps a parsed object as [obj]; with an object
        # schema that would fail validation on a CORRECT answer.
        self._stub(['{"units": [{"title": "A", "lessons": ["L"]},'
                    ' {"title": "B", "lessons": ["L"]}]}'])
        result = llm_utils.llm_generate_json(          # default expected_type="list"
            "build the subtree", schema=SUBTREE_SCHEMA, retries=2)
        self.assertIsInstance(result, dict)
        self.assertEqual(len(self.prompts), 1, "a valid answer must not retry")


if __name__ == '__main__':
    unittest.main()
