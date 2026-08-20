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


def _pdf_with_toc(entries, n_pages):
    """A PDF whose pages carry identifiable text and whose ToC is `entries`.

    Every page holds ~2,300 chars, comfortably over MIN_CHAPTER_CHARS, so a
    section that comes back short did so because its PAGE RANGE was wrong and
    not because the fixture was thin.
    """
    import fitz
    doc = fitz.open()
    for i in range(1, n_pages + 1):
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(36, 36, 576, 756),
                            " ".join(f"page{i:03d}word{j:03d}" for j in range(260)),
                            fontsize=8)
    doc.set_toc([list(e) for e in entries])
    path = os.path.join(tempfile.mkdtemp(), "book.pdf")
    doc.save(path)
    doc.close()
    return path


class TestTocSpans(unittest.TestCase):
    """A table of contents is not guaranteed to move forwards.

    `end = entries[i + 1].page - 1` assumed it did. A reflowed textbook puts two
    sections on one page and the first of them got a page range that ran
    backwards — empty text, dropped by the length filter, and renumbered over by
    the survivors so the course looked complete.
    """

    def test_distinct_start_pages_are_unchanged(self):
        """The arithmetic was only ever wrong for ties and jumps backwards; the
        ordinary case must not move."""
        from services.research.book_reader import _toc_spans
        entries = [(1, "A", 1), (1, "B", 5), (1, "C", 9)]
        self.assertEqual(_toc_spans(entries, 12), [(1, 4), (5, 8), (9, 12)])

    def test_two_sections_on_one_page_both_get_that_page(self):
        from services.research.book_reader import _toc_spans
        entries = [(2, "4.3", 112), (2, "4.4", 112), (2, "4.5", 118)]
        spans = _toc_spans(entries, 200)
        self.assertEqual(spans[0], (112, 117))
        self.assertEqual(spans[1], (112, 117))

    def test_no_span_is_ever_empty(self):
        from services.research.book_reader import _toc_spans
        entries = [(1, "A", 7), (1, "B", 7), (1, "C", 7)]
        for first, last in _toc_spans(entries, 20):
            self.assertGreaterEqual(last, first)

    def test_a_backwards_entry_does_not_swallow_the_book(self):
        """An appendix bookmark pointing at an earlier page used to hand the
        entry before it a range covering everything after."""
        from services.research.book_reader import _toc_spans
        entries = [(1, "Ten", 10), (1, "Appendix", 5), (1, "Twenty", 20)]
        spans = _toc_spans(entries, 30)
        self.assertEqual(spans[0], (10, 19))
        self.assertEqual(spans[1], (5, 9))
        self.assertEqual(spans[2], (20, 30))

    def test_a_page_beyond_the_document_is_clamped(self):
        from services.research.book_reader import _toc_spans
        self.assertEqual(_toc_spans([(1, "A", 99)], 10), [(10, 10)])


class TestPdfTocSections(unittest.TestCase):
    def test_a_section_sharing_a_start_page_still_reaches_the_course(self):
        """The reproduction: §4.3 and §4.4 both begin on page 112."""
        toc = [[1, "Chapter 4: Membranes", 1],
               [2, "4.1 Structure", 1],
               [2, "4.2 Fluidity", 3],
               [2, "4.3 Osmosis", 5],
               [2, "4.4 Tonicity", 5],
               [2, "4.5 Transport", 7],
               [2, "4.6 Endocytosis", 9]]
        b = open_book(_pdf_with_toc(toc, 10))
        titles = [c.title for c in b.chapters]
        self.assertIn("4.3 Osmosis", titles)
        self.assertIn("4.4 Tonicity", titles)
        self.assertEqual(len(titles), 6)

    def test_the_shared_page_is_read_by_both_sections(self):
        toc = [[1, "Chapter 4", 1], [2, "4.1", 1], [2, "4.2", 3],
               [2, "4.3", 5], [2, "4.4", 5], [2, "4.5", 7], [2, "4.6", 9]]
        b = open_book(_pdf_with_toc(toc, 10))
        by_title = {c.title: c.text for c in b.chapters}
        self.assertIn("page005word001", by_title["4.3"])
        self.assertIn("page005word001", by_title["4.4"])

    def test_a_dropped_section_is_logged_not_silent(self):
        """`len(chapters) + 1` renumbers the survivors over the gap, so an
        unlogged drop cannot be noticed after the fact."""
        import logging as _logging
        toc = [[1, "Chapter 1", 1], [2, "1.1", 1], [2, "1.2", 2],
               [2, "1.3", 3], [2, "1.4", 4], [2, "1.5 Blank", 5],
               [2, "1.6", 6]]
        path = _pdf_with_toc(toc, 6)
        # Page 5 is emptied after the fixture is built, so exactly one section
        # falls under the length floor.
        import fitz
        doc = fitz.open(path)
        doc[4].clean_contents()
        doc[4].add_redact_annot(doc[4].rect)
        doc[4].apply_redactions()
        doc.saveIncr()
        doc.close()
        with self.assertLogs("services.research.book_reader", level="WARNING") as cm:
            b = open_book(path)
        self.assertTrue(any("1.5 Blank" in m for m in cm.output),
                        f"the dropped section must be named: {cm.output}")
        self.assertNotIn("1.5 Blank", [c.title for c in b.chapters])


