# REGISTER WITH (app.py, next to the parent_api registration):
#   from share_api import share_api; app.register_blueprint(share_api)
"""Browser-facing proxy for course export/import.

The browser can only reach the web-ui, and the bundle logic lives on the RAG
process beside the StorageManager (services/rag/share_api.py). This blueprint
is a pass-through, deliberately: it validates nothing about the bundle itself,
because a second, slightly different validator in front of the real one is how
two components come to disagree about what a valid bundle is. Its own failure
modes — no file attached, RAG unreachable, upload over the cap — are the only
ones it names.

The /api/share/* namespace is distinct from everything in app.py for the same
reason library_api's is: two rules on one URL is silent shadowing in Flask, so
registering this file must not be able to change any shipped route.
"""

import logging
import os

import requests
from flask import Blueprint, Response, jsonify, request, stream_with_context

logger = logging.getLogger(__name__)

share_api = Blueprint("share_api", __name__)

# Same env var + default app.py uses for SERVICES['rag'], read here so this
# module imports nothing from app.py (which would be circular).
RAG_URL = os.environ.get("RAG_URL", "http://helga-rag-engine:5002")

# Mirrors MAX_BUNDLE_BYTES in services/rag/share_api.py. Enforced here too so
# an oversized upload dies at the edge instead of crossing the network twice.
MAX_BUNDLE_BYTES = 256 * 1024 * 1024


@share_api.route("/api/share/course/<uid>/export", methods=["GET"])
def proxy_export(uid):
    try:
        upstream = requests.get(
            f"{RAG_URL}/api/share/course/{uid}/export",
            stream=True, timeout=(5, 300))
    except requests.RequestException as e:
        logger.error(f"share export proxy unreachable: {e}")
        return jsonify({"ok": False, "error": "share_service_unreachable",
                        "detail": "rag-engine did not answer"}), 502

    if upstream.status_code != 200:
        # RAG already answered with a named JSON error; pass it through
        # untranslated so the browser sees the real reason.
        try:
            return jsonify(upstream.json()), upstream.status_code
        except ValueError:
            return jsonify({"ok": False, "error": "export_failed",
                            "detail": f"rag-engine answered "
                                      f"{upstream.status_code}"}), 502

    # Stream rather than buffer: the bundle can be tens of megabytes and the
    # web-ui process serves every other page while this download runs.
    headers = {}
    for h in ("Content-Type", "Content-Disposition", "Content-Length"):
        if h in upstream.headers:
            headers[h] = upstream.headers[h]
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=64 * 1024)),
        status=200, headers=headers)


@share_api.route("/api/share/course/import", methods=["POST"])
def proxy_import():
    upload = request.files.get("bundle")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "empty_upload",
                        "detail": "no 'bundle' file in the request"}), 400
    if request.content_length and request.content_length > MAX_BUNDLE_BYTES:
        return jsonify({"ok": False, "error": "bundle_too_large",
                        "detail": f"cap is {MAX_BUNDLE_BYTES} bytes"}), 413

    try:
        upstream = requests.post(
            f"{RAG_URL}/api/share/course/import",
            files={"bundle": (upload.filename, upload.stream,
                              upload.mimetype or "application/zip")},
            # Import writes a whole course; give it the same ceiling the
            # custom-course create proxy gets, not a chat-sized timeout.
            timeout=600)
    except requests.RequestException as e:
        logger.error(f"share import proxy unreachable: {e}")
        return jsonify({"ok": False, "error": "share_service_unreachable",
                        "detail": "rag-engine did not answer"}), 502

    try:
        return jsonify(upstream.json()), upstream.status_code
    except ValueError:
        return jsonify({"ok": False, "error": "import_failed",
                        "detail": f"rag-engine answered "
                                  f"{upstream.status_code}"}), 502
