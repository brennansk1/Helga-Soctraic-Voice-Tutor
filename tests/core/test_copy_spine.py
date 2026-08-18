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

    def test_merely_overlapping_book_is_never_copied(self):
        """College Algebra scored 4.75 against Linear Algebra's 4.67 before the
        ranking fix and pulled Probability into a linear algebra course. Copying
        the WRONG book's structure is worse than inventing one.

        Asserted on the SOURCE rather than on getting None: with the curated
        fallback in place the right answer may be a substitute spine, and a
        "returns None" assertion would forbid the better outcome."""
        b = _b([_outline("College Algebra", 4.75, 30)])
        spine = b._spine_from_syllabus("Linear Algebra", 6)
        if spine is not None:
            assert b._spine_source["book"] != "College Algebra"

    def test_overlapping_book_with_no_curated_fallback_is_refused(self):
        b = _b([_outline("Adjacent Field", 4.75, 30)])
        assert b._spine_from_syllabus("Obscure Subject", 6) is None

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

    def test_the_real_wikibooks_listing_is_never_the_spine(self):
        """The index must not become the spine. Since the curated-spine fallback
        was added it may be *replaced* rather than refused outright, so the
        assertion is about what the spine came FROM, not about getting None —
        the original "returns None" form was too strong and would have blocked
        the better answer."""
        alphabetical = [
            "Addition, Multiplication, and Transpose", "Any Matrix Represents a Linear Map",
            "Augmented Matrices", "Basis", "Basis Vectors", "Basis and Dimension",
            "Change of Basis", "Changing Map Representations", "Characteristic Equation",
            "Cofactors and Minors", "Column and Row Spaces", "Determinants",
        ]
        b = _b([{"book": "Linear Algebra", "relevance": 9.0, "source": "Wikibooks",
                 "url": "u", "chapters": alphabetical}])
        spine = b._spine_from_syllabus("Linear Algebra", 6)
        if spine is not None:
            assert getattr(b, "_spine_source", {}).get("source") != "Wikibooks"
            titles = [m["title"] for m in spine]
            assert "Identity Matrix" not in titles

    def test_an_index_with_no_curated_fallback_is_refused_outright(self):
        alphabetical = sorted(["Alpha topic", "Beta topic", "Delta topic",
                               "Epsilon topic", "Gamma topic", "Omega topic"])
        b = _b([{"book": "Obscure Subject", "relevance": 9.0, "source": "Wikibooks",
                 "url": "u", "chapters": alphabetical}])
        assert b._spine_from_syllabus("Obscure Subject", 4) is None

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


class TestPrefersASequencedSource(unittest.TestCase):
    """Relevance says whether a book is ABOUT the subject; it says nothing about
    whether its chapter list is in teaching order. Picking by relevance alone
    selected the Wikibooks index (highest score, alphabetical) over a sequenced
    book, and produced modules running Addition..., Cofactors..., Diagonal
    Matrix. Ordering is the part we cannot reconstruct, so it wins."""

    def test_lower_scoring_sequenced_book_beats_higher_scoring_index(self):
        alphabetical = sorted(["Augmented Matrices", "Basis", "Change of Basis",
                               "Cofactors", "Determinants", "Eigenvalues",
                               "Identity Matrix", "Inverses"])
        b = _b([
            {"book": "Linear Algebra", "relevance": 9.0, "source": "Wikibooks",
             "url": "u", "chapters": alphabetical},
            {"book": "Linear Algebra", "relevance": 7.0, "source": "OpenStax",
             "url": "u", "chapters": _TAUGHT_ORDER},
        ])
        spine = b._spine_from_syllabus("Linear Algebra", 5)
        assert spine is not None
        assert b._spine_source["source"] == "OpenStax", b._spine_source

    def test_all_sources_alphabetical_falls_back_to_generation(self):
        alphabetical = sorted(["Alpha", "Beta", "Delta", "Epsilon", "Gamma",
                               "Omega", "Sigma", "Theta"])
        b = _b([{"book": "X", "relevance": 9.0, "source": "Wikibooks",
                 "url": "u", "chapters": alphabetical}])
        assert b._spine_from_syllabus("X", 4) is None


