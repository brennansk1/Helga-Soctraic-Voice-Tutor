"""An empty unit renders as a step that teaches nothing.

MEASURED: a real build produced a unit called "Session Zero" with zero lessons,
which had passed every other structural check. These survive because the lesson
minimum is advisory on the endpoint the builder posts to -- minItems is stripped
from response_format and /v1 ignores the format field carrying it -- so nothing
stops a unit coming back empty.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core.course_builder import SkeletonBuilder  # noqa: E402


def _b():
    b = SkeletonBuilder.__new__(SkeletonBuilder)
    b.status_callback = None
    return b


def _course(*unit_specs):
    """unit_specs: (title, n_lessons) per unit, all in one module."""
    return {"modules": [{"title": "M", "units": [
        {"title": t, "lessons": [{"title": f"L{i}", "concepts": []}
                                 for i in range(n)]}
        for t, n in unit_specs]}]}


class TestEmptyUnitPruning(unittest.TestCase):
    def test_an_empty_unit_is_dropped(self):
        c = _course(("Real Unit", 3), ("Session Zero", 0))
        assert _b()._drop_empty_units(c) == 1
        assert [u["title"] for u in c["modules"][0]["units"]] == ["Real Unit"]

    def test_units_with_lessons_are_untouched(self):
        c = _course(("A", 3), ("B", 2))
        assert _b()._drop_empty_units(c) == 0
        assert len(c["modules"][0]["units"]) == 2

    def test_a_module_is_never_emptied(self):
        """A module with no units at all is a worse defect than the one being
        fixed, so pruning stops short of that."""
        c = _course(("Only Unit", 0))
        _b()._drop_empty_units(c)
        assert len(c["modules"][0]["units"]) == 1

    def test_a_single_lesson_unit_is_kept(self):
        """Thin is not empty. A one-lesson unit is reported by the structure
        check; removing it would lose real material."""
        c = _course(("Thin", 1), ("Full", 3))
        assert _b()._drop_empty_units(c) == 0

    def test_it_runs_before_persist(self):
        import inspect
        src = inspect.getsource(SkeletonBuilder._build_inner)
        assert "_drop_empty_units" in src


if __name__ == "__main__":
    unittest.main()