class TestDegradedDigestsAreNotCached(unittest.TestCase):
    """A digester outage is transient; the cache is not.

    Writing the truncated fallback into `_DIGEST_CACHE` made one bad minute
    permanent for the process — every later lesson from that chapter, and every
    retry in the same run, read the same partial text without calling again.
    """

    def _book(self):
        return Book("B", [Chapter("One", "word " * 20000, 1)])

    def test_a_failed_digest_is_retried_not_remembered(self):
        from services.core.book_source import clear_digest_cache, digest_chapter
        clear_digest_cache()
        state = {"up": False}

        def flaky(prompt, **kw):
            if not state["up"]:
                raise RuntimeError("model down")
            return {"points": ["a teaching point long enough to keep here"]}

        b = self._book()
        self.assertEqual(digest_chapter(b, 1, flaky)[1], "truncated")
        state["up"] = True
        text, how = digest_chapter(b, 1, flaky)
        self.assertEqual(how, "digested", "the fallback must not be sticky")
        self.assertIn("teaching point", text)

    def test_a_partial_digest_is_not_cached_either(self):
        """Points from three chunks out of ten is still a degraded read."""
        from services.core.book_source import clear_digest_cache, digest_chapter
        clear_digest_cache()
        calls = {"n": 0, "fail_after": 2}

        def half(prompt, **kw):
            calls["n"] += 1
            if calls["n"] > calls["fail_after"]:
                raise RuntimeError("model down")
            return {"points": ["a teaching point long enough to keep here"]}

        b = self._book()
        self.assertEqual(digest_chapter(b, 1, half)[1], "digested")
        before = calls["n"]
        calls["fail_after"] = 10_000
        digest_chapter(b, 1, half)
        self.assertGreater(calls["n"], before,
                           "a digest missing chunks must be re-taken")

    def test_a_clean_digest_is_still_cached(self):
        from services.core.book_source import clear_digest_cache, digest_chapter
        clear_digest_cache()
        calls = []

        def fake(prompt, **kw):
            calls.append(1)
            return {"points": ["a teaching point long enough to keep here"]}

        b = self._book()
        digest_chapter(b, 1, fake)
        first = len(calls)
        digest_chapter(b, 1, fake)
        self.assertEqual(len(calls), first)


class TestConceptNamingTally(unittest.TestCase):
    """`named + skipped` is the only report a book build gives. A lesson that
    lost every concept was counted in neither, so the log read as a clean run
    while the course had a lesson with nothing in it."""

    def _course(self, slots=3):
        return {"title": "T", "modules": [{"units": [{"lessons": [
            {"title": "Chapter 1", "book_chapter": 1,
             "concepts": [{} for _ in range(slots)]}]}]}]}

    def _book(self):
        return Book("B", [Chapter("One", "word " * 400, 1)])

    def test_a_well_formed_but_unusable_response_counts_as_skipped(self):
        from services.core.book_source import attach_concepts
        course = self._course()
        # The shape the schema asks for, with the one key that makes an entry
        # usable missing from every item.
        res = attach_concepts(course, self._book(),
                              lambda **kw: {"concepts": [{"objectives": ["o"]},
                                                         {"objectives": ["o"]}]})
        self.assertEqual(res["named"], 0)
        self.assertEqual(res["skipped"], 3)

    def test_a_lesson_that_named_nothing_keeps_its_original_title(self):
        """Renaming the lesson after emptying it made the failure look like a
        successfully-read chapter."""
        from services.core.book_source import attach_concepts
        course = self._course()
        attach_concepts(course, self._book(),
                        lambda **kw: {"chapter_title": "Membranes",
                                      "concepts": [{"objectives": []}]})
        lesson = course["modules"][0]["units"][0]["lessons"][0]
        self.assertEqual(lesson["title"], "Chapter 1")

    def test_a_partial_response_counts_the_slots_it_left_empty(self):
        from services.core.book_source import attach_concepts
        course = self._course(slots=3)
        res = attach_concepts(course, self._book(),
                              lambda **kw: {"concepts": [{"title": "Osmosis"}]})
        self.assertEqual(res["named"], 1)
        self.assertEqual(res["skipped"], 2, "named + skipped must equal the slots")
        self.assertEqual(
            len(course["modules"][0]["units"][0]["lessons"][0]["concepts"]), 1)

    def test_a_clean_run_still_reports_clean(self):
        from services.core.book_source import attach_concepts
        course = self._course(slots=2)
        res = attach_concepts(course, self._book(),
                              lambda **kw: {"concepts": [{"title": "A"},
                                                         {"title": "B"}]})
        self.assertEqual((res["named"], res["skipped"]), (2, 0))
