"""Copy the real textbook's spine instead of inventing one.

Measured against MIT 18.06: an invented spine covered 7-9 of 10 published topic
areas while running 38-59% LONGER than the real course. It was never short of
room -- it never SELECTED least squares, projections or Gram-Schmidt, and the
same cluster went missing on every run. Selection is where coverage is lost.
"""

import os
import sys
import unittest

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core.course_builder import SkeletonBuilder  # noqa: E402


def _b(outlines):
    b = SkeletonBuilder.__new__(SkeletonBuilder)
    b.status_callback = None
    b._syllabus_outlines = outlines
    return b


def _outline(book, relevance, n_chapters, source="Wikibooks"):
    return {"book": book, "source": source, "relevance": relevance,
            "url": "http://x", "chapters": [f"Chapter {i}" for i in range(n_chapters)]}


class TestWhenItFires(unittest.TestCase):
    def test_exact_subject_match_with_enough_chapters_copies(self):
        spine = _b([_outline("Linear Algebra", 7.5, 30)])._spine_from_syllabus(
            "Linear Algebra", 6)
        assert spine is not None and len(spine) == 6
        assert all(m["from_syllabus"] for m in spine)

    def test_merely_overlapping_book_does_not_copy(self):
        """College Algebra scored 4.75 against Linear Algebra's 4.67 before the
        ranking fix and pulled Probability into a linear algebra course. Copying
        the WRONG book's structure is worse than inventing one."""
        assert _b([_outline("College Algebra", 4.75, 30)])._spine_from_syllabus(
            "Linear Algebra", 6) is None

    def test_too_few_chapters_does_not_copy(self):
        """Copying 3 chapters into 6 modules means padding, which is the failure
        this is meant to avoid."""
        assert _b([_outline("Linear Algebra", 7.5, 3)])._spine_from_syllabus(
            "Linear Algebra", 6) is None

    def test_no_syllabus_falls_through_to_generation(self):
        assert _b([])._spine_from_syllabus("Anything", 6) is None

    def test_env_flag_disables_it(self):
        os.environ["HELGA_COPY_SPINE"] = "0"
        try:
            assert _b([_outline("Linear Algebra", 9.0, 40)])._spine_from_syllabus(
                "Linear Algebra", 6) is None
        finally:
            os.environ.pop("HELGA_COPY_SPINE", None)


class TestScopeAdaptation(unittest.TestCase):
    def test_a_long_book_is_sampled_across_its_whole_arc(self):
        """A 154-chapter book is not a 6-module course. Taking the FIRST six
        chapters would keep only the introduction; the material the invented
        spines kept missing lives in the tail."""
        chapters = [f"Topic {i:03d}" for i in range(154)]
        b = _b([{"book": "Linear Algebra", "relevance": 9.0, "source": "Wikibooks",
                 "url": "u", "chapters": chapters}])
        spine = b._spine_from_syllabus("Linear Algebra", 6)
        titles = [m["title"] for m in spine]
        assert titles[0] == "Topic 000"
        assert int(titles[-1].split()[-1]) > 100, \
            f"spine stopped at {titles[-1]} — the book's later material was dropped"

    def test_provenance_is_recorded(self):
        b = _b([_outline("Linear Algebra", 8.0, 30)])
        b._spine_from_syllabus("Linear Algebra", 6)
        assert b._spine_source["book"] == "Linear Algebra"
        assert b._spine_source["chapters_available"] == 30


if __name__ == "__main__":
    unittest.main()
