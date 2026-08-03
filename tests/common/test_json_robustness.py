"""Smaller/quantised models emit malformed JSON; parsing must survive it.

Observed in this repo, not hypothetical: a judge model emitted
    {"verdict": "CORRECTED", "evidence": "the tutor said "excellent" here"}
and json.loads rejected it, silently discarding 24 of 30 evaluation trials and
leaving a meaningless "100% from 6 trials". Unescaped quotes inside string
values are the single most common malformation and the one the old repair path
gave up on entirely.

Defence in depth, strongest first:
  1. grammar-constrained decoding (Ollama `format`) so invalid JSON cannot be
     generated -- see llm_generate(json_format=...)
  2. repair_json() for unconstrained output
  3. retry, escalating to constrained JSON mode
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common.llm_utils import (  # noqa: E402
    parse_llm_json, repair_json, _escape_inner_quotes,
)


class TestUnescapedInnerQuotes(unittest.TestCase):
    """The failure that used to discard whole responses."""

    def test_quote_inside_value(self):
        r = parse_llm_json('[{"title":"the "big" one","rationale":"x"}]', expected_type="list")
        self.assertIsNotNone(r)
        self.assertEqual(r[0]["title"], 'the "big" one')

    def test_real_judge_failure_case(self):
        raw = ('{"verdict": "CORRECTED", "evidence": "the tutor said "excellent" '
               'and moved on"}')
        r = parse_llm_json(raw, expected_type="dict")
        self.assertIsNotNone(r, "this exact shape discarded 24/30 real trials")
        self.assertEqual(r["verdict"], "CORRECTED")

    def test_multiple_inner_quotes_across_fields(self):
        r = parse_llm_json('[{"a":"say "hi" now","b":"and "bye" then"}]',
                           expected_type="list")
        self.assertIsNotNone(r)
        self.assertEqual(r[0]["b"], 'and "bye" then')

    def test_colon_inside_value_is_not_mistaken_for_structure(self):
        r = parse_llm_json('[{"title":"ratio 3:1 is "high""}]', expected_type="list")
        self.assertIsNotNone(r)

    def test_already_escaped_is_left_alone(self):
        r = parse_llm_json('[{"title":"the \\"big\\" one"}]', expected_type="list")
        self.assertIsNotNone(r)
        self.assertEqual(r[0]["title"], 'the "big" one')

    def test_valid_json_is_not_corrupted(self):
        raw = '[{"title":"A","rationale":"B"}]'
        self.assertEqual(_escape_inner_quotes(raw), raw)

    def test_never_raises_on_garbage(self):
        for junk in ('', '"', '{{{', 'not json at all', '"""""'):
            _escape_inner_quotes(junk)  # must not raise


class TestOtherMalformations(unittest.TestCase):
    def test_markdown_fence(self):
        # Ollama's /v1 shim wraps grammar-constrained output in ```json fences.
        self.assertIsNotNone(
            parse_llm_json('```json\n[{"title":"A"}]\n```', expected_type="list"))

    def test_trailing_comma(self):
        self.assertIsNotNone(parse_llm_json('[{"title":"A",}]', expected_type="list"))

    def test_single_quotes(self):
        self.assertIsNotNone(parse_llm_json("[{'title':'A'}]", expected_type="list"))

    def test_prose_preamble(self):
        self.assertIsNotNone(
            parse_llm_json('Here you go:\n[{"title":"A"}]', expected_type="list"))

    def test_python_literals(self):
        r = parse_llm_json('[{"ok":True,"bad":False,"n":None}]', expected_type="list")
        self.assertIsNotNone(r)
        self.assertIs(r[0]["ok"], True)
        self.assertIsNone(r[0]["n"])


class TestConstrainedDecodingIsWired(unittest.TestCase):
    """Repair is the backstop; constrained generation is the real fix."""

    def test_llm_generate_accepts_json_format(self):
        import inspect
        from services.common import llm_utils
        self.assertIn("json_format",
                      inspect.signature(llm_utils.llm_generate).parameters)

    def test_generate_json_passes_schema_as_format(self):
        import inspect
        from services.common import llm_utils
        src = inspect.getsource(llm_utils.llm_generate_json)
        self.assertIn("json_format", src,
                      "llm_generate_json must constrain generation, not only "
                      "validate afterwards")

    def test_retry_escalates_to_constrained_mode(self):
        import inspect
        from services.common import llm_utils
        src = inspect.getsource(llm_utils.llm_generate_json)
        self.assertIn('"json"', src,
                      "a failed parse should retry in JSON mode rather than "
                      "re-rolling the same unconstrained prompt")


if __name__ == '__main__':
    unittest.main()
