"""Figures extracted from an uploaded EPUB/PDF, and the review that filters them.

The review is the point. Most images embedded in a book are not figures — a
publisher logo on every page, a chapter rule, a bullet glyph — and handing
those to a learner is worse than having no images, because each one occupies
the slot a real figure would have had.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from services.common.document_figures import (      # noqa: E402
    figures_from_document, parse_caption, review,
)
from tests.fixtures.make_book_fixtures import build  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@unittest.skipUnless(HAVE_PIL, "fixture generation needs Pillow")
class _Books(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="helga_figs_")
        cls.books = build(cls.dir)


class TestCaptions(unittest.TestCase):
    """A real caption is the strongest positive signal AND free alt-text —
    the author's own words, no LLM, no hallucination risk."""

    def test_common_caption_forms(self):
        for text, label in [
            ("Figure 1.1: A right triangle.", "Figure 1.1"),
            ("Fig. 2.3 — Radius and circumference.", "Fig 2.3"),
            ("FIGURE 12.4. Cell division.", "Figure 12.4"),
            ("Plate IV: The Antikythera mechanism.", "Plate IV"),
            ("Table 2.1 Comparison of methods", "Table 2.1"),
        ]:
            got, caption = parse_caption(text)
            self.assertEqual(got, label, text)
            self.assertTrue(caption)

    def test_prose_without_a_label_still_yields_context(self):
        label, caption = parse_caption("The diagram above shows a triangle.")
        self.assertEqual(label, "")
        self.assertTrue(caption)

    def test_empty_input_is_safe(self):
        self.assertEqual(parse_caption(""), ("", ""))
        self.assertEqual(parse_caption(None), ("", ""))


class TestEpubFigures(_Books):
    def test_real_figures_are_kept_with_their_captions(self):
        kept, _ = figures_from_document(self.books["epub"])
        self.assertEqual(len(kept), 2)
        labels = sorted(f["label"] for f in kept)
        self.assertEqual(labels, ["Fig 2.3", "Figure 1.1"])
        self.assertIn("right triangle", kept[0]["caption"].lower() + kept[1]["caption"].lower())

    def test_page_furniture_is_rejected_with_reasons(self):
        kept, rejected = figures_from_document(self.books["epub"])
        reasons = " ".join(r["reason"] for r in rejected)
        self.assertIn("page furniture", reasons)       # the repeated logo
        self.assertIn("aspect", reasons)               # the horizontal rule
        self.assertIn("too small", reasons)            # the bullet glyph
        self.assertTrue(all("logo" not in f["name"] for f in kept))

    def test_line_art_is_not_rejected_for_being_small_on_disk(self):
        """Regression: a real 520x400 figure compressed to 2,939 bytes and was
        filtered as 'too small'. Clean line art is exactly what a textbook
        figure IS, so a byte threshold rejects the best figures and keeps the
        ornaments. Dimensions decide, not file size."""
        kept, _ = figures_from_document(self.books["epub"])
        small = [f for f in kept if f["bytes"] < 3000]
        self.assertTrue(small, "the compact line-art figure was filtered out again")

    def test_a_caption_outranks_an_uncaptioned_image(self):
        kept, _ = figures_from_document(self.books["epub"])
        self.assertTrue(all(f["score"] >= 10 for f in kept))


class TestPdfFigures(_Books):
    def setUp(self):
        if not self.books.get("pdf"):
            self.skipTest("reportlab unavailable — cannot build a PDF fixture")

    def test_figures_are_found_with_page_captions(self):
        kept, _ = figures_from_document(self.books["pdf"])
        self.assertEqual(len(kept), 2)
        self.assertEqual(sorted(f["label"] for f in kept), ["Fig 2.3", "Figure 1.1"])

    def test_the_repeated_logo_is_rejected(self):
        kept, rejected = figures_from_document(self.books["pdf"])
        self.assertTrue(any("page furniture" in r["reason"] for r in rejected))

    def test_location_records_the_page(self):
        kept, _ = figures_from_document(self.books["pdf"])
        self.assertTrue(all(f["location"].startswith("page ") for f in kept))

    def test_pdf_text_extraction_now_works(self):
        """It was advertised in /library and raised UnsupportedDocument."""
        from services.common.document_extract import extract
        text = extract(self.books["pdf"])
        self.assertIn("Right Triangles", text)
        self.assertIn("[[page:1]]", text)          # page mapping for figures

    def test_a_scanned_pdf_says_so_instead_of_returning_nothing(self):
        from services.common.document_extract import ExtractionFailed, extract_pdf
        with patch("pypdf.PdfReader") as reader:
            reader.return_value.is_encrypted = False
            page = type("P", (), {"extract_text": lambda self: ""})()
            reader.return_value.pages = [page]
            with self.assertRaises(ExtractionFailed) as ctx:
                extract_pdf(self.books["pdf"])
        self.assertIn("OCR", str(ctx.exception))


class TestReviewSafety(unittest.TestCase):
    def test_review_of_nothing_is_not_an_error(self):
        self.assertEqual(review([]), ([], []))

    def test_unknown_format_is_rejected(self):
        kept, rejected = review([{
            "name": "x", "bytes": 99_000, "format": "", "width": 0, "height": 0,
            "digest": "d", "label": "", "caption": "", "context": "",
            "location": "", "data": b"not an image"}])
        self.assertEqual(kept, [])
        self.assertIn("format", rejected[0]["reason"])

    def test_unsupported_document_types_return_empty(self):
        self.assertEqual(figures_from_document("/tmp/nope.docx"), ([], []))
        self.assertEqual(figures_from_document("/tmp/missing.epub"), ([], []))


if __name__ == "__main__":
    unittest.main()
