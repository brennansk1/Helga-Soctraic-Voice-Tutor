"""The web-ui side of the notebook and the printables (notes_api blueprint).

Everything here runs OFFLINE: the RAG service is stubbed at the blueprint's
own seam (_rag_get), because a test that needs the rag-engine container up
tells you about docker-compose, not about this code.

The certificate has one property worth defending hard: it must be impossible
to render for an unfinished course by typing the URL. The completeness check
is server-side, and the not-finished page is asserted to be a progress page,
not a certificate with awkward numbers on it.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "services/web-ui"))

from flask import Flask          # noqa: E402

import notes_api                 # noqa: E402


class _FakeResp:
    """The two attributes of requests.Response the blueprint reads."""

    def __init__(self, payload, status=200, headers=None,
                 content_type="application/json"):
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode() \
            if isinstance(payload, (dict, list)) else payload
        self.headers = {"Content-Type": content_type}
        self.headers.update(headers or {})

    def json(self):
        return self._payload


def _client():
    app = Flask(
        __name__,
        static_folder=os.path.join(_ROOT, "services/web-ui/static"),
        template_folder=os.path.join(_ROOT, "services/web-ui/templates"),
    )
    # base.html calls csrf_token(); the real app registers it, the test shim
    # only needs it to exist.
    app.jinja_env.globals["csrf_token"] = lambda: "test-token"
    app.register_blueprint(notes_api.notes_api)
    return app.test_client()


STRUCTURE = {
    "title": "Alpine Botany",
    "structure": {"modules": [{
        "uid": "mod_1", "title": "Meadows",
        "units": [{
            "uid": "unit_1", "title": "Flowers",
            "lessons": [{
                "uid": "less_1", "title": "Edelweiss",
                "concepts": [
                    {"uid": "con_a", "title": "Leaf structure",
                     "completed": True, "bloom_level": 2},
                    {"uid": "con_b", "title": "Root systems",
                     "completed": False, "bloom_level": 0},
                ],
            }],
        }],
    }]},
}


class TestNotebookPage(unittest.TestCase):

    def test_page_renders_and_carries_the_course_uid(self):
        html = _client().get("/notebook?course_uid=course_ab12cd34") \
            .data.decode()
        self.assertIn('"course_ab12cd34"', html)
        self.assertIn("notebook-empty", html)

    def test_page_without_a_uid_is_the_picker_not_a_bounce(self):
        resp = _client().get("/notebook")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("notebook-picker", resp.data.decode())


class TestNotebookProxy(unittest.TestCase):

    def test_proxy_passes_body_and_status_through(self):
        payload = {"course_uid": "c1", "total_notes": 0, "groups": []}
        with patch.object(notes_api, "_rag_get",
                          return_value=(_FakeResp(payload), 200)):
            resp = _client().get("/api/notebook/c1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["total_notes"], 0)

    def test_rag_down_is_named_not_drawn_as_an_empty_notebook(self):
        with patch.object(notes_api, "_rag_get", return_value=(None, 502)):
            resp = _client().get("/api/notebook/c1")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("error", resp.get_json())

    def test_export_keeps_the_attachment_header(self):
        raw = _FakeResp(b"# notes", headers={
            "Content-Disposition": 'attachment; filename="x-notebook.md"'},
            content_type="text/markdown")
        with patch.object(notes_api, "_rag_get", return_value=(raw, 200)):
            resp = _client().get("/api/notebook/c1/export")
        self.assertIn("attachment", resp.headers["Content-Disposition"])


class TestPrintSyllabus(unittest.TestCase):

    def test_syllabus_renders_structure_and_counts(self):
        with patch.object(notes_api, "_rag_get",
                          return_value=(_FakeResp(STRUCTURE), 200)):
            html = _client().get("/print/syllabus/c1").data.decode()
        self.assertIn("Alpine Botany", html)
        self.assertIn("Meadows", html)
        self.assertIn("Leaf structure", html)
        self.assertIn("2 concepts", html)
        # The completed concept gets the filled checkbox.
        self.assertIn("syllabus-check done", html)

    def test_rag_failure_names_itself(self):
        with patch.object(notes_api, "_rag_get", return_value=(None, 502)):
            resp = _client().get("/print/syllabus/c1")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("could not load", resp.data.decode())


class TestPrintCertificate(unittest.TestCase):

    COMPLETE = {"course_uid": "c1", "course_title": "Alpine Botany",
                "total_concepts": 2, "completed_concepts": 2,
                "complete": True, "completion_date": "2026-08-19T12:00:00",
                "learner_name": "Brennan"}

    def test_finished_course_gets_the_certificate(self):
        with patch.object(notes_api, "_rag_get",
                          return_value=(_FakeResp(self.COMPLETE), 200)):
            html = _client().get("/print/certificate/c1").data.decode()
        self.assertIn("Certificate of Completion", html)
        self.assertIn("Brennan", html)
        self.assertIn("Alpine Botany", html)
        self.assertIn("2026-08-19", html)

    def test_no_name_on_file_leaves_a_signature_line(self):
        cert = dict(self.COMPLETE, learner_name="")
        with patch.object(notes_api, "_rag_get",
                          return_value=(_FakeResp(cert), 200)):
            html = _client().get("/print/certificate/c1").data.decode()
        self.assertIn("cert-name-line", html)

    def test_unfinished_course_cannot_be_certified_by_url(self):
        cert = dict(self.COMPLETE, complete=False, completed_concepts=1,
                    completion_date=None)
        with patch.object(notes_api, "_rag_get",
                          return_value=(_FakeResp(cert), 200)):
            html = _client().get("/print/certificate/c1").data.decode()
        self.assertNotIn("Certificate of Completion", html)
        self.assertIn("Not finished yet", html)
        self.assertIn("1 of 2", html)


if __name__ == "__main__":
    unittest.main()
