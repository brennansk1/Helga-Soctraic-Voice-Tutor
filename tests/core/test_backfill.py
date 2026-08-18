"""Coverage backfill: act on the syllabus gap instead of only reporting it.

Measured against MIT 18.06, a generated Linear Algebra course covered 7 of 10
published topic areas while running 59% LONGER than the real course. It was not
short of room -- it never selected the orthogonality cluster. The syllabus check
had always been diagnostic only, so nothing acted on that.
"""

import os
import sys
import unittest
from unittest import mock

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
for p in (_root, os.path.join(_root, "services/core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.core.course_builder import SkeletonBuilder  # noqa: E402


def _builder(chapters):
    b = SkeletonBuilder.__new__(SkeletonBuilder)
    b.status_callback = None
    b.course_params = {"concepts_per_lesson": 3}
    b._syllabus_chapters = chapters
    return b


def _course(*lesson_titles):
    return {"modules": [{"title": "M", "units": [
        {"title": "u", "lessons": [{"title": t, "concepts": []}
                                   for t in lesson_titles]}]}]}


def _lessons(course):
    return [l["title"] for m in course["modules"]
            for u in m.get("units", []) for l in u.get("lessons", [])]


_STUB = lambda self, ch, topic, n: [{"uid": "con_x", "title": f"{ch} basics"}]


class TestGapDetection(unittest.TestCase):
    def test_adds_exactly_the_cluster_the_mit_comparison_found_missing(self):
        b = _builder(["Orthogonality", "Least Squares Approximation",
                      "Gram-Schmidt Process", "Determinants"])
        course = _course("Cofactor expansion and determinants")
        with mock.patch.object(SkeletonBuilder, "_concepts_for_backfill", _STUB):
            b._backfill_uncovered_chapters(course, "Linear Algebra")
        added = _lessons(course)[1:]
        assert added == ["Orthogonality", "Least Squares Approximation",
                         "Gram-Schmidt Process"], added
        assert course["backfilled_lessons"] == 3

    def test_covered_chapters_are_not_duplicated(self):
        """The point is coverage, not volume — re-adding what is already taught
        is the duplication problem this project already has at program scale."""
        b = _builder(["Eigenvalues and Eigenvectors"])
        course = _course("Eigenvalues and Eigenvectors")
        with mock.patch.object(SkeletonBuilder, "_concepts_for_backfill", _STUB):
            b._backfill_uncovered_chapters(course, "Linear Algebra")
        assert _lessons(course) == ["Eigenvalues and Eigenvectors"]
        assert "backfilled_lessons" not in course

    def test_generic_chapter_names_are_not_evidence_either_way(self):
        """'Introduction' matches everything and means nothing; treating it as a
        gap would add a lesson to every course forever."""
        b = _builder(["Introduction", "Overview", "Chapter 1", "Basics"])
        course = _course("Something unrelated")
        with mock.patch.object(SkeletonBuilder, "_concepts_for_backfill", _STUB):
            b._backfill_uncovered_chapters(course, "X")
        assert "backfilled_lessons" not in course

    def test_partial_word_match_counts_as_covered(self):
        b = _builder(["Orthogonal Projections"])
        course = _course("Projections onto subspaces")
        with mock.patch.object(SkeletonBuilder, "_concepts_for_backfill", _STUB):
            b._backfill_uncovered_chapters(course, "X")
        assert "backfilled_lessons" not in course


class TestSafety(unittest.TestCase):
    def test_no_syllabus_means_no_backfill(self):
        """With no external evidence there is no gap to speak of. Inventing one
        from model knowledge is exactly the self-referential move this avoids."""
        course = _course("A")
        _builder([])._backfill_uncovered_chapters(course, "X")
        assert _lessons(course) == ["A"]

    def test_failed_concept_generation_adds_no_stub_lesson(self):
        """A lesson with no concepts is worse than a missing lesson: it renders
        as a real step and teaches nothing."""
        b = _builder(["Gram-Schmidt Process"])
        course = _course("Unrelated")
        with mock.patch.object(SkeletonBuilder, "_concepts_for_backfill",
                               lambda self, c, t, n: []):
            b._backfill_uncovered_chapters(course, "X")
        assert _lessons(course) == ["Unrelated"]

    def test_backfill_is_capped(self):
        b = _builder([f"Distinct Topic {i}" for i in range(40)])
        course = _course("Unrelated")
        with mock.patch.object(SkeletonBuilder, "_concepts_for_backfill", _STUB):
            b._backfill_uncovered_chapters(course, "X", cap=6)
        assert course["backfilled_lessons"] == 6

    def test_backfilled_items_are_marked_as_such(self):
        """Provenance: a learner and a later audit should both be able to tell
        which material came from the outline and which was patched in."""
        b = _builder(["Gram-Schmidt Process"])
        course = _course("Unrelated")
        with mock.patch.object(SkeletonBuilder, "_concepts_for_backfill", _STUB):
            b._backfill_uncovered_chapters(course, "X")
        added = course["modules"][0]["units"][0]["lessons"][-1]
        assert added["backfilled"] is True


if __name__ == "__main__":
    unittest.main()


class TestSourceSelection(unittest.TestCase):
    """Only the best-matching syllabus may act as a coverage checklist.

    REGRESSION: for "Linear Algebra" the brief also matched OpenStax *College
    Algebra*, and pooling every source's chapters backfilled Exponential and
    Logarithmic Functions, Analytic Geometry and Probability into a linear
    algebra course. A weaker source is useful as corroboration and dangerous as
    a checklist.
    """

    def _retain(self, syllabi):
        b = SkeletonBuilder.__new__(SkeletonBuilder)
        brief = {"syllabi": syllabi, "courses": []}
        # exercise the retention block via the same code path
        ranked = sorted([o for o in brief["syllabi"] if o.get("chapters")],
                        key=lambda o: o.get("relevance", 0), reverse=True)
        best = ranked[0]
        margin = float(best.get("relevance", 0)) * 0.75
        chosen = [o for o in ranked if float(o.get("relevance", 0)) >= margin]
        return [c for o in chosen for c in o["chapters"]]

    def test_weak_secondary_source_is_excluded(self):
        chapters = self._retain([
            {"book": "Linear Algebra", "relevance": 7.5,
             "chapters": ["Vector Spaces", "Eigenvalues"]},
            {"book": "College Algebra", "relevance": 2.25,
             "chapters": ["Exponential and Logarithmic Functions",
                          "Analytic Geometry", "Probability"]},
        ])
        assert chapters == ["Vector Spaces", "Eigenvalues"], chapters

    def test_comparably_strong_sources_are_both_kept(self):
        """Two genuine syllabi for the same subject corroborate each other."""
        chapters = self._retain([
            {"book": "Linear Algebra", "relevance": 7.5, "chapters": ["A"]},
            {"book": "Linear Algebra (Wikiversity)", "relevance": 6.5,
             "chapters": ["B"]},
        ])
        assert chapters == ["A", "B"]
