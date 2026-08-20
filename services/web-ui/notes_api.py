# REGISTER WITH (in app.py, next to the library_api mount):
#
#     from notes_api import notes_api
#     app.register_blueprint(notes_api)
#
"""The Session Notebook and the printables, on the web-ui side.

Three surfaces, all read-only:

  1. /notebook — the learner's own session notes for one course, chronological
     and grouped by concept, with Markdown export. The data lives on the RAG
     process (services/rag/notes_api.py); this blueprint proxies it under
     /api/notebook/* the same way app.py proxies every other RAG read.

  2. /print/syllabus/<uid> — the course structure as a print-friendly page.

  3. /print/certificate/<uid> — a completion certificate for a FINISHED
     course. The completeness check happens server-side against the same
     read model the page renders, so the certificate cannot be conjured for a
     half-done course by editing a URL — an unfinished course gets an honest
     progress page instead.

Pages render from this blueprint (render_template resolves against the app's
template folder), so app.py itself stays untouched.
"""

import logging
import os

import requests
from flask import Blueprint, Response, jsonify, render_template, request

logger = logging.getLogger(__name__)

notes_api = Blueprint("notes_api", __name__)

# Same env var + default as app.py's SERVICES['rag']; restated here rather than
# imported because importing app.py from a blueprint it registers is a cycle.
RAG_URL = os.environ.get("RAG_URL", "http://helga-rag-engine:5002")


def _rag_get(path, timeout=10, params=None):
    """One RAG GET with the failure modes callers actually need to tell apart:
    (response, status) on an answer, (None, 502) when the service is
    unreachable — 'the course has no notes' and 'the service is down' must
    never render the same."""
    try:
        resp = requests.get(f"{RAG_URL}{path}", timeout=timeout, params=params)
        return resp, resp.status_code
    except requests.RequestException as e:
        logger.error(f"notebook: RAG unreachable for {path}: {e}")
        return None, 502


# --------------------------------------------------------------------- pages

@notes_api.route("/notebook")
def notebook_page():
    """The notebook shell; notebook.js fetches the data. Without a course_uid
    the page offers the course picker rather than bouncing — an empty
    parameter is a navigation state, not an error."""
    return render_template("notebook.html",
                           course_uid=request.args.get("course_uid", ""))


# ------------------------------------------------------------------- proxies

@notes_api.route("/api/notebook/<course_uid>", methods=["GET"])
def notebook_data(course_uid):
    resp, status = _rag_get(f"/api/courses/{course_uid}/notes")
    if resp is None:
        return jsonify({"error": "The notebook service did not answer"}), status
    return Response(resp.content, status=status,
                    mimetype=resp.headers.get("Content-Type", "application/json"))


@notes_api.route("/api/notebook/<course_uid>/export", methods=["GET"])
def notebook_export(course_uid):
    resp, status = _rag_get(f"/api/courses/{course_uid}/notes/export", timeout=20)
    if resp is None:
        return jsonify({"error": "The notebook service did not answer"}), status
    headers = {}
    if "Content-Disposition" in resp.headers:
        headers["Content-Disposition"] = resp.headers["Content-Disposition"]
    return Response(resp.content, status=status,
                    mimetype=resp.headers.get("Content-Type", "text/markdown"),
                    headers=headers)


# ---------------------------------------------------------------- printables

@notes_api.route("/print/syllabus/<course_uid>")
def print_syllabus(course_uid):
    resp, status = _rag_get("/api/course_structure",
                            params={"uid": course_uid})
    if resp is None or status != 200:
        detail = "the course service did not answer" if resp is None \
            else f"HTTP {status} from the course service"
        return render_template("print_syllabus.html", error=detail,
                               course_uid=course_uid, title="", modules=[],
                               concept_count=0), (status if status != 200 else 502)
    data = resp.json()
    modules = (data.get("structure") or {}).get("modules") or []
    concept_count = sum(
        len(lesson.get("concepts") or [])
        for m in modules
        for u in (m.get("units") or [])
        for lesson in (u.get("lessons") or []))
    return render_template("print_syllabus.html", error=None,
                           course_uid=course_uid,
                           title=data.get("title", ""),
                           modules=modules,
                           concept_count=concept_count)


@notes_api.route("/print/certificate/<course_uid>")
def print_certificate(course_uid):
    resp, status = _rag_get(f"/api/courses/{course_uid}/completion")
    if resp is None or status != 200:
        detail = "the course service did not answer" if resp is None \
            else f"HTTP {status} from the course service"
        return render_template("print_certificate.html", error=detail,
                               cert=None, course_uid=course_uid), \
            (status if status != 200 else 502)
    return render_template("print_certificate.html", error=None,
                           cert=resp.json(), course_uid=course_uid)
