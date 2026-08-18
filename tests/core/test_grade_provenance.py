"""A grade produced by an LLM outage must be distinguishable from a real one.

The grade-2 default on a grading failure is the right fail-safe -- B3.3 chose it
so an outage can never credit mastery -- but it was indistinguishable downstream
from a learner who genuinely earned a 2. FSRS scheduling, mastery gates and
(under the programme design, where retention gates a course pass) all consume
these. A grade fabricated during an outage is data about the infrastructure.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
os.environ.setdefault("DATA_ROOT", "/tmp/helga_test_data")
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core.fsm_logic import MnemosyneFSM  # noqa: E402


class TestGradeProvenance(unittest.TestCase):
    def setUp(self):
        self.fsm = MnemosyneFSM.__new__(MnemosyneFSM)

    def test_failure_is_marked_ungraded(self):
        r = self.fsm._parse_grade_response(None)
        assert r["grade"] == 2, "fail-safe must still not credit mastery"
        assert r["graded"] is False
        assert r["grade_source"] == "fallback"

    def test_real_grade_is_marked_graded(self):
        r = self.fsm._parse_grade_response('{"grade": 4, "feedback": "good"}')
        assert r["grade"] == 4
        assert r["graded"] is True
        assert r["grade_source"] == "llm"

    def test_a_genuine_two_is_not_confused_with_a_fallback_two(self):
        """The whole point: same number, different provenance."""
        real = self.fsm._parse_grade_response('{"grade": 2, "feedback": "partial"}')
        fake = self.fsm._parse_grade_response(None)
        assert real["grade"] == fake["grade"] == 2
        assert real["graded"] is True and fake["graded"] is False

    def test_unparseable_content_is_also_marked(self):
        r = self.fsm._parse_grade_response("the model rambled without json")
        assert r.get("graded") is False or r.get("grade_source") == "fallback"


if __name__ == "__main__":
    unittest.main()
