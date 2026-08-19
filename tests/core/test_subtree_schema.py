"""A count the schema does not enforce is a count the prompt merely requests.

MEASURED: modules asked for 3 units and 9 lessons returned ~1.7 units and ~5
lessons. Firming the prompt from "about N units" to "this is NOT approximate"
moved nothing, because the grammar the model decoded against still permitted the
short answer. SUBTREE_SCHEMA had no minItems anywhere.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core.course_builder import SkeletonBuilder  # noqa: E402


def _levels(schema):
    units = schema["properties"]["units"]
    lessons = units["items"]["properties"]["lessons"]
    concepts = lessons["items"]["properties"]["concepts"]
    return units, lessons, concepts


class TestMinimumsAreEnforced(unittest.TestCase):
    def test_every_level_carries_its_minimum(self):
        u, l, c = _levels(SkeletonBuilder.subtree_schema(3, 3, 3))
        assert u["minItems"] == 3 and l["minItems"] == 3 and c["minItems"] == 3

    def test_minimums_track_the_requested_shape(self):
        u, l, c = _levels(SkeletonBuilder.subtree_schema(2, 4, 5))
        assert (u["minItems"], l["minItems"], c["minItems"]) == (2, 4, 5)

    def test_a_floor_is_never_below_one(self):
        u, l, c = _levels(SkeletonBuilder.subtree_schema(0, -1, 0))
        assert u["minItems"] == l["minItems"] == c["minItems"] == 1


class TestItRemainsAFloorNotAQuota(unittest.TestCase):
    def test_no_maximum_is_imposed(self):
        """Units may differ in size — the topical grouping the design calls for
        depends on a module being allowed MORE than the minimum."""
        u, l, c = _levels(SkeletonBuilder.subtree_schema(3, 3, 3))
        for level in (u, l, c):
            assert "maxItems" not in level


class TestTheStaticSchemaIsNotMutated(unittest.TestCase):
    def test_repeated_calls_do_not_accumulate(self):
        """A shallow copy would let one module's minimums leak into the next."""
        SkeletonBuilder.subtree_schema(9, 9, 9)
        u, _, _ = _levels(SkeletonBuilder.subtree_schema(2, 2, 2))
        assert u["minItems"] == 2

    def test_the_class_constant_stays_unconstrained(self):
        u, l, c = _levels(SkeletonBuilder.SUBTREE_SCHEMA)
        for level in (u, l, c):
            assert "minItems" not in level


if __name__ == "__main__":
    unittest.main()


class TestUnitsStayFlexible(unittest.TestCase):
    """A unit is a TOPICAL grouping whose size should follow the material; the
    lesson count is the calendar. Forcing a minimum unit count splits material
    that naturally forms fewer groups, which contradicts the design decision that
    units may differ in size.
    """

    def test_the_unit_floor_is_school_shaped_not_absent(self):
        """A module is 2-3 weeks, so 2 units is structural rather than arbitrary.

        This asserted `min_units=1` when the floor was relaxed to honour "units
        may differ in size". Measured consequence: units-per-module came back
        [3, 3, 1, 1, 1, 1] — four of six modules were one week wearing a
        module's name — while coverage fell 100% -> 90% and lesson balance went
        from spread 0.04 to a 3.0 taper.

        Flexibility below the level a registrar would recognise is not
        flexibility, it is collapse. Units remain uncapped above the floor.
        """
        import inspect
        from services.core.course_builder import SkeletonBuilder
        src = inspect.getsource(SkeletonBuilder._build_module_subtree_oneshot)
        assert "min_units=2" in src, \
            "a module of one unit is a week, not a module"

    def test_lesson_and_concept_floors_are_still_passed(self):
        """The lesson floor now derives from the range MINIMUM rather than the
        target — enforcing the target would make the range decorative, since a
        thin subject could never settle at the low end."""
        import inspect
        from services.core.course_builder import SkeletonBuilder
        src = inspect.getsource(SkeletonBuilder._build_module_subtree_oneshot)
        assert "min_lessons=" in src and "_lesson_lo" in src
        assert "min_concepts=base_concepts" in src
