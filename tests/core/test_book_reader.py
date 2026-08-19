"""Reading a book of any shape, and shaping a course like it.

Every threshold here was set against real books — a 1486-page OpenStax PDF and
Gutenberg's Pride and Prejudice in EPUB and plain text — not against fixtures.
"""

import os
import sys
import tempfile
import unittest
import zipfile

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.core.book_skeleton import (  # noqa: E402
    build_structure, choose_shape, summarise)
from services.core.book_source import parse_concepts, passage_for  # noqa: E402
from services.research.book_reader import Book, Chapter, open_book  # noqa: E402


def _chapters(n, words=500, part=None, start=1):
    return [Chapter(f"Chapter {i}", ("word " * words), start + i - 1, part=part)
            for i in range(1, n + 1)]


class TestShapeAdapts(unittest.TestCase):
    """A self-help book has chapters and no parts; a textbook has both. Forcing
    one ladder onto every book produces invented scaffolding around real
    content."""

    def test_parts_become_units_not_modules(self):
        """A module is a pedagogical division a curriculum designer chose. A
        novel or a self-help book has none, so the container module is a shell
        and the level that adapts is the UNIT."""
        chs = _chapters(4, part="Part One") + _chapters(4, part="Part Two", start=5)
        s = choose_shape(Book("B", chs))
        self.assertEqual(s["shape"], "parts_as_units")
        self.assertEqual(s["units"], 2)
        self.assertEqual(s["modules"], 1)

    def test_many_short_chapters_become_a_flat_lesson_list(self):
        s = choose_shape(Book("Novel", _chapters(30, words=800)))
        self.assertEqual(s["shape"], "chapters_as_lessons")
        self.assertEqual(s["lessons"], 30, "one lesson per chapter")

    def test_long_chapters_do_not_become_several_lessons(self):
        """ONE CHAPTER IS ONE LESSON, ALWAYS.

        An earlier version split long chapters across two to four lessons "for
        balance", inventing boundaries the author did not put there — in the one
        place where the author's boundaries are exactly what building from a
        book is meant to preserve. Length buys CONCEPTS instead.
        """
        book = Book("Treatise", _chapters(8, words=9000))
        c = build_structure(book)
        lessons = [l for m in c["modules"] for u in m["units"]
                   for l in u["lessons"]]
        self.assertEqual(len(lessons), 8)
        self.assertGreaterEqual(len(lessons[0]["concepts"]), 5,
                                "a long chapter earns more concepts")

    def test_every_shape_decision_explains_itself(self):
        for book in (Book("A", _chapters(30)), Book("B", _chapters(6, words=9000))):
            self.assertTrue(choose_shape(book)["why"])


class TestOneLessonPerChapter(unittest.TestCase):
    """The invariant. The chapter is the author's unit of teaching and the
    lesson is ours; that correspondence is the point of building from a book."""

    def _lessons(self, book):
        c = build_structure(book)
        return [l for m in c["modules"] for u in m["units"] for l in u["lessons"]]

    def test_a_novel(self):
        self.assertEqual(len(self._lessons(Book("N", _chapters(59, words=900)))), 59)

    def test_a_long_textbook(self):
        self.assertEqual(len(self._lessons(Book("T", _chapters(12, words=9000)))), 12)

    def test_with_parts(self):
        chs = (_chapters(4, part="Part One")
               + _chapters(4, part="Part Two", start=5))
        self.assertEqual(len(self._lessons(Book("B", chs))), 8)

    def test_a_very_short_book(self):
        self.assertEqual(len(self._lessons(Book("S", _chapters(3)))), 3)

    def test_concept_count_scales_with_chapter_length(self):
        short = self._lessons(Book("A", _chapters(6, words=600)))
        long = self._lessons(Book("B", _chapters(6, words=12000)))
        self.assertLess(len(short[0]["concepts"]), len(long[0]["concepts"]))


class TestStructure(unittest.TestCase):
    def test_each_lesson_links_back_to_its_chapter(self):
        """The link that lets hydration READ rather than recall."""
        c = build_structure(Book("N", _chapters(12)))
        lessons = [l for m in c["modules"] for u in m["units"]
                   for l in u["lessons"]]
        self.assertEqual(len(lessons), 12)
        self.assertEqual([l["book_chapter"] for l in lessons], list(range(1, 13)))

    def test_parts_produce_one_unit_each_under_a_container_module(self):
        chs = _chapters(3, part="Part One") + _chapters(3, part="Part Two", start=4)
        c = build_structure(Book("B", chs))
        self.assertEqual(len(c["modules"]), 1)
        self.assertTrue(c["modules"][0]["container_only"])
        units = c["modules"][0]["units"]
        self.assertEqual([u["title"] for u in units], ["Part One", "Part Two"])

    def test_chapters_before_the_first_part_are_not_lost(self):
        """Front matter chapters precede the first part heading and would
        otherwise vanish from the course entirely."""
        chs = (_chapters(2)
               + _chapters(3, part="Part One", start=3)
               + _chapters(3, part="Part Two", start=6))
        c = build_structure(Book("B", chs))
        units = c["modules"][0]["units"]
        self.assertEqual(units[0]["title"], "Introduction")
        self.assertEqual(len(units), 3)

    def test_concept_slots_start_empty(self):
        """The book decides what its chapters teach. Inventing concept names
        before reading is how a course gets headings the source cannot support."""
        c = build_structure(Book("N", _chapters(3)))
        con = c["modules"][0]["units"][0]["lessons"][0]["concepts"]
        self.assertTrue(all(x["title"] == "" for x in con))
        self.assertTrue(all(x["from_book"] for x in con))

    def test_summary_counts(self):
        s = summarise(build_structure(Book("N", _chapters(10))))
        self.assertEqual(s["lessons"], 10)
        self.assertEqual(s["chapters_linked"], 10)


