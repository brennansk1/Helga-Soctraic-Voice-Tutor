"""The two endpoints do not accept the same schema.

MEASURED: Ollama's native `format` honours minItems (minItems=5 took an 8-unit
answer to 10). The OpenAI-compatible /v1 json_schema validator REJECTS it with a
400 in ~0.3 s. Sending one schema to both turned a working call into an instant
failure, and because the caller reads a failure as "empty", it silently disabled
the one-shot subtree path for 5 of 6 modules and made every course a third
shorter -- with no error reaching the user.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common.llm_utils import _V1_UNSUPPORTED, _v1_safe_schema  # noqa: E402


class TestReduction(unittest.TestCase):
    def test_unsupported_keywords_are_removed(self):
        schema = {"type": "array", "minItems": 3, "maxItems": 9,
                  "items": {"type": "string", "minLength": 2}}
        safe = _v1_safe_schema(schema)
        assert "minItems" not in safe and "maxItems" not in safe
        assert "minLength" not in safe["items"]

    def test_it_reaches_every_level_of_nesting(self):
        schema = {"properties": {"units": {"type": "array", "minItems": 3,
                  "items": {"properties": {"lessons": {"type": "array",
                                                       "minItems": 3}}}}}}
        safe = _v1_safe_schema(schema)
        deep = safe["properties"]["units"]["items"]["properties"]["lessons"]
        assert "minItems" not in deep

    def test_the_caller_s_schema_is_not_mutated(self):
        """A shared reference would strip the constraint from the NATIVE field
        too — the one place it actually works."""
        schema = {"type": "array", "minItems": 4}
        _v1_safe_schema(schema)
        assert schema["minItems"] == 4

    def test_structure_and_types_survive(self):
        schema = {"type": "object", "required": ["a"],
                  "properties": {"a": {"type": "string"}}}
        assert _v1_safe_schema(schema) == schema

    def test_lists_are_walked(self):
        schema = {"anyOf": [{"type": "array", "minItems": 2}]}
        assert "minItems" not in _v1_safe_schema(schema)["anyOf"][0]

    def test_the_keyword_list_covers_the_common_constraints(self):
        for kw in ("minItems", "maxItems", "minLength", "pattern", "minimum"):
            assert kw in _V1_UNSUPPORTED


class TestBothFieldsAreStillSent(unittest.TestCase):
    def test_native_format_keeps_the_constraint(self):
        """The strictest endpoint must not dictate what the permissive one is
        allowed to enforce."""
        import inspect
        from services.common import llm_utils
        src = inspect.getsource(llm_utils)
        assert '"format": json_format' in src, "native format must get the full schema"
        assert '_v1_safe_schema(json_format)' in src, "v1 must get the reduction"


if __name__ == "__main__":
    unittest.main()
