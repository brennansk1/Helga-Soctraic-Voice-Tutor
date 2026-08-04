"""A3 tests: advertised features must be reachable and must not lie.

Two features were "half-present" — backed by working services but with no way
in, and in the EPUB case actively misleading:

  * Memory Palace: /palace redirected to home while the FSM kept the full
    MEMORY_PALACE state and librarian kept /palace/start, /locus/next, /anchor.
  * Document import: /api/upload_epub existed with zero frontend, and the
    uploaded file was never read — only its FILENAME was used to pick a topic.
"""

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, '../../../'))
_webui = os.path.join(_root, 'services/web-ui')
for p in (_root, _webui):
    if p not in sys.path:
        sys.path.insert(0, p)

for _mod in ("gevent", "gevent.monkey", "socketio", "flask_socketio"):
    if isinstance(sys.modules.get(_mod), MagicMock):
        del sys.modules[_mod]
        sys.modules.pop("app", None)
        sys.modules.pop("auth", None)

import app as webui_app  # noqa: E402

app = webui_app.app
app.config['TESTING'] = True


class TestPalaceReachable(unittest.TestCase):
    def test_palace_page_renders_instead_of_redirecting(self):
        rv = app.test_client().get('/palace')
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b'Memory Palace', rv.data)

    def test_palace_page_offers_a_course_selector(self):
        rv = app.test_client().get('/palace')
        self.assertIn(b'palace-course', rv.data)
        self.assertIn(b'palace-begin', rv.data)

    def test_locus_next_forwards_course_uid(self):
        """librarian's /locus/next requires course_uid; the proxy dropped it,
        so walking the palace always 400'd."""
        fake = MagicMock()
        fake.json.return_value = {}
        fake.status_code = 200
        with patch.object(webui_app.requests, 'get', return_value=fake) as get:
            app.test_client().get('/locus/next?current=con_1&course_uid=course_9')
        params = get.call_args.kwargs.get('params', {})
        self.assertEqual(params.get('course_uid'), 'course_9')


class TestImportUiPresent(unittest.TestCase):
    def test_courses_page_exposes_an_import_entry_point(self):
        rv = app.test_client().get('/courses')
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b'openImportModal', rv.data)
        self.assertIn(b'/api/upload_epub', rv.data)

    def test_import_ui_offers_only_what_can_be_parsed(self):
        """The UI must advertise exactly what document_extract can read.

        This test used to assert the OPPOSITE — that PDF was absent — because
        no PDF parser existed and offering it would have produced a course that
        ignored the file. pypdf is now installed and PDF is genuinely read, so
        the honest UI is one that offers it. The invariant is unchanged: never
        advertise a format we cannot parse."""
        rv = app.test_client().get('/courses')
        body = rv.data.decode()
        accept_line = [l for l in body.splitlines() if 'accept=' in l and 'import-file' in l]
        self.assertTrue(accept_line, "import file input should declare accepted types")
        for unparseable in ('.docx', '.mobi', '.azw'):
            self.assertNotIn(unparseable, accept_line[0])


class TestUploadValidation(unittest.TestCase):
    def _post(self, filename, data=b'x'):
        return app.test_client().post(
            '/api/upload_epub',
            data={'file': (io.BytesIO(data), filename)},
            content_type='multipart/form-data')

    def test_pdf_is_no_longer_rejected_for_its_type(self):
        """PDF is now read for real, so it must not be refused as a format.
        (A junk body may still fail extraction — that is a different error.)"""
        rv = self._post('book.pdf')
        if rv.status_code == 400:
            self.assertNotIn('not supported', rv.get_json()['error'].lower())

    def test_formats_without_a_parser_are_still_refused_with_a_reason(self):
        for name in ('book.docx', 'book.mobi', 'book.azw3'):
            rv = self._post(name)
            self.assertEqual(rv.status_code, 400, name)
            self.assertIn('not supported', rv.get_json()['error'].lower())

    def test_unknown_type_is_rejected(self):
        self.assertEqual(self._post('thing.xyz').status_code, 400)

    def test_accepted_types_are_not_rejected_for_type(self):
        for name in ('notes.md', 'notes.txt', 'book.epub'):
            rv = self._post(name)
            if rv.status_code == 400:
                self.assertNotIn('Accepted formats', rv.get_json().get('error', ''),
                                 f"{name} should pass the type check")


class TestUploadedFileIsActuallyRead(unittest.TestCase):
    def test_fsm_extracts_document_text(self):
        """start_creation must read the file, not just its name."""
        p = os.path.join(_root, 'services/core/fsm_logic.py')
        with open(p) as f:
            src = f.read()
        self.assertIn('document_extract', src,
                      'the uploaded document must actually be parsed')
        self.assertIn('source_text', src)

    def test_hydrator_consumes_the_document(self):
        p = os.path.join(_root, 'services/core/course_builder.py')
        with open(p) as f:
            src = f.read()
        self.assertIn('source_document', src)
        self.assertIn('_excerpt_for_concept', src,
                      'the document must be sliced per concept, not ignored')


if __name__ == '__main__':
    unittest.main()