class TestChapterTitles(unittest.TestCase):
    """"Chapter I" tells a learner nothing about what they are about to study."""

    def test_a_bare_heading_is_recognised(self):
        from services.core.book_source import needs_a_title
        for t in ("Chapter I", "CHAPTER XXIII.", "", "Section 4"):
            self.assertTrue(needs_a_title(t), t)

    def test_an_authored_title_is_recognised(self):
        from services.core.book_source import needs_a_title
        for t in ("The Cell Membrane", "Chapter 4 — Enzymes"):
            self.assertFalse(needs_a_title(t), t)

    def test_a_bare_heading_gains_a_subject(self):
        from services.core.book_source import compose_title
        self.assertEqual(compose_title(2, "Chapter II", "Mr Bennet Visits"),
                         "Chapter 2 — Mr Bennet Visits")

    def test_an_authored_title_is_kept(self):
        """The author's words beat ours where the author supplied them."""
        from services.core.book_source import compose_title
        self.assertEqual(compose_title(4, "The Cell Membrane", "Something Else"),
                         "Chapter 4 — The Cell Membrane")

    def test_a_bare_heading_with_no_subject_survives(self):
        from services.core.book_source import compose_title
        self.assertEqual(compose_title(7, "Chapter VII", None), "Chapter VII")


class TestReadTool(unittest.TestCase):
    def test_a_short_chapter_is_returned_whole(self):
        b = Book("B", [Chapter("One", "short text here", 1)])
        self.assertEqual(passage_for(b, 1, "anything"), "short text here")

    def test_a_missing_chapter_returns_nothing_not_another_chapter(self):
        """Material from the wrong chapter reads as authoritative and is not
        what the lesson is about."""
        b = Book("B", [Chapter("One", "text", 1)])
        self.assertEqual(passage_for(b, 99, "x"), "")

    def test_a_long_chapter_is_capped(self):
        b = Book("B", [Chapter("One", "word " * 5000, 1)])
        self.assertLessEqual(len(passage_for(b, 1, "word")), 7200)

    def test_chunks_overlap_so_nothing_falls_between_them(self):
        ch = Chapter("One", "x" * 20000, 1)
        chunks = ch.chunks(size=6000, overlap=400)
        self.assertGreater(len(chunks), 2)
        self.assertGreater(sum(len(c) for c in chunks), 20000)


class TestConceptParsing(unittest.TestCase):
    def test_structured_concepts(self):
        got = parse_concepts({"concepts": [
            {"title": "Social Assumption of Marriage", "objectives": ["a", "b"]},
            {"title": "Contrast in Temperament"}]}, 3)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["objectives"], ["a", "b"])

    def test_under_filling_is_not_padded(self):
        """A chapter that carries two ideas should not be made to carry four."""
        got = parse_concepts({"concepts": [{"title": "One"}]}, 4)
        self.assertEqual(len(got), 1)

    def test_junk_yields_nothing(self):
        self.assertEqual(parse_concepts(None, 3), [])
        self.assertEqual(parse_concepts({"concepts": [{}]}, 3), [])


class TestFormats(unittest.TestCase):
    def test_a_text_book_parses(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "b.txt")
        with open(p, "w") as f:
            for i in range(1, 7):
                f.write(f"\nCHAPTER {i}\n\n" + ("sentence here. " * 200) + "\n")
        b = open_book(p)
        self.assertIsNotNone(b)
        self.assertGreaterEqual(len(b.chapters), 5)

    def test_gutenberg_boilerplate_is_not_a_chapter(self):
        """Measured on the real Pride and Prejudice: the START OF THE PROJECT
        GUTENBERG marker sits in the text body and was parsed as chapter one."""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "b.txt")
        with open(p, "w") as f:
            f.write("*** START OF THE PROJECT GUTENBERG EBOOK 1342 ***\n"
                    + ("boilerplate " * 300) + "\n")
            for i in range(1, 5):
                f.write(f"\nCHAPTER {i}\n\n" + ("real content here. " * 200) + "\n")
        b = open_book(p)
        self.assertTrue(all("GUTENBERG" not in c.title.upper() for c in b.chapters))

    def test_an_unsupported_format_returns_none(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "b.docx")
        open(p, "w").write("x")
        self.assertIsNone(open_book(p))

    def test_a_missing_file_returns_none(self):
        self.assertIsNone(open_book("/nonexistent/book.epub"))
