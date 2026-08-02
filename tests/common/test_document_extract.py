"""A3 tests: document extraction must actually read the document.

Guards the specific defect this replaced — EPUB upload derived a topic from the
FILENAME and never opened the file, so a user's book produced a generic course
about its own name.
"""

import os
import sys
import tempfile
import unittest
import zipfile

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if _root not in sys.path:
    sys.path.insert(0, _root)

from services.common.document_extract import (  # noqa: E402
    extract, extract_epub, extract_text_file,
    UnsupportedDocument, ExtractionFailed, summarize_source,
)

CHAP1 = """<html><body>
<h1>Chapter One</h1>
<p>The mitochondrion is the powerhouse of the cell.</p>
<script>console.log('noise')</script>
<style>.x{color:red}</style>
</body></html>"""

CHAP2 = """<html><body><h1>Chapter Two</h1>
<p>Ribosomes synthesise proteins from messenger RNA.</p></body></html>"""

CONTAINER = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf">
  <manifest>
    <item id="c1" href="chap1.xhtml"/>
    <item id="c2" href="chap2.xhtml"/>
  </manifest>
  <spine><itemref idref="c2"/><itemref idref="c1"/></spine>
</package>"""


def _make_epub(path, with_opf=True):
    with zipfile.ZipFile(path, "w") as z:
        if with_opf:
            z.writestr("META-INF/container.xml", CONTAINER)
            z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/chap1.xhtml", CHAP1)
        z.writestr("OEBPS/chap2.xhtml", CHAP2)


class TestEpubExtraction(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "biology_101.epub")
        _make_epub(self.path)

    def test_extracts_actual_book_content_not_the_filename(self):
        text = extract(self.path)
        self.assertIn("mitochondrion", text)
        self.assertIn("Ribosomes", text)
        self.assertNotIn(
            "biology_101", text,
            "the filename must not be what gets taught — that was the bug"
        )

    def test_respects_spine_reading_order(self):
        text = extract_epub(self.path)
        self.assertLess(
            text.index("Ribosomes"), text.index("mitochondrion"),
            "spine lists chapter two first; reading order must be honoured"
        )

    def test_strips_script_and_style_noise(self):
        text = extract_epub(self.path)
        self.assertNotIn("console.log", text)
        self.assertNotIn("color:red", text)

    def test_falls_back_when_opf_is_missing(self):
        p = os.path.join(self.dir, "no_opf.epub")
        _make_epub(p, with_opf=False)
        text = extract_epub(p)
        self.assertIn("mitochondrion", text)

    def test_max_chars_truncates(self):
        self.assertLessEqual(len(extract_epub(self.path, max_chars=40)), 40)

    def test_bad_zip_is_reported_not_silently_empty(self):
        p = os.path.join(self.dir, "broken.epub")
        with open(p, "w") as f:
            f.write("this is not a zip")
        with self.assertRaises(ExtractionFailed):
            extract_epub(p)

    def test_empty_epub_raises_rather_than_returning_nothing(self):
        p = os.path.join(self.dir, "empty.epub")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("OEBPS/blank.xhtml", "<html><body></body></html>")
        with self.assertRaises(ExtractionFailed):
            extract_epub(p)

    def test_missing_file_raises(self):
        with self.assertRaises(ExtractionFailed):
            extract_epub(os.path.join(self.dir, "nope.epub"))


class TestPlainText(unittest.TestCase):
    def test_reads_markdown_and_text(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "notes.md")
        with open(p, "w") as f:
            f.write("# Notes\nEnzymes lower activation energy.")
        self.assertIn("activation energy", extract(p))

    def test_empty_file_raises(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "empty.txt")
        open(p, "w").close()
        with self.assertRaises(ExtractionFailed):
            extract_text_file(p)


class TestUnsupportedIsHonest(unittest.TestCase):
    def test_pdf_raises_rather_than_pretending(self):
        """No PDF parser is installed. Returning empty text while reporting
        success is exactly the bug this module exists to fix."""
        with self.assertRaises(UnsupportedDocument) as ctx:
            extract("/tmp/whatever.pdf")
        self.assertIn("pdf", str(ctx.exception).lower())

    def test_unknown_type_raises(self):
        with self.assertRaises(UnsupportedDocument):
            extract("/tmp/thing.xyz")


class TestSummary(unittest.TestCase):
    def test_summary_reports_word_count(self):
        self.assertIn("words extracted", summarize_source("a b c"))


if __name__ == '__main__':
    unittest.main()
