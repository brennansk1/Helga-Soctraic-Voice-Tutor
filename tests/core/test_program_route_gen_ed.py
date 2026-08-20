"""The general-education choice must actually reach the planner.

Everything else about this feature was tested on the planner directly. That
leaves the wiring untested, and wiring is where this repo's bugs live: a
radio nobody reads, a parameter the route drops, a default that quietly
reasserts itself. This drives the real core handler.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))


class _MockFlaskApp:
    def __init__(self, *a, **k):
        pass

    def route(self, *a, **k):
        return lambda f: f

    def run(self, *a, **k):
        pass


_DEPS = {k: MagicMock() for k in (
    'kuzu', 'libzim', 'sentence_transformers', 'psutil', 'yaml', 'fsrs_engine',
    'safety', 'service_manager', 'db_manager', 'content_provider',
    'course_builder')}
_DEPS['course_builder'].__file__ = 'mock.py'

with patch.dict('sys.modules', _DEPS):
    from services.core import fsm_logic
import services.core.program as prog


def _call(body):
    """Invoke the real route, capturing what plan_degree was handed."""
    seen = {}

    def fake_plan_degree(subject, template, llm_json_fn=None, search_fn=None,
                         preset="college", general_education="include"):
        seen["general_education"] = general_education
        return {"subject": subject, "template": template, "courses": [],
                "general_education": general_education}

    with patch.object(prog, "plan_degree", fake_plan_degree), \
            patch.object(fsm_logic, "request",
                         MagicMock(get_json=lambda **k: body)):
        result = fsm_logic.create_program()
    status = result[1] if isinstance(result, tuple) and len(result) > 1 else 200
    return seen.get("general_education"), status


@pytest.mark.parametrize("mode", ["skip", "transferred", "include"])
def test_the_choice_reaches_the_planner(mode):
    got, status = _call({"subject": "Dungeon Mastering",
                         "template": "bachelors",
                         "general_education": mode})
    assert got == mode
    assert status == 201


def test_omitting_it_keeps_the_full_programme():
    """No preference expressed must not quietly shorten someone's degree."""
    got, status = _call({"subject": "Economics", "template": "bachelors"})
    assert got == "include"
    assert status == 201


def test_an_unrecognised_value_is_refused():
    """Not silently coerced -- a typo must not decide the shape of a degree."""
    got, status = _call({"subject": "Economics", "template": "bachelors",
                         "general_education": "nonsense"})
    assert status == 400
    assert got is None, "the planner should never have been called"
