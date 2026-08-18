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


# A realistic chapter list: sequenced as taught, NOT alphabetical. Using
# "Chapter 0, Chapter 1, ..." here would be sorted, and the alphabetical-index
# guard would correctly refuse it — the fixture would then be testing the guard
# rather than the behaviour under test.
_TAUGHT_ORDER = [
    "Vectors and Vector Spaces", "Solving Linear Systems", "Matrix Operations",
    "Determinants", "Inverses and LU Factorization", "Basis and Dimension",
    "The Four Fundamental Subspaces", "Orthogonality", "Projections",
    "Least Squares", "Gram-Schmidt", "Eigenvalues", "Diagonalization",
    "Symmetric Matrices", "Positive Definite Matrices", "Singular Value Decomposition",
]


def _outline(book, relevance, n_chapters, source="Wikibooks"):
    chapters = [f"{_TAUGHT_ORDER[i % len(_TAUGHT_ORDER)]} {i // len(_TAUGHT_ORDER) or ''}".strip()
                for i in range(n_chapters)]
    return {"book": book, "source": source, "relevance": relevance,
            "url": "http://x", "chapters": chapters}


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
        # 154 sequenced (not sorted) chapters
        chapters = [f"{_TAUGHT_ORDER[i % len(_TAUGHT_ORDER)]} part {i}"
                    for i in range(154)]
        b = _b([{"book": "Linear Algebra", "relevance": 9.0, "source": "Wikibooks",
                 "url": "u", "chapters": chapters}])
        spine = b._spine_from_syllabus("Linear Algebra", 6)
        titles = [m["title"] for m in spine]
        assert int(titles[-1].split()[-1]) > 100, \
            f"spine stopped at {titles[-1]} — the book's later material was dropped"

    def test_provenance_is_recorded(self):
        b = _b([_outline("Linear Algebra", 8.0, 30)])
        b._spine_from_syllabus("Linear Algebra", 6)
        assert b._spine_source["book"] == "Linear Algebra"
        assert b._spine_source["chapters_available"] == 30


if __name__ == "__main__":
    unittest.main()


class TestAlphabeticalIndexIsNotASyllabus(unittest.TestCase):
    """REGRESSION. Wikibooks stores a book as sub-pages and the API returns them
    SORTED, so "Linear Algebra" comes back as Addition..., Any Matrix...,
    Augmented Matrices, Basis, ... Copying that produced a course whose modules
    were alphabetically-ordered sub-topics -- "Identity Matrix" as a module.

    It scored 100% on the keyword coverage instrument, which is precisely the
    blind spot that instrument documents about itself: presence is not sequence.
    Ordering is the pedagogy.
    """

    def test_the_real_wikibooks_listing_is_refused(self):
        alphabetical = [
            "Addition, Multiplication, and Transpose", "Any Matrix Represents a Linear Map",
            "Augmented Matrices", "Basis", "Basis Vectors", "Basis and Dimension",
            "Change of Basis", "Changing Map Representations", "Characteristic Equation",
            "Cofactors and Minors", "Column and Row Spaces", "Determinants",
        ]
        b = _b([{"book": "Linear Algebra", "relevance": 9.0, "source": "Wikibooks",
                 "url": "u", "chapters": alphabetical}])
        assert b._spine_from_syllabus("Linear Algebra", 6) is None

    def test_a_taught_order_is_accepted(self):
        """OpenStax books are ordered as taught, so they must still qualify."""
        taught = ["Whole Numbers", "The Language of Algebra", "Integers",
                  "Fractions", "Decimals", "Percents",
                  "The Properties of Real Numbers", "Solving Linear Equations"]
        b = _b([{"book": "Prealgebra", "relevance": 9.0, "source": "OpenStax",
                 "url": "u", "chapters": taught}])
        spine = b._spine_from_syllabus("Prealgebra", 4)
        assert spine is not None and len(spine) == 4

    def test_a_short_list_is_not_judged(self):
        from services.core.course_builder import _looks_alphabetical
        assert _looks_alphabetical(["A", "B", "C"]) is False
