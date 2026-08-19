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

    def test_an_authored_title_is_kept_UNCHANGED(self):
        """A title the author wrote needs nothing from us.

        An earlier version prefixed any title lacking a digit, which broke
        textbooks: the section "The Process of Science" became "Chapter 1 — The
        Process of Science" though it is a SECTION of chapter 1, and the next
        section of the same chapter became "Chapter 2 — ...". The number was
        the leaf ordinal, so the titles asserted a structure the book lacks.
        """
        from services.core.book_source import compose_title
        for orig in ("The Cell Membrane", "Water", "The Process of Science"):
            self.assertEqual(compose_title(4, orig, "Something Else"), orig)

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


class TestTextbookLadder(unittest.TestCase):
    """A textbook's table of contents is a ladder, not a list.

    MEASURED on a real OpenStax biology export: 19 level-2 CHAPTERS over 86
    level-3 SECTIONS. Flattening them made a chapter and one of its own sections
    siblings.
    """

    def _textbook(self):
        chs = []
        for ch in range(1, 4):
            for sec in range(1, 4):
                chs.append(Chapter(f"Section {ch}.{sec}", "word " * 900,
                                   len(chs) + 1, part=f"Chapter {ch}: Topic {ch}",
                                   level=3))
        return Book("Text", chs)

    def test_a_hierarchical_book_is_detected(self):
        self.assertTrue(self._textbook().hierarchical)

    def test_a_flat_book_is_not(self):
        flat = Book("Novel", [Chapter(f"Chapter {i}", "word " * 900, i)
                              for i in range(1, 12)])
        self.assertFalse(flat.hierarchical)

    def test_chapters_become_modules_and_sections_lessons(self):
        c = build_structure(self._textbook())
        self.assertEqual(len(c["modules"]), 3)
        self.assertEqual(c["modules"][0]["title"], "Chapter 1: Topic 1")
        lessons = [l for m in c["modules"] for u in m["units"]
                   for l in u["lessons"]]
        self.assertEqual(len(lessons), 9)

    def test_a_novel_still_gets_no_modules(self):
        """The textbook ladder must not leak into a flat book."""
        flat = Book("Novel", [Chapter(f"Chapter {i}", "word " * 900, i)
                              for i in range(1, 12)])
        c = build_structure(flat)
        self.assertEqual(len(c["modules"]), 1)
        self.assertTrue(c["modules"][0]["container_only"])

    def test_one_lesson_per_section_still_holds(self):
        book = self._textbook()
        c = build_structure(book)
        lessons = [l for m in c["modules"] for u in m["units"]
                   for l in u["lessons"]]
        self.assertEqual(len(lessons), len(book.chapters))


class TestFrontMatter(unittest.TestCase):
    """A leading block that is front matter wearing a chapter's title.

    MEASURED on Gutenberg's Art of War: the header, translator's introduction
    and table of contents arrive as one 14,000-word block, and the split titled
    it from the LAST ToC line inside it — "Chapter XIII. The Use of Spies". The
    course then began at the book's final chapter, which for a course built to
    follow a book's order is the worst possible failure.
    """

    def _book(self, titles):
        from services.research.book_reader import _drop_front_matter
        return _drop_front_matter(
            [Chapter(t, "word " * 400, i + 1) for i, t in enumerate(titles)])

    def test_an_out_of_order_leading_chapter_is_dropped(self):
        kept = self._book(["Chapter XIII. The Use of Spies", "Chapter I. Laying Plans",
                           "Chapter II. Waging War", "Chapter III. Stratagem"])
        self.assertEqual(len(kept), 3)
        self.assertTrue(kept[0].title.startswith("Chapter I."))

    def test_order_is_renumbered_after_dropping(self):
        kept = self._book(["Chapter IX. Later", "Chapter I. A", "Chapter II. B",
                           "Chapter III. C"])
        self.assertEqual([c.order for c in kept], [1, 2, 3])

    def test_a_correctly_ordered_book_is_untouched(self):
        titles = ["Chapter I. A", "Chapter II. B", "Chapter III. C", "Chapter IV. D"]
        self.assertEqual(len(self._book(titles)), 4)

    def test_untitled_chapters_are_never_dropped(self):
        """Only an arithmetic contradiction justifies dropping; absence of a
        number is not evidence of anything."""
        self.assertEqual(len(self._book(["Preface", "Opening", "Middle", "End"])), 4)

    def test_a_very_short_book_is_left_alone(self):
        self.assertEqual(len(self._book(["Chapter V. X", "Chapter I. Y"])), 2)


class TestAdaptiveDigestion(unittest.TestCase):
    """Chapters are not the same size. Measured: Pride and Prejudice runs a
    median of 11k chars and a max of 31k; OpenStax biology 14k and 36k. The
    original 12,000-char cap truncated the MEDIAN chapter of both books."""

    def test_a_normal_chapter_is_read_whole(self):
        from services.core.book_source import digest_chapter
        b = Book("B", [Chapter("One", "word " * 3000, 1)])
        text, how = digest_chapter(b, 1)
        self.assertEqual(how, "whole")

    def test_an_oversized_chapter_without_a_digester_says_it_truncated(self):
        """A thin result must be attributable rather than mysterious."""
        from services.core.book_source import digest_chapter
        b = Book("B", [Chapter("One", "word " * 20000, 1)])
        text, how = digest_chapter(b, 1)
        self.assertEqual(how, "truncated")

    def test_an_oversized_chapter_is_digested_in_reading_order(self):
        from services.core.book_source import clear_digest_cache, digest_chapter
        clear_digest_cache()
        calls = []

        def fake(prompt, **kw):
            calls.append(prompt)
            n = len(calls)
            # Long enough to clear the >20-char filter that drops fragments.
            return {"points": [f"point {n}a is a full teaching point here",
                               f"point {n}b is a full teaching point here"]}

        b = Book("B", [Chapter("One", "word " * 20000, 1)])
        text, how = digest_chapter(b, 1, fake)
        self.assertEqual(how, "digested")
        self.assertGreater(len(calls), 1, "a long chapter needs several passes")
        self.assertLess(text.index("point 1a"), text.index("point 2a"))

    def test_the_digest_is_cached(self):
        """One call per 14k chars is 150 s for a 115k-char chapter, and both the
        prompt builder and the caller ask for it."""
        from services.core.book_source import clear_digest_cache, digest_chapter
        clear_digest_cache()
        calls = []

        def fake(prompt, **kw):
            calls.append(1)
            return {"points": ["a point that is long enough to keep"]}

        b = Book("B", [Chapter("One", "word " * 20000, 1)])
        digest_chapter(b, 1, fake)
        first = len(calls)
        digest_chapter(b, 1, fake)
        self.assertEqual(len(calls), first, "second call must be cached")

    def test_a_missing_chapter_is_reported_not_guessed(self):
        from services.core.book_source import digest_chapter
        self.assertEqual(digest_chapter(Book("B", []), 9)[1], "missing")


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