class TestCuratedSpine(unittest.TestCase):
    """Some subjects have no machine-readable teaching order anywhere. Linear
    algebra has no OpenStax title and its Wikibooks entry is an alphabetical
    index, so research returns complete coverage with no sequence. A chapter list
    transcribed from a published textbook supplies the part that cannot be
    reconstructed from an index."""

    def test_curated_spine_is_found_by_alias(self):
        from services.core.course_builder import _curated_spine
        for name in ("Linear Algebra", "linear algebra", "Matrix Algebra"):
            c = _curated_spine(name)
            assert c is not None, name
            assert c["chapters"][0] == "Introduction to Vectors"

    def test_unknown_subject_has_no_curated_spine(self):
        from services.core.course_builder import _curated_spine
        assert _curated_spine("Underwater Basket Weaving") is None

    def test_curated_spine_is_used_when_research_only_finds_an_index(self):
        alphabetical = sorted(["Augmented Matrices", "Basis", "Change of Basis",
                               "Cofactors", "Determinants", "Eigenvalues",
                               "Identity Matrix", "Inverses"])
        b = _b([{"book": "Linear Algebra", "relevance": 9.0, "source": "Wikibooks",
                 "url": "u", "chapters": alphabetical}])
        spine = b._spine_from_syllabus("Linear Algebra", 6)
        assert spine is not None, "curated spine was not consulted"
        assert b._spine_source["source"] == "curated"
        titles = [m["title"] for m in spine]
        assert titles[0] == "Introduction to Vectors"

    def test_curated_spine_is_a_last_resort_not_a_default(self):
        """A real sequenced source from research must still win — the curated
        file is a fallback for a gap, not a preferred answer."""
        b = _b([{"book": "Linear Algebra", "relevance": 9.0, "source": "OpenStax",
                 "url": "u", "chapters": _TAUGHT_ORDER}])
        b._spine_from_syllabus("Linear Algebra", 6)
        assert b._spine_source["source"] == "OpenStax"


class TestGroupingNotSampling(unittest.TestCase):
    """Reducing a chapter list to a module count must GROUP, not sample.

    REGRESSION: Strang's 12 chapters sampled into 6 modules produced
    Introduction to Vectors, Vector Spaces, Determinants, SVD, Complex Vectors,
    Numerical LA -- dropping Solving Linear Equations, ORTHOGONALITY,
    Eigenvalues and Applications. Half the book, including the exact cluster
    (least squares, projections, Gram-Schmidt) whose absence started this work.
    """

    def test_every_chapter_survives_the_reduction(self):
        chapters = [f"{t} chapter" for t in _TAUGHT_ORDER]   # 16, sequenced
        b = _b([{"book": "Src", "relevance": 9.0, "source": "OpenStax",
                 "url": "u", "chapters": chapters}])
        b._spine_from_syllabus("Src", 6)
        assert b._spine_source["chapters_covered"] == len(chapters), \
            "chapters were dropped instead of grouped"

    def test_dropped_chapters_appear_in_the_module_scope(self):
        """A grouped chapter must still be visible to the substructure builder,
        or it is dropped in practice even though it was 'covered'."""
        chapters = ["Vectors", "Solving Systems", "Vector Spaces",
                    "Orthogonality", "Determinants", "Eigenvalues"]
        b = _b([{"book": "Src", "relevance": 9.0, "source": "OpenStax",
                 "url": "u", "chapters": chapters}])
        spine = b._spine_from_syllabus("Src", 3)
        scopes = " ".join(m["scope"] for m in spine)
        for ch in chapters:
            assert ch in scopes, f"{ch} vanished from the spine"

    def test_order_is_preserved(self):
        chapters = ["Vectors", "Solving Systems", "Vector Spaces",
                    "Orthogonality", "Determinants", "Eigenvalues"]
        b = _b([{"book": "Src", "relevance": 9.0, "source": "OpenStax",
                 "url": "u", "chapters": chapters}])
        titles = [m["title"] for m in b._spine_from_syllabus("Src", 3)]
        assert titles == ["Vectors", "Vector Spaces", "Determinants"]


class TestSectionDetailReachesTheBuilder(unittest.TestCase):
    """Section titles are where the specifics live. "Eigenvalues and
    Eigenvectors" as a module title says nothing about symmetric or
    positive-definite matrices; its SECTIONS name both, and that was the last
    area still missing coverage against MIT 18.06."""

    def test_grouped_chapters_carry_their_sections(self):
        from services.core.course_builder import SkeletonBuilder
        b = SkeletonBuilder.__new__(SkeletonBuilder)
        b.status_callback = None
        b._syllabus_outlines = []
        scopes = " ".join(m["scope"] for m
                          in b._spine_from_syllabus("Linear Algebra", 6))
        for term in ("Symmetric Matrices", "Positive Definite Matrices",
                     "Least Squares Approximations", "Gram-Schmidt"):
            assert term in scopes, f"{term} never reached the builder"

    def test_no_research_at_all_still_reaches_the_curated_spine(self):
        """REGRESSION: an early `if not outlines: return None` skipped the
        curated fallback entirely — and "research found nothing" is exactly when
        it is most useful."""
        from services.core.course_builder import SkeletonBuilder
        b = SkeletonBuilder.__new__(SkeletonBuilder)
        b.status_callback = None
        b._syllabus_outlines = []
        assert b._spine_from_syllabus("Linear Algebra", 6) is not None

    def test_unknown_subject_with_no_research_still_returns_none(self):
        from services.core.course_builder import SkeletonBuilder
        b = SkeletonBuilder.__new__(SkeletonBuilder)
        b.status_callback = None
        b._syllabus_outlines = []
        assert b._spine_from_syllabus("Underwater Basket Weaving", 6) is None
