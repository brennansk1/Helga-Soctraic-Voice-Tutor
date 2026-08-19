"""The model's JSON shape varies, and a whole build must not die of it.

MEASURED: 1 build in 3 crashed with "'str' object has no attribute 'get'"
because the lesson list came back as ["Lesson one", "Lesson two"] where a list
of objects was requested. Roughly 20 minutes of work destroyed by a stylistic
choice. Other stages in this file already tolerate the same drift; this one did
not.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _coerce(lessons_data):
    """The normalisation now applied before lessons are read."""
    return [({"title": item} if isinstance(item, str) else item)
            for item in (lessons_data or [])
            if isinstance(item, (str, dict))]


class TestLessonShapeDrift(unittest.TestCase):
    def test_a_bare_string_becomes_a_title(self):
        """Coerce rather than reject: the string IS the title, which is the only
        required field."""
        assert _coerce(["Vectors", "Matrices"]) == [
            {"title": "Vectors"}, {"title": "Matrices"}]

    def test_objects_pass_through_untouched(self):
        rows = [{"title": "Vectors", "llm_fallback": True}]
        assert _coerce(rows) == rows

    def test_a_mixed_list_is_handled(self):
        out = _coerce([{"title": "A"}, "B"])
        assert out == [{"title": "A"}, {"title": "B"}]

    def test_junk_entries_are_dropped_not_crashed_on(self):
        assert _coerce([{"title": "A"}, None, 42, ["nested"]]) == [{"title": "A"}]

    def test_empty_and_none_are_safe(self):
        assert _coerce([]) == [] and _coerce(None) == []


class TestTheRealCrashIsFixed(unittest.TestCase):
    def test_builder_normalises_before_reading_titles(self):
        """Guard against the coercion being removed: the source must normalise
        the list before any .get() is called on its items."""
        import inspect
        from services.core.course_builder import SkeletonBuilder
        src = inspect.getsource(SkeletonBuilder._build_substructures_progressive)
        norm = src.index('{"title": item} if isinstance(item, str)')
        read = src.index('lesson_data.get("title"')
        assert norm < read, "lessons are read before the shape is normalised"


if __name__ == "__main__":
    unittest.main()
