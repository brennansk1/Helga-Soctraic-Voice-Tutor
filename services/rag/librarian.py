from flask import Flask, request, jsonify, has_request_context
import logging
import sys
import os
import time
import json
import threading
import requests
import re
import uuid
from services.common.storage import StorageManager, DEFAULT_STUDENT_ID
from services.common.storage import ProgressStore
from services.common.review_items import demath

# THIS SERVICE WAS LOGGING NOTHING BELOW WARNING.
#
# librarian.py configured no handler at all, so the root logger's default
# (WARNING) applied and every logger.info in the RAG service — and in
# course_builder when it runs HERE, which is where a resume and every external
# handback run — was discarded. Measured while debugging a handback: the
# hydrator's "[RESUME] finished with status", "[BRIEF]", "[SOURCES]" and
# "[MARKDOWN] Structuring" lines were all absent, so a hydration that wrote
# nothing looked identical to one that never started. core configures this and
# rag did not, which is why the same code is loud in one service and silent in
# the other.
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Env Vars
WEB_UI_URL = os.getenv("WEB_UI_URL", "http://web-ui:5000")
LLM_API_URL = os.getenv(
    "LLM_API_URL", "http://host.docker.internal:11434/v1/chat/completions"
)
DATA_ROOT = os.getenv("DATA_ROOT", "/app/data")


# B15.5: the web-ui emits status updates into a per-student Socket.IO room and
# DROPS any payload that arrives without a student_id (fail closed — never
# broadcast one student's tokens to another). This service used to POST
# {"message": ...} with no owner, so every STRUCT:/hydration message it sent was
# silently discarded with a 202 and the wizard's progress tree never populated.
# The drop is correct; the caller has to stamp the owner.
#
# The owner is resolved from the inbound request when it carries one, otherwise
# it is the legacy single-student id — the same fallback storage.py and the
# web-ui's current_student_id() use, and the room a pre-accounts browser joins.
# The wizard's ContentHydrator fans concept work out over a ThreadPoolExecutor
# and calls back from worker threads that have NO Flask request context, so
# request handlers stash the resolved owner here for those threads to read.
_status_student_id = DEFAULT_STUDENT_ID
_status_student_lock = threading.Lock()


def _status_owner() -> str:
    """student_id to stamp on outbound status payloads."""
    if has_request_context():
        try:
            body = request.get_json(silent=True) or {}
            sid = body.get("student_id") or request.args.get("student_id")
            if sid:
                return sid
        except Exception as e:
            logger.debug(f"Could not read student_id from request: {e}")
    with _status_student_lock:
        return _status_student_id


def _bind_status_owner() -> str:
    """Pin the current request's owner for the worker threads spawned under it.

    Call once at the top of any handler that emits progress; without it a
    callback running off the request thread falls back to whatever the previous
    request left behind.
    """
    global _status_student_id
    owner = _status_owner()
    with _status_student_lock:
        _status_student_id = owner
    return owner


def _update_status(message: str, log: str = None):
    try:
        payload = {"message": message, "student_id": _status_owner()}
        if log:
            payload["log"] = log
        requests.post(
            f"{WEB_UI_URL}/api/update_thinking_status", json=payload, timeout=1
        )
    except Exception as e:
        logger.debug(f"Failed to send status update: {e}")


# Initialize Storage Manager
storage = StorageManager(DATA_ROOT)

# Auto-clean failed courses on startup
try:
    from services.common.course_cleaner import clean_failed_courses
    clean_failed_courses(DATA_ROOT)
except Exception as e:
    logger.warning(f"Failed to run course auto-cleaner: {e}")

# Start background operations (cleanup, integrity checks, cache pruning)
bg_ops = None
try:
    from services.common.background_ops import BackgroundOperations
    bg_ops = BackgroundOperations(storage_manager=storage, interval_seconds=300)
    bg_ops.start()
    logger.info("Background operations initialized")
except Exception as e:
    logger.warning(f"Background ops init failed (non-fatal): {e}")

app = Flask(__name__)

# Course bundles: export a course as one portable file, import someone
# else's. Registered here because the course stores live on this service.
from services.rag.share_api import create_share_blueprint
app.register_blueprint(create_share_blueprint(storage))

# The session notebook: reads the notes real sessions actually produce (the
# Markdown ## Session Notes bullets) merged with the session_notes table.
from services.rag.notes_api import create_notes_blueprint
app.register_blueprint(create_notes_blueprint(storage))

# The pipeline surface: see every stage, take any of them, hand the rest back.
from services.rag.pipeline_api import create_pipeline_blueprint
app.register_blueprint(create_pipeline_blueprint(storage))

# B27.1: opt-in structured JSON logs (HELGA_JSON_LOGS=true)
try:
    from services.common.logging_utils import configure_json_logging
    configure_json_logging("rag-engine")
except Exception:
    pass


# B18: mount the assessment engine blueprint (spec 05 §0 — shares this
# process, the StorageManager, and llm_utils; no new container).
try:
    from services.exam.exam_engine import create_exam_blueprint
    app.register_blueprint(create_exam_blueprint(storage))
    logger.info("Exam engine blueprint mounted")
except Exception as e:
    logger.error(f"Exam engine mount failed (non-fatal): {e}")


# B26.2: admin catalog review endpoints. Gated by ADMIN_TOKEN (never linked
# from the student UI; students only ever see published catalog rows).
from functools import wraps as _wraps

def _admin_required(f):
    @_wraps(f)
    def decorated(*args, **kwargs):
        token = os.getenv("ADMIN_TOKEN")
        supplied = request.headers.get("X-Admin-Token")
        if not token or supplied != token:
            return jsonify({"error": "admin token required"}), 403
        return f(*args, **kwargs)
    return decorated


@app.route("/api/admin/catalog/courses", methods=["GET"])
@_admin_required
def admin_catalog_list():
    courses = storage.catalog_courses.list_catalog_courses(published_only=False)
    report = {r["code"]: r for r in storage.standards.coverage_report()}
    return jsonify({"courses": courses,
                    "coverage": [r for r in report.values()]})


@app.route("/api/admin/catalog/courses/<uid>", methods=["GET"])
@_admin_required
def admin_catalog_get(uid):
    course = storage.catalog_courses.get_course(uid)
    if not course:
        return jsonify({"error": "not found"}), 404
    return jsonify(course)


@app.route("/api/admin/catalog/courses/<uid>/transition", methods=["POST"])
@_admin_required
def admin_catalog_transition(uid):
    from services.core.catalog_admin import transition_catalog_course
    body = request.get_json(force=True)
    try:
        new_status = transition_catalog_course(
            storage, uid, body.get("action"),
            actor=body.get("actor", "admin"), note=body.get("note"))
        return jsonify({"catalog_status": new_status})
    except ValueError as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/admin/catalog/coverage", methods=["GET"])
@_admin_required
def admin_catalog_coverage():
    """B26.4: standards-coverage audit — published vs gaps per Utah code."""
    return jsonify({"report": storage.standards.coverage_report(
        subject=request.args.get("subject"))})

# Embedding model for (planned) dense/hybrid retrieval. Loaded LAZILY: the old
# eager load was pure startup cost because search currently uses SQLite FTS5 and
# nothing called the model (B2). get_embed_model() is the seam the sqlite-vec
# hybrid pipeline will use once it lands (runtime-validated task).
_embed_model = None


class _OllamaEmbedder:
    """Adapter exposing `.encode` so existing call sites are unchanged."""

    def __init__(self, fn):
        self.encode = fn


def get_embed_model():
    """Embeddings now come from Ollama, not sentence-transformers.

    sentence-transformers pulled PyTorch + transformers into this service:
    hundreds of MB on disk, far more resident, and a recurring source of
    dependency conflicts — on a 24 GB box that is already swapping, that is not
    affordable for an optional retrieval path.

    Ollama is already running and already serves embeddings, so this removes
    the dependency outright AND upgrades the model: bge-m3 is 1024-dim versus
    all-MiniLM-L6-v2's 384. Ollama also owns the model's memory, so it is
    shared and evictable rather than pinned in this process.

    NOTE: the dimension change invalidates any previously built dense index —
    it must be rebuilt. See services/common/embeddings.expected_dim().
    """
    global _embed_model
    if _embed_model is None:
        from services.common.embeddings import get_embed_fn, EMBED_MODEL
        logger.info(f"Using Ollama embeddings: {EMBED_MODEL}")
        _embed_model = _OllamaEmbedder(get_embed_fn())
    return _embed_model

# ZIM/KuzuDB removed — all content is LLM-generated and stored in SQLite + JSON


def _substring_concept_search(query, course_uid):
    """Legacy fallback: substring scan over concept titles. Kept as a safety net
    when FTS5 is unavailable or errors. Returns concept results in the standard
    response shape."""
    results = []
    if course_uid:
        concepts = storage.courses.get_flat_concepts(course_uid)
        for c in concepts:
            c.setdefault("course_uid", course_uid)
    else:
        concepts = []
        for course in storage.courses.list_courses():
            for c in storage.courses.get_flat_concepts(course["uid"]):
                c["course_uid"] = course["uid"]
                concepts.append(c)

    q = query.lower()
    for concept in concepts:
        if q in concept.get("title", "").lower():
            c_course = concept.get("course_uid", course_uid or "")
            content = storage.courses.get_concept_content(c_course, concept["uid"])
            results.append(
                {
                    "uid": concept["uid"],
                    "course_uid": c_course,
                    "title": concept["title"],
                    "text": _search_excerpt(content) if content else "",
                    "type": "Concept",
                }
            )
            if len(results) >= 10:
                break
    return results



# --- what a search result should show ----------------------------------------
#
# A concept file opens with a front-matter rule, its title as an H1, and a
# `## Metadata` block. Sending its first 500 characters therefore sent exactly
# that, and every result in the search dropdown read
#
#     "--- # Clause Ordering Rules ## Metadata - **Bloom Targ…"
#
# — file syntax, in the first thing a learner sees when they search. There was
# no prose in the payload for the client to recover, so this is fixed where the
# text is chosen rather than where it is rendered.
_SEARCH_SKIP_SECTIONS = {
    "metadata", "learning objectives", "prerequisites", "mastery criteria",
    "sources", "visual aids", "socratic hooks",
}


def _search_excerpt(markdown, limit=500):
    """The first real teaching prose in a concept, for a result preview."""
    if not markdown:
        return ""
    out, skipping = [], False
    for line in str(markdown).splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            skipping = heading in _SEARCH_SKIP_SECTIONS
            continue
        if skipping or stripped.startswith("```"):
            continue
        # "- **Bloom Target**: 3" and friends are metadata wearing a bullet.
        if re.match(r"^[-*+]\s*\*\*[^*]+\*\*\s*:", stripped):
            continue
        out.append(stripped)
        if sum(len(x) for x in out) >= limit:
            break
    text = " ".join(out)
    # Strip the inline markers; keep the words inside code spans, since in a
    # SQL course those words are the point.
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or str(markdown)[:limit]


@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "")
    course_uid = request.args.get("course_uid")
    # Optional: ?mode=hybrid routes through dense+FTS5 fusion when available.
    # The default (mode absent or any value other than "hybrid") uses FTS5 only.
    mode = request.args.get("mode", "fts")
    if not query:
        return jsonify({"error": "No query"}), 400

    results = []

    # Search courses by title (cheap metadata scan, unchanged behavior)
    for course in storage.courses.list_courses():
        if query.lower() in course.get("title", "").lower():
            results.append(
                {
                    "uid": course["uid"],
                    "title": course["title"],
                    "text": course.get("overview", ""),
                    "type": "Course",
                }
            )

    # If we found course matches, return them without digging into concepts
    if results:
        return jsonify({"results": results})

    # Concept search.
    # Default: SQLite FTS5 (ranked, searches title AND body content).
    # Optional hybrid mode: FTS5 + dense via sqlite-vec, fused with RRF.
    #   - Hybrid is only active when ?mode=hybrid AND sqlite-vec is available.
    #   - If dense deps are absent the request degrades to FTS5, and the
    #     response says so (`retrieval_mode` + `degraded`). A2: degradation is
    #     reported, never silent.
    #   - Never raises — always falls back to substring search on any exception.
    # A2: hybrid degradation must be LOUD. Silently serving lexical-only
    # results when the caller asked for dense retrieval hides a real quality
    # cliff — the caller believes it got semantic search and cannot tell it
    # didn't. `retrieval_mode` in the response reports what actually ran.
    actual_mode = "fts5"
    degraded_reason = None
    try:
        if mode == "hybrid":
            if not storage.search.is_dense_available():
                degraded_reason = "sqlite-vec unavailable"
                logger.warning(
                    "hybrid search requested but DEGRADED to FTS5: %s", degraded_reason)
                concept_rows = storage.search.search(query, course_uid=course_uid, limit=10)
            else:
                try:
                    embed_fn = get_embed_model().encode
                except Exception as embed_err:
                    embed_fn = None
                    degraded_reason = f"embedding model unavailable ({embed_err})"
                    logger.warning(
                        "hybrid search requested but DEGRADED to FTS5: %s", degraded_reason)
                concept_rows = storage.search.hybrid_search(
                    query,
                    embed_fn=embed_fn,
                    course_uid=course_uid,
                    limit=10,
                )
                actual_mode = "fts5" if embed_fn is None else "hybrid"
        else:
            concept_rows = storage.search.search(query, course_uid=course_uid, limit=10)

        for row in concept_rows:
            content = row.get("content") or ""
            results.append(
                {
                    "uid": row["concept_uid"],
                    # WHICH COURSE THIS CONCEPT IS IN.
                    #
                    # The storage layer has always returned it and this
                    # response dropped it, so the header search sent every
                    # concept hit to /learn?course_uid=con_xxxxxxxx — a
                    # concept uid in the course slot, naming a course that
                    # does not exist. learn.html already deep-links to
                    # ?course_uid=&concept_uid=, so the destination was built
                    # and only the link to it was wrong.
                    "course_uid": row.get("course_uid"),
                    "title": row.get("title", ""),
                    "text": _search_excerpt(content) if content else "",
                    "type": "Concept",
                }
            )
    except Exception as e:
        logger.warning(f"Search failed, falling back to substring: {e}")
        results = _substring_concept_search(query, course_uid)
        actual_mode = "substring"
        degraded_reason = str(e)

    payload = {"results": results, "retrieval_mode": actual_mode}
    if mode == "hybrid" and actual_mode != "hybrid":
        # Explicit, machine-readable signal that the caller did NOT get what
        # it asked for.
        payload["degraded"] = True
        payload["degraded_reason"] = degraded_reason or "hybrid unavailable"
    return jsonify(payload)


def _course_progress_pct(course_uid, stats, student_id=None):
    """How much of this course the learner has actually done, 0-100.

    Counted from user_progress rather than from the course document, because
    progress belongs to the learner and the document belongs to the build.
    A concept counts once it has been completed or reviewed; `locked` and
    `in_progress` do not, so the number cannot drift upward on a concept that
    was merely opened.
    """
    if not course_uid:
        return 0
    total = 0
    try:
        total = int((stats or {}).get("concepts") or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return 0
    try:
        rows = storage.progress.get_course_progress(course_uid, student_id=student_id)
        done = sum(1 for r in rows
                   if storage.progress.is_done(r.get("status")))
        return max(0, min(100, round(done * 100 / total)))
    except Exception as e:
        # A progress read that fails must not cost the learner the course list.
        # Zero is the honest answer here — it is what we know.
        logger.warning("progress unavailable for %s: %s", course_uid, e)
        return 0


@app.route("/api/courses", methods=["GET", "DELETE"])
def courses():
    # The learner whose progress this is. Without it every card would report
    # the default profile's progress to whoever asked.
    student_id = request.args.get("student_id") or None

    if request.method == "DELETE":
        uid = request.args.get("uid")
        if not uid:
            return jsonify({"error": "Invalid request"}), 400
        try:
            storage.courses.delete_course(uid)
            logger.info(f"Deleted course: {uid}")
            return jsonify({"status": "deleted"})
        except Exception as e:
            logger.error(f"Failed to delete course {uid}: {e}")
            return jsonify({"error": str(e)}), 500

    try:
        course_list = []
        for course in storage.courses.list_courses():
            stats = storage.courses.get_course_stats(course["uid"])
            course_list.append(
                {
                    "uid": course["uid"],
                    "title": course.get("title", ""),
                    # THE KEY IS `description`, NOT `overview`.
                    #
                    # Courses are written with a `description`; this read
                    # `overview` and fell back to "", so every card in the
                    # library showed no description — and the front end filled
                    # the gap with the identical sentence "A comprehensive
                    # interactive course." on all four. The descriptions were
                    # on disk the whole time ("Regex as a working tool for
                    # parsing text."). Same shape as the dashboard reading
                    # total_courses while the API returned courses.
                    "description": (course.get("description")
                                    or course.get("overview") or ""),
                    "status": course.get("status", "unknown"),
                    # WHY IT IS NOT READY, in the audit's own words.
                    #
                    # The gate writes a precise sentence ("4 of 4 concepts are
                    # missing sections the tutor reads — there is no lesson to
                    # teach") and it stopped at structure.json. The card fell
                    # back to the generic "something worth reviewing" caveat, so
                    # a course that cannot be taught at all looked exactly like
                    # one with a few rough edges.
                    "gate_reason": course.get("gate_reason") or "",
                    # Concepts the TUTOR CAN TEACH FROM, by the same check the
                    # audit gate uses (course_audit.is_teachable) — not files on
                    # disk. A concept can have a markdown file and still be an
                    # outline the tutor cannot run a lesson from, which is
                    # exactly the state two of these courses are in.
                    "teachable_count": course.get("hydrated_count"),
                    "concept_count": course.get("concept_count"),
                    "teaching_style": course.get("teaching_style", ""),
                    # WHICH TEACHING LAYER THIS COURSE ACTUALLY GOT.
                    #
                    # A course that routed to no domain is taught generically —
                    # no per-kind guidance, none of the prohibitions that
                    # define each domain. That is a real difference in what the
                    # learner receives and nothing surfaced it, so a course
                    # quietly getting the lesser path looked identical to one
                    # getting the better path.
                    "teaching_domain": course.get("teaching_domain") or None,
                    # A LITERAL ZERO, FOR EVERY COURSE, ALWAYS.
                    #
                    # courses.js gates the resume affordance on `progress > 0`,
                    # so the card could never render "Continue: <concept>" and
                    # always read "Start Learning" — no matter how much of the
                    # course the learner had done. Every session began at
                    # concept one. That is READY_FOR_USE D1, D3 and E4 failing
                    # on a single hard-coded value.
                    "progress": _course_progress_pct(
                        course.get("uid"), stats, student_id),
                    "stats": stats,
                }
            )
        return jsonify({"courses": course_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/courses/summary", methods=["GET"])
def courses_summary():
    """Lightweight course listing — returns only essential fields for card rendering."""
    try:
        course_list = []
        for course in storage.courses.list_courses():
            course_list.append({
                "uid": course.get("uid", ""),
                "title": course.get("title", ""),
                "status": course.get("status", "unknown"),
                "created_at": course.get("created_at", ""),
            })
        return jsonify({"courses": course_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/course_status/<uid>", methods=["GET"])
def course_status(uid):
    """WIZ-6: Poll course creation/hydration status by UID."""
    try:
        course = storage.courses.get_course(uid)
        if not course:
            return jsonify({"uid": uid, "status": "not_found"}), 404
        status = course.get("status", "unknown")
        stats = storage.courses.get_course_stats(uid)
        return jsonify({
            "uid": uid,
            "status": status,
            "title": course.get("title", ""),
            "stats": stats,
        })
    except Exception as e:
        logger.error(f"Course status check failed for {uid}: {e}")
        return jsonify({"uid": uid, "status": "error", "error": str(e)}), 500


@app.route("/api/stats", methods=["GET"])
def stats():
    try:
        courses = storage.courses.list_courses()
        total_concepts = sum(
            storage.courses.get_course_stats(c["uid"]).get("concepts", 0)
            for c in courses
        )
        streak = storage.activity.get_streak()

        # HOW MANY THE LEARNER HAS ACTUALLY LEARNED.
        #
        # This returned only a TOTAL concept count, and the dashboard's card is
        # labelled "Concepts learned" — so there was no number to show and the
        # page displayed 0 next to "You have 4 courses". Studied means the
        # learner has been through it: a user_progress row that is completed or
        # reviewed, not merely one that exists.
        studied = 0
        try:
            # Third copy of the same list, this one in SQL. Built from the
            # shared definition so the dashboard cannot disagree with the
            # course list and the learn path about what "learned" means.
            _done = ProgressStore.DONE_STATUSES
            row = storage.courses._get_db().execute(
                "SELECT COUNT(*) FROM user_progress WHERE status IN (%s)"
                % ",".join("?" * len(_done)), tuple(_done)
            ).fetchone()
            studied = int(row[0]) if row else 0
        except Exception as e:
            logger.warning("studied-concept count unavailable: %s", e)

        return jsonify({
            "courses": len(courses),
            "concepts": total_concepts,
            "concepts_studied": studied,
            "streak": streak,
            # The names home.js has always asked for. It read total_courses and
            # concepts_mastered, this endpoint returned courses and concepts,
            # and the mismatch fell through to a literal "0" — so the dashboard
            # reported no courses while listing four of them underneath.
            "total_courses": len(courses),
            "total_concepts": total_concepts,
            "concepts_mastered": studied,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/course_structure", methods=["GET"])
def structure():
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "Invalid request"}), 400
    try:
        course = storage.courses.get_course(uid)
        if not course:
            return jsonify({"error": "Course not found"}), 404

        # B5.6: load all progress for this course in ONE query, then look up
        # per concept in-memory (was an N+1: one SELECT per concept).
        # student_id matters here as much as it does in the course list: without
        # it this read the default profile's progress regardless of who asked.
        progress_by_concept = {
            p["concept_uid"]: p
            for p in storage.progress.get_course_progress(
                uid, student_id=request.args.get("student_id") or None)
        }

        # Build structure response matching old format
        structure = {"modules": []}
        for module in course.get("modules", []):
            mod = {"uid": module["uid"], "title": module["title"], "units": []}
            for unit in module.get("units", []):
                unit_dict = {"uid": unit["uid"], "title": unit["title"], "lessons": []}
                for lesson in unit.get("lessons", []):
                    lesson_dict = {
                        "uid": lesson["uid"],
                        "title": lesson["title"],
                        "concepts": [],
                    }
                    for concept in lesson.get("concepts", []):
                        # Check completion from the pre-loaded progress map (B5.6)
                        progress = progress_by_concept.get(concept["uid"])
                        # Shared definition -- see ProgressStore.DONE_STATUSES.
                        # Counting "completed" alone here, while the course list
                        # counted reviewed and mastered too, is what made one
                        # course read 4% on one tab and 0% on the next.
                        completed = (
                            storage.progress.is_done(progress["status"])
                            if progress else False
                        )
                        bloom_level = (
                            progress.get("bloom_level", 0) if progress else 0
                        )
                        lesson_dict["concepts"].append(
                            {
                                "uid": concept["uid"],
                                "title": concept["title"],
                                "completed": completed,
                                "bloom_level": bloom_level,
                                # WITHHELD travels with the node.
                                #
                                # Pass 3 marks a concept it could not fix, and
                                # this endpoint is what the path view renders
                                # from. Dropping the flag here — the same way
                                # source_confidence was dropped, which is why
                                # the low-confidence badge never had a value —
                                # would leave the learner clicking a concept
                                # the tutor will then refuse to teach.
                                "withheld": bool(concept.get("withheld")),
                                "withheld_reason": concept.get(
                                    "withheld_reason", ""),
                            }
                        )
                    unit_dict["lessons"].append(lesson_dict)
                mod["units"].append(unit_dict)
            structure["modules"].append(mod)

        # LRN-10: Cache structure for 30s to reduce N-query load on large courses
        resp = jsonify({"title": course.get("title", ""), "structure": structure})
        resp.headers["Cache-Control"] = "private, max-age=30"
        return resp
    except Exception as e:
        logger.error(f"structure error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/flat_syllabus", methods=["GET"])
def flat_syllabus():
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "Invalid request"}), 400
    try:
        concepts = storage.courses.get_flat_concepts(uid)
        syllabus = []
        for c in concepts:
            content = storage.courses.get_concept_content(uid, c["uid"])
            syllabus.append({"uid": c["uid"], "title": c["title"], "text": content})
        return jsonify({"syllabus": syllabus})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/concept_sources", methods=["GET"])
def concept_sources():
    """Where a concept's content actually came from.

    Read-only view over the sources the build already retained. Answers with
    available:false rather than a 404 when a course predates the sources
    table, so the client can say "not recorded for this course" instead of
    rendering an error over a lesson that is otherwise fine.
    """
    uid = request.args.get("uid")
    course_uid = request.args.get("course_uid")
    if not uid or not course_uid:
        return jsonify({"error": "uid and course_uid are required"}), 400
    try:
        return jsonify(storage.courses.get_concept_sources(course_uid, uid))
    except Exception as e:
        logging.exception("concept_sources failed for %s/%s", course_uid, uid)
        return jsonify({"error": str(e)}), 500


@app.route("/concept_details", methods=["GET"])
def concept_details():
    uid = request.args.get("uid")
    course_uid = request.args.get("course_uid")
    if not uid:
        return jsonify({"error": "Invalid request"}), 400
    try:
        # Find concept across courses if course_uid not specified
        if course_uid:
            concept = storage.courses.get_concept_by_uid(course_uid, uid)
            if concept:
                content = storage.courses.get_concept_content(course_uid, uid)
                # Extract pedagogy from markdown content
                misconceptions = _extract_section(content, "Misconceptions")
                analogies = _extract_section(content, "Analogies")
                return jsonify(
                    {
                        "uid": uid,
                        "title": concept["title"],
                        "text": content,
                        "resource_text": content,
                        "source_type": "markdown",
                        "misconceptions": misconceptions,
                        "analogies": analogies,
                        "bloom_level": concept.get("bloom_level", 1),
                        "key_terms": concept.get("key_terms", []),
                        "examples": concept.get("examples", []),
                        "takeaways": concept.get("takeaways", []),
                    }
                )
        else:
            result = storage.courses.find_concept_across_courses(uid)
            if result:
                content = storage.courses.get_concept_content(result["course_uid"], uid)
                misconceptions = _extract_section(content, "Misconceptions")
                analogies = _extract_section(content, "Analogies")
                return jsonify(
                    {
                        "uid": uid,
                        "title": result["title"],
                        "text": content,
                        "resource_text": content,
                        "source_type": "markdown",
                        "misconceptions": misconceptions,
                        "analogies": analogies,
                        "bloom_level": result.get("bloom_level", 1),
                        "key_terms": result.get("key_terms", []),
                        "examples": result.get("examples", []),
                        "takeaways": result.get("takeaways", []),
                    }
                )
        return jsonify({"error": "Not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _extract_section(markdown: str, section_name: str) -> list:
    """Extract content from a markdown section as a list of lines."""
    if not markdown:
        return []
    pattern = rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, markdown, re.DOTALL)
    if match:
        content = match.group(1).strip()
        lines = [
            line.strip().lstrip("- ")
            for line in content.split("\n")
            if line.strip() and line.strip() != "None"
        ]
        return [l for l in lines if l and not l.startswith("#")]
    return []


@app.route("/api/course_meta", methods=["GET"])
def course_meta():
    """Return course metadata including teaching_style."""
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "Invalid request"}), 400
    try:
        course = storage.courses.get_course(uid)
        if course:
            return jsonify(
                {
                    "uid": uid,
                    "title": course.get("title", ""),
                    "overview": course.get("overview", ""),
                    "status": course.get("status", ""),
                    "teaching_style": course.get("teaching_style", ""),
                }
            )
        return jsonify({"error": "Course not found"}), 404
    except Exception as e:
        logger.error(f"course_meta error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/teaching_context", methods=["GET"])
def teaching_context():
    """Return misconceptions and analogies for a concept (extracted from .md content)."""
    uid = request.args.get("uid")
    course_uid = request.args.get("course_uid")
    if not uid:
        # A missing required argument is a bad request, not a concept that
        # happens to have no misconceptions listed. Returning the empty
        # success shape hid caller bugs indefinitely.
        return jsonify({"error": "uid required"}), 400
    try:
        content = ""
        if course_uid:
            content = storage.courses.get_concept_content(course_uid, uid)
        else:
            result = storage.courses.find_concept_across_courses(uid)
            if result:
                content = storage.courses.get_concept_content(result["course_uid"], uid)

        misconceptions = _extract_section(content, "Misconceptions")
        analogies = _extract_section(content, "Analogies")
        return jsonify({"misconceptions": misconceptions, "analogies": analogies})
    except Exception as e:
        # "This concept lists no misconceptions" and "we could not read the
        # concept" are very different facts, and the empty-200 shape made them
        # identical. A tutor that quietly drops its misconception coaching
        # because storage threw is worse than one that says it is broken.
        logger.error(f"teaching_context error: {e}", exc_info=True)
        return jsonify({
            "error": "teaching_context_unavailable",
            "error_code": "TEACHING_CONTEXT_UNAVAILABLE",
            "detail": str(e),
        }), 503


@app.route("/api/create_custom_course", methods=["POST"])
def create_custom_course():
    """Create a custom course with manually specified modules."""
    data = request.json
    title = data.get("title")
    teaching_style = data.get("teaching_style", "")
    modules = data.get("modules", [])

    if not title or not modules:
        return jsonify({"error": "Missing title or modules"}), 400

    try:
        _bind_status_owner()
        course_uid = f"course_{uuid.uuid4().hex[:8]}"

        # Build course dict
        course_dict = {
            "uid": course_uid,
            "title": title,
            "teaching_style": teaching_style,
            "status": "building",
            "modules": [],
        }

        for idx, mod_spec in enumerate(modules, 1):
            mod_title = mod_spec.get("title")
            mod_depth = mod_spec.get("depth", 3)
            source_file = mod_spec.get("source_file")

            mod_uid = f"mod_{uuid.uuid4().hex[:8]}"
            _update_status(f"Building structure for module: {mod_title}")

            unit_uid = f"unit_{uuid.uuid4().hex[:8]}"

            module_dict = {
                "uid": mod_uid,
                "title": mod_title,
                "ordinal": idx,
                "source_file": source_file or "",
                "units": [
                    {
                        "uid": unit_uid,
                        "title": f"{mod_title} - Overview",
                        "ordinal": 1,
                        "lessons": [],
                    }
                ],
            }

            for lesson_idx in range(1, min(mod_depth + 1, 6)):
                lesson_uid = f"less_{uuid.uuid4().hex[:8]}"
                lesson_dict = {
                    "uid": lesson_uid,
                    "title": f"Lesson {lesson_idx}",
                    "ordinal": lesson_idx,
                    "concepts": [],
                }

                for concept_idx in range(1, 4):
                    concept_uid = f"con_{uuid.uuid4().hex[:8]}"
                    lesson_dict["concepts"].append(
                        {
                            "uid": concept_uid,
                            "title": f"Concept {concept_idx}",
                            "ordinal": concept_idx,
                            "learning_objectives": [],
                        }
                    )

                module_dict["units"][0]["lessons"].append(lesson_dict)

            course_dict["modules"].append(module_dict)

        # This path builds a placeholder skeleton only — literal "Lesson N" /
        # "Concept N" titles, empty learning objectives — and NEVER invokes the
        # ContentHydrator, so not one concept has a markdown body. Marking it
        # "ready" made courses.js render an enterable "Start Learning" card over
        # a course with no content at all.
        #
        # "partial" is the honest status: courses.js already handles it (disabled
        # "Not Ready" button, see static/js/courses.js), and course_cleaner's
        # INCOMPLETE_STATUSES ({"failed", "hydration_failed"}) does not collect
        # it, so the user's module structure survives a restart instead of being
        # deleted out from under them. Not "skeleton"/"building": those make the
        # card poll for a build that will never arrive.
        course_dict["status"] = "partial"
        storage.courses.create_course(course_dict)

        logger.info(
            f"Custom course skeleton created (NOT hydrated, status=partial): "
            f"{course_uid} with {len(modules)} modules"
        )
        return jsonify(
            {
                "status": "ok",
                "course_uid": course_uid,
                "course_status": "partial",
                "message": (
                    f'Course "{title}" outlined with {len(modules)} modules. '
                    "No content has been generated yet, so it is not ready to study."
                ),
            }
        )

    except Exception as e:
        logger.error(f"create_custom_course error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/draft/reorder", methods=["POST"])
def draft_reorder():
    """Reorder modules in a draft course structure."""
    data = request.json
    course_uid = data.get("course_uid")
    module_order = data.get("module_order", [])

    if not course_uid or not module_order:
        return jsonify({"error": "Missing course_uid or module_order"}), 400

    try:
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "Course not found"}), 404

        # Reorder modules
        uid_to_module = {m["uid"]: m for m in course.get("modules", [])}
        reordered = []
        for i, mod_uid in enumerate(module_order, 1):
            if mod_uid in uid_to_module:
                mod = uid_to_module[mod_uid]
                mod["ordinal"] = i
                reordered.append(mod)

        course["modules"] = reordered
        storage.courses.update_course(course_uid, course)

        logger.info(f"Reordered {len(module_order)} modules for course {course_uid}")
        return jsonify(
            {"status": "ok", "message": f"Reordered {len(module_order)} modules"}
        )
    except Exception as e:
        logger.error(f"draft_reorder error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/course_modules", methods=["GET"])
def course_modules():
    """Return ordered list of modules for a course (for draft board)."""
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "Invalid request"}), 400
    try:
        course = storage.courses.get_course(uid)
        if not course:
            return jsonify({"error": "Course not found"}), 404

        modules = []
        for m in sorted(course.get("modules", []), key=lambda x: x.get("ordinal", 0)):
            modules.append(
                {
                    "uid": m["uid"],
                    "title": m["title"],
                    "ordinal": m.get("ordinal", 0),
                    "source_file": m.get("source_file", ""),
                }
            )
        return jsonify({"modules": modules})
    except Exception as e:
        logger.error(f"course_modules error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    result = {"status": "healthy", "storage": True}
    if bg_ops:
        result["background_ops"] = bg_ops.get_status()
    return jsonify(result)


@app.route("/api/due_cards", methods=["GET"])
def due_cards_endpoint():
    topic = request.args.get("topic", "")
    course_uid = request.args.get("course_uid", "")
    # The calendar asks about a specific DAY. get_due_cards has accepted
    # target_date all along; this endpoint just never read it, so every
    # future day on the schedule page listed today's cards under that
    # day's heading.
    target_date = request.args.get("target_date") or None
    try:
        # Pass course_uid only if non-empty; otherwise fetch all due cards
        cards = storage.flashcards.get_due_cards(
            course_uid=course_uid if course_uid else None,
            target_date=target_date,
        )
        return jsonify({"cards": cards})
    except Exception as e:
        logger.error(f"Error fetching due cards: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/update_card", methods=["POST"])
def update_card_endpoint():
    data = request.json
    uid = data.get("uid")
    if not uid:
        return jsonify({"error": "Missing uid"}), 400
    try:
        updated_info = {k: v for k, v in data.items() if k != "uid"}
        storage.flashcards.update_card(uid, **updated_info)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Error updating card {uid}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate_flashcards", methods=["POST"])
def generate_flashcards():
    """Generate new flashcards for a concept via LLM."""
    data = request.json
    course_uid = data.get("course_uid")
    concept_uid = data.get("concept_uid")

    if not course_uid or not concept_uid:
        return jsonify({"error": "Missing course_uid or concept_uid"}), 400

    try:
        # Get concept content
        content = storage.courses.get_concept_content(course_uid, concept_uid)
        if not content:
            return jsonify({"error": "Concept content not found"}), 404

        from services.common.llm_utils import llm_generate, extract_python_list

        sys_prompt = "You are an expert educational content creator specializing in active recall. Create Anki-style flashcards from the provided text."
        user_prompt = f'Based on the following text, generate 3-5 high-quality flashcards testing key facts and concepts.\n\nTEXT:\n{content[:4000]}\n\nOutput STRICT JSON Array in this format: [{{"front": "Question here?", "back": "Answer here."}}]'

        raw_output = llm_generate(user_prompt, sys_prompt=sys_prompt, max_tokens=800)
        cards_data = extract_python_list(raw_output)

        if not cards_data:
            return jsonify({"error": "Failed to generate flashcards"}), 500

        added_cards = []
        for card in cards_data:
            front = card.get("front", "").strip()
            back = card.get("back", "").strip()
            if front and back:
                uid = storage.flashcards.add_card(course_uid, concept_uid, front, back)
                added_cards.append({"uid": uid, "front": front, "back": back})

        return jsonify({"status": "ok", "cards": added_cards})

    except Exception as e:
        logger.error(f"Failed to generate flashcards: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/grade_card_fsrs", methods=["POST"])
def grade_card_fsrs_endpoint():
    """Grade a flashcard using FSRS algorithm (server-side).

    Replaces client-side SM-2 with proper FSRS scheduling.
    Expects: {uid, rating} where rating is 1=Again, 2=Hard, 3=Good, 4=Easy
    """
    data = request.json
    uid = data.get("uid")
    rating = data.get("rating")

    if not uid or rating is None:
        return jsonify({"error": "Missing uid or rating"}), 400

    try:
        rating = int(rating)
        if rating < 1 or rating > 4:
            return jsonify({"error": "Rating must be 1-4 (Again/Hard/Good/Easy)"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "Rating must be an integer 1-4"}), 400

    try:
        from services.core.fsrs_engine import FSRSEngine
        fsrs = FSRSEngine(desired_retention=_desired_retention())
        result = storage.flashcards.grade_card_fsrs(uid, rating, fsrs)
        return jsonify({"status": "ok", **result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"FSRS grading error for card {uid}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/review_stats", methods=["GET"])
def review_stats_endpoint():
    """Unified review statistics — flashcard due dates + calendar data.

    Used by both the Review tab and Schedule tab for a single source of truth.
    """
    course_uid = request.args.get("course_uid")
    try:
        stats = storage.flashcards.get_review_stats(
            course_uid=course_uid if course_uid else None
        )
        # Also include streak from activity store. The stats themselves are the
        # point of this endpoint, so a streak lookup failure should not 503 the
        # whole response — but a fabricated 0 reads as "your streak is broken",
        # so flag it rather than let the caller believe the number.
        streak_unavailable = False
        try:
            streak = storage.activity.get_streak()
        except Exception as streak_err:
            logger.warning(f"review_stats: streak lookup failed: {streak_err}")
            streak = 0
            streak_unavailable = True
        stats["streak"] = streak
        if streak_unavailable:
            stats["streak_unavailable"] = True
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Review stats error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/quiz", methods=["GET"])
def quiz_endpoint():
    """Generate a quiz question from a random concept.

    Prioritizes concepts the user has studied (completed) and uses
    FSRS data to focus on weak areas (low stability / high lapses).
    """
    course_uid = request.args.get("course_uid")
    try:
        import random
        from services.common.llm_utils import llm_generate

        # Get all courses or filter to one
        if course_uid:
            courses = [storage.courses.get_course(course_uid)]
            courses = [c for c in courses if c]
        else:
            courses = []
            for c in storage.courses.list_courses():
                uid = c.get("uid") or c.get("course_uid")
                if uid:
                    full = storage.courses.get_course(uid)
                    if full:
                        courses.append(full)

        if not courses:
            return jsonify({"error": "No courses found"}), 404

        # Get all concepts across selected courses
        all_concepts = []
        for course in courses:
            c_uid = course.get("uid", "")
            flat = storage.courses.get_flat_concepts(c_uid)
            for concept in flat:
                concept["_course_uid"] = c_uid
                concept["_course_title"] = course.get("title", "")
            all_concepts.extend(flat)

        if not all_concepts:
            return jsonify({"error": "No concepts found in selected courses"}), 404

        # Weight selection toward concepts with weak flashcards (high lapses, low stability)
        weighted = []
        for concept in all_concepts:
            weight = 1.0
            try:
                cards = storage.flashcards.get_cards_for_concept(concept["uid"])
                if cards:
                    avg_lapses = sum(c.get("lapses", 0) for c in cards) / len(cards)
                    avg_stability = sum(c.get("stability", 1) or 1 for c in cards) / len(cards)
                    # More lapses = higher weight, lower stability = higher weight
                    weight = max(1.0, 1.0 + avg_lapses - (avg_stability / 10.0))
            except Exception:
                pass
            weighted.append((concept, weight))

        # Weighted random selection
        total_weight = sum(w for _, w in weighted)
        r = random.uniform(0, total_weight)
        cumulative = 0
        chosen = weighted[0][0]
        for concept, weight in weighted:
            cumulative += weight
            if r <= cumulative:
                chosen = concept
                break

        # Get concept content
        content = storage.courses.get_concept_content(chosen["_course_uid"], chosen["uid"])
        if not content or len(content.strip()) < 20:
            content = f"Topic: {chosen.get('title', 'Unknown')}"

        # Generate question via LLM
        sys_prompt = (
            "You are an expert examiner. Generate ONE challenging but fair question "
            "that tests deep understanding of the concept. The question should require "
            "explanation, not just recall. Output ONLY the question text, nothing else."
        )
        user_prompt = f"Generate a test question about this concept:\n\n{content[:3000]}"

        # 200 TOKENS DOES NOT HOLD A QUESTION THIS PROMPT ASKS FOR.
        #
        # The system prompt asks for a question that "requires explanation, not
        # just recall", and the model obliges with a scenario, a table
        # definition and two or three parts. Measured on a live SQL quiz: the
        # question ended mid-clause at "...fails to meet the business
        # requirement for `NULL`" -- the budget ran out, and nothing checked,
        # because the only guard is a 10-character floor.
        question = llm_generate(user_prompt, sys_prompt=sys_prompt, max_tokens=700)
        if not question or len(question.strip()) < 10:
            question = f"Explain the key principles of {chosen.get('title', 'this concept')} and give an example."

        # THE QUIZ WAS THE ONE SURFACE STILL SERVING RAW TeX.
        #
        # Three surfaces render concept text and each handled math differently:
        # the Socratic session runs it through KaTeX (learn.html vendors it),
        # review items are demathed to plain text server-side, and the quiz
        # passed the model's output straight through to `textContent`. KaTeX is
        # not loaded on the practice page, so a learner was shown
        # "$O(N \log N \cdot \log_k M)$" literally, dollar signs and all.
        #
        # Demathing here rather than in the client keeps this consistent with
        # the review path and fixes every consumer of the endpoint at once.
        return jsonify({
            "question": demath(question.strip()),
            "concept_uid": chosen["uid"],
            "concept_title": chosen.get("title", ""),
            "course_uid": chosen["_course_uid"],
            "course_title": chosen.get("_course_title", ""),
            "context_text": content[:3000],
        })

    except Exception as e:
        logger.error(f"Quiz generation error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# THE QUIZ GRADER ASKED FOR A LIST AND THEN READ IT AS AN OBJECT.
#
# llm_generate_json defaults to expected_type="list". The grader never
# overrode it, so the helper steered the model to a JSON array, returned one,
# and the very next line did `(result or {}).get("grade")` -- and a non-empty
# list is truthy, so it went straight into AttributeError: 'list' object has no
# attribute 'get'. Surfaced by running the tab: "Your answer was not graded
# ('list' object has no attribute 'get')".
#
# The failure was invisible because the handler below is careful -- it refuses
# to call an infrastructure failure a wrong answer -- so every quiz answer came
# back ungraded with a polite message instead of an error anyone chased.
#
# /api/review/check_answer next door already passes expected_type="dict" with a
# schema. Same treatment here.
_QUIZ_GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["PASS", "FAIL"]},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "feedback": {"type": "string"},
        "missing_concepts": {"type": "array", "items": {"type": "string"}},
        "key_point": {"type": "string"},
    },
    "required": ["grade", "feedback"],
}


@app.route("/api/quiz/grade", methods=["POST"])
def quiz_grade_endpoint():
    """Grade a quiz answer and optionally create/prioritize flashcards for weak areas.

    Anki philosophy: wrong quiz answers should generate flashcards so the user
    can review weak concepts via spaced repetition.
    """
    data = request.json
    question = data.get("question", "")
    answer = data.get("answer", "")
    context = data.get("context", "")
    concept_uid = data.get("concept_uid", "")
    course_uid = data.get("course_uid", "")

    if not question or not answer:
        return jsonify({"error": "Missing question or answer"}), 400

    try:
        from services.common.llm_utils import llm_generate_json

        sys_prompt = (
            "You are a strict but fair grader. Evaluate the student's answer "
            "against the reference material. Output JSON with these fields:\n"
            '{"grade": "PASS" or "FAIL", "score": 0-100, '
            '"feedback": "brief explanation", '
            '"missing_concepts": ["concept1", "concept2"], '
            '"key_point": "the most important thing the student should remember"}'
        )
        user_prompt = (
            f"REFERENCE MATERIAL:\n{context[:2000]}\n\n"
            f"QUESTION: {question}\n\n"
            f"STUDENT ANSWER: {answer}\n\n"
            "Grade this answer. Be fair but rigorous."
        )

        result = llm_generate_json(user_prompt, sys_prompt=sys_prompt,
                                   max_tokens=400, expected_type="dict",
                                   schema=_QUIZ_GRADE_SCHEMA)
        # Tolerate the wrapper the model sometimes adds anyway, the same way
        # the skeleton builder does: a one-element array holding the object.
        if isinstance(result, list):
            result = next((r for r in result if isinstance(r, dict)), None)
        if not isinstance(result, dict):
            result = None

        # An infrastructure failure is not an assessment.
        #
        # This used to substitute {"grade": "FAIL", "score": 0} whenever the LLM
        # returned nothing, and return it with HTTP 200 as though grading had
        # happened. Two things then went wrong at once: the student saw a red ✗
        # for an answer that was very possibly correct, and — because the FAIL
        # branch below downgrades existing cards — up to five of their EXISTING
        # flashcards for that concept were graded "Again" (rating 1) through
        # FSRS. A single Ollama hiccup mid-quiz therefore did permanent damage
        # to a review schedule the model never actually looked at.
        #
        # Grading either happened or it did not. When it did not we say so with
        # a distinct, named error and a non-2xx status so no client can mistake
        # it for a verdict, and we return before touching FSRS at all.
        raw_grade = (result or {}).get("grade")
        grade = raw_grade.strip().upper() if isinstance(raw_grade, str) else ""
        if grade not in ("PASS", "FAIL"):
            # Covers both a falsy result (LLM or transport down) and a
            # structurally valid response with no usable verdict in it. Both
            # mean "we do not know", which is never "the student was wrong".
            logger.warning(
                "Quiz grading unavailable — no usable verdict from the grader "
                "(result=%r). No grade reported, FSRS schedule untouched.",
                result,
            )
            return jsonify({
                "error": "grading_unavailable",
                "error_code": "GRADING_UNAVAILABLE",
                "graded": False,
                "retryable": True,
                "message": (
                    "The grader is unavailable right now. Your answer was not "
                    "graded, and nothing in your review schedule changed. "
                    "Please try again."
                ),
            }), 503

        # Past this point a real verdict exists, so the Anki side effects below
        # are acting on an actual assessment.
        result["grade"] = grade

        try:
            score = int(float(result.get("score", 0)))
        except (TypeError, ValueError):
            # A malformed score is cosmetic — the verdict above is what drives
            # the FSRS side effects — so clamp it rather than fail the request.
            score = 0
        score = max(0, min(100, score))
        result["score"] = score

        missing = result.get("missing_concepts") or []
        if not isinstance(missing, list):
            missing = []
        key_point = result.get("key_point", "")

        cards_created = 0
        if grade == "FAIL" and concept_uid and course_uid:
            try:
                # Check if flashcards already exist for this concept
                existing = storage.flashcards.get_cards_for_concept(concept_uid)
                if len(existing) < 8:  # Don't create too many
                    # Create a card from the question itself
                    if key_point:
                        storage.flashcards.add_card(
                            course_uid, concept_uid,
                            front=question,
                            back=key_point,
                        )
                        cards_created += 1

                    # Create cards for each missing concept
                    for mc in missing[:3]:
                        if mc and len(mc) > 5:
                            storage.flashcards.add_card(
                                course_uid, concept_uid,
                                front=f"Explain: {mc}",
                                back=f"Review the concept of {mc} in your course materials.",
                            )
                            cards_created += 1

                    # Downgrade existing cards' stability (they need more review).
                    # Only ever reached on a real FAIL verdict — see the
                    # grading_unavailable guard above.
                    if existing:
                        from services.core.fsrs_engine import FSRSEngine
                        fsrs = FSRSEngine()
                        for card in existing[:5]:
                            try:
                                storage.flashcards.grade_card_fsrs(card["uid"], 1, fsrs)
                            except Exception as card_err:
                                # One card failing to reschedule should not abort
                                # the rest, but it must not vanish either — a
                                # silent skip here is a review that never comes
                                # back.
                                logger.warning(
                                    "Could not downgrade card %s after a quiz FAIL: %s",
                                    card.get("uid"), card_err,
                                )

            except Exception as e:
                logger.warning(f"Failed to create quiz-based flashcards: {e}")

        result["cards_created"] = cards_created
        if cards_created > 0:
            result["flashcard_note"] = f"{cards_created} flashcard(s) added for review"

        # Explicit positive marker so a client never has to infer "this was
        # really graded" from the mere presence of a `grade` key.
        result["graded"] = True

        return jsonify(result)

    except Exception as e:
        logger.error(f"Quiz grading error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/auto_generate_flashcards", methods=["POST"])
def auto_generate_flashcards_endpoint():
    """Auto-generate flashcards for a concept on completion.

    Called by FSM when a concept is completed in Socratic mode.
    Checks if cards already exist to avoid duplicates.
    """
    data = request.json
    course_uid = data.get("course_uid")
    concept_uid = data.get("concept_uid")
    concept_title = data.get("concept_title", "")
    socratic_grade = data.get("grade", 3)

    if not course_uid:
        return jsonify({"error": "Missing course_uid"}), 400

    # If no concept_uid, generate for all concepts in the course (manual trigger)
    if not concept_uid:
        try:
            from services.common.llm_utils import llm_generate, extract_python_list
            concepts = storage.courses.get_flat_concepts(course_uid)
            total_created = 0
            for concept in concepts[:20]:  # Cap to avoid LLM overload
                try:
                    existing = storage.flashcards.get_cards_for_concept(concept["uid"])
                    if len(existing) >= 3:
                        continue
                    content = storage.courses.get_concept_content(course_uid, concept["uid"])
                    if not content or len(content.strip()) < 30:
                        continue
                    sys_prompt = (
                        "You are an Anki flashcard expert. Create high-quality flashcards.\n"
                        "- Minimum information: each card tests ONE atomic fact\n"
                        "- No ambiguity: questions have exactly one correct answer\n"
                        "Output STRICT JSON array: [{\"front\": \"Q\", \"back\": \"A\"}]"
                    )
                    user_prompt = (
                        f"Create 3 Anki flashcards from this concept:\n\n"
                        f"TITLE: {concept.get('title', '')}\n\n"
                        f"CONTENT:\n{content[:3000]}"
                    )
                    raw = llm_generate(user_prompt, sys_prompt=sys_prompt, max_tokens=600)
                    cards_data = extract_python_list(raw)
                    if cards_data:
                        for card in cards_data:
                            front = card.get("front", "").strip()
                            back = card.get("back", "").strip()
                            if front and back:
                                storage.flashcards.add_card(course_uid, concept["uid"], front, back)
                                total_created += 1
                except Exception as ce:
                    logger.warning(f"Flashcard gen failed for {concept.get('uid')}: {ce}")
            return jsonify({"status": "ok", "cards_created": total_created})
        except Exception as e:
            logger.error(f"Bulk flashcard gen error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    try:
        # Check if cards already exist
        existing = storage.flashcards.get_cards_for_concept(concept_uid)
        if len(existing) >= 3:
            return jsonify({
                "status": "skipped",
                "message": f"Cards already exist for {concept_title}",
                "existing_count": len(existing),
            })

        # Get concept content
        content = storage.courses.get_concept_content(course_uid, concept_uid)
        if not content or len(content.strip()) < 30:
            return jsonify({"status": "skipped", "message": "Insufficient content"})

        from services.common.llm_utils import llm_generate, extract_python_list

        sys_prompt = (
            "You are an Anki flashcard expert. Create high-quality flashcards following "
            "these principles:\n"
            "- Minimum information: each card tests ONE atomic fact\n"
            "- No ambiguity: questions have exactly one correct answer\n"
            "- Context-independent: cards make sense on their own\n"
            "- Use cloze-deletion style where appropriate\n"
            "Output STRICT JSON array: [{\"front\": \"Q\", \"back\": \"A\"}]"
        )
        user_prompt = (
            f"Create 3-5 Anki flashcards from this concept:\n\n"
            f"TITLE: {concept_title}\n\n"
            f"CONTENT:\n{content[:4000]}"
        )

        raw = llm_generate(user_prompt, sys_prompt=sys_prompt, max_tokens=800)
        cards_data = extract_python_list(raw)

        if not cards_data:
            return jsonify({"status": "failed", "message": "LLM failed to generate cards"})

        added = []
        for card in cards_data:
            front = card.get("front", "").strip()
            back = card.get("back", "").strip()
            if front and back:
                uid = storage.flashcards.add_card(course_uid, concept_uid, front, back)
                added.append({"uid": uid, "front": front, "back": back})

        # Set initial FSRS scheduling based on Socratic grade
        if added:
            try:
                from services.core.fsrs_engine import FSRSEngine
                fsrs = FSRSEngine()
                # Map Socratic grade (1-5) to FSRS rating (1-4)
                fsrs_rating = max(1, min(4, socratic_grade))
                for card_info in added:
                    try:
                        storage.flashcards.grade_card_fsrs(card_info["uid"], fsrs_rating, fsrs)
                    except Exception as e:
                        # THIS IS THE WRITE THAT SCHEDULES THE REVIEW.
                        #
                        # It was `except Exception: pass` — silent at every log
                        # level. The card gets created and never scheduled, and
                        # nothing distinguishes that from "not due yet", so a
                        # concept the learner completed simply never comes back.
                        # That is READY_FOR_USE D4 failing invisibly.
                        logger.error(
                            "could not schedule review for card %s (%s) — the "
                            "concept was completed but will not come up for "
                            "review", card_info.get("uid"), e)
            except Exception as e:
                logger.warning(f"FSRS initial scheduling failed: {e}")

        return jsonify({
            "status": "ok",
            "cards": added,
            "count": len(added),
        })

    except Exception as e:
        logger.error(f"Auto flashcard generation error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


#: Courses whose hydration is being re-run right now, so a second click (or a
#: second tab) cannot start a competing hydrator over the same files.
_RESUMING = set()
_RESUMING_LOCK = __import__("threading").Lock()

#: Statuses a resume is allowed to act on. "ready" is excluded deliberately:
#: re-running hydration over a finished course would spend an hour of model
#: time to rewrite content that is already good.
# `needs_review` is the audit gate's verdict: the build finished but the course
# is not teachable as it stands. Resume is exactly the right remedy — it
# re-hydrates what is missing rather than discarding the concepts that are fine.
_RESUMABLE = ("partial", "hydration_failed", "skeleton", "building", "failed",
              "needs_review")


@app.route("/api/course/<course_uid>/resume_build", methods=["POST"])
def resume_build(course_uid):
    """Finish a course whose build stopped short, without rebuilding it.

    WHY THIS EXISTS
    ---------------
    `hydrate()` marks a course "partial" when even ONE concept comes back a
    stub, and `courses.js` renders anything that is not "ready" as a disabled
    card. So a single failed concept in a hundred left a course permanently
    unopenable, with no way forward but Delete and build the whole thing again
    — discarding every concept that HAD hydrated, which on this hardware is
    hours of model time.

    Hydration already skips concepts that have content, so resuming costs only
    the concepts that actually failed. The expensive, correct thing was
    already implemented; nothing exposed it.

    Returns 202 and works in the background: on this hardware even a handful of
    concepts outlives any sensible request timeout.
    """
    try:
        course = storage.courses.get_course(course_uid)
    except Exception as e:
        logger.error(f"resume_build: cannot read {course_uid}: {e}")
        return jsonify({"error": "course could not be read"}), 500
    if not course:
        return jsonify({"error": "no such course"}), 404

    # THE CONTENT IS THE TRUTH; THE STATUS IS A CACHED JUDGEMENT ABOUT IT.
    #
    # This refused on status alone, so a course marked "ready" that is missing
    # a concept — because the concept was cleared, a write failed, or the
    # status was written before the last one landed — could not be resumed at
    # all. The reply said "nothing to resume" while a concept sat empty, and
    # the pipeline handback above it reported "resuming" to its own caller, so
    # two layers disagreed and the learner's course stayed broken.
    #
    # Counting what is actually missing costs one read per concept and cannot
    # disagree with reality. If nothing is missing, "ready" is right and there
    # is genuinely nothing to do; if something is missing, that is precisely
    # what resume exists for, whatever the status says.
    missing = 0
    for m in course.get("modules") or []:
        for u in m.get("units") or []:
            for l in u.get("lessons") or []:
                for c in l.get("concepts") or []:
                    try:
                        body = storage.courses.get_concept_content(
                            course_uid, c.get("uid")) or ""
                    except Exception:
                        body = ""
                    if len(body.split()) < 40:
                        missing += 1

    status = (course.get("status") or "").lower()
    if not missing:
        return jsonify({"status": status or "ready",
                        "message": "nothing to resume"}), 200
    if status == "ready":
        logger.warning(
            "%s is marked ready but %d concept(s) have no content — resuming "
            "anyway and letting the finalize verdict reset the status",
            course_uid, missing)
    elif status not in _RESUMABLE:
        return jsonify({"error": f"cannot resume a course in state {status!r}"}), 409

    with _RESUMING_LOCK:
        if course_uid in _RESUMING:
            return jsonify({"status": "already_resuming"}), 202
        _RESUMING.add(course_uid)

    # SAY THAT IT IS BUILDING WHILE IT BUILDS.
    #
    # A course reaped as "failed" and then resumed kept advertising "failed"
    # for the hours the resume took, so the course list told a learner their
    # course was broken while it was actively being written. The status is
    # what the UI renders from; leaving it stale is the same class of defect
    # as leaving it wrong.
    # READ, MODIFY, WRITE. update_course OVERWRITES structure.json with the
    # dict it is given — passing {"status": "building"} would replace the
    # entire course with a three-key stub and destroy every module in it.
    try:
        _c = storage.courses.get_course(course_uid)
        if _c:
            _c["status"] = "building"
            storage.courses.update_course(course_uid, _c)
    except Exception as e:
        logger.warning("could not mark %s as building: %s", course_uid, e)

    # CLAIM THE BUILD SLOT, OR THE REAPER TAKES THE COURSE.
    #
    # background_ops' stale-build reaper skips whatever build_state.current()
    # names as live. resume_build never claimed it, so `live_uid` was always
    # None, the guard never matched, and the reaper stamped
    # status="failed", error="Course creation timed out (>1 hour)" on a course
    # that was actively hydrating — measured four times in twelve minutes on
    # 2026-08-25, one of them fourteen seconds after this function started the
    # hydration it was reaping.
    #
    # Its cutoff is measured from created_at, so a course created hours ago is
    # permanently past it and the reap repeats every 300s for the life of the
    # build. Claiming the slot is what makes the guard work.
    _build_id = None
    try:
        from services.common import build_state
        _build_id = build_state.start(
            (course or {}).get("title") or course_uid,
            course_uid=course_uid, source="resume", stage="hydration")
        if _build_id is None:
            logger.info("resume for %s proceeds without the build slot — "
                        "another build owns it", course_uid)
    except Exception as e:
        logger.warning("could not claim the build slot for %s: %s — the stale "
                       "reaper may mark this course failed while it builds",
                       course_uid, e)

    def _run():
        try:
            from services.core.course_builder import ContentHydrator
            h = ContentHydrator(status_callback=_update_status, course_depth=3,
                                storage=storage)
            try:
                h.hydrate(course_uid)
            finally:
                h.close()
            after = (storage.courses.get_course(course_uid) or {}).get("status")
            logger.info(f"[RESUME] {course_uid} finished with status {after!r}")
        except Exception as e:
            logger.error(f"[RESUME] {course_uid} failed: {e}", exc_info=True)
        finally:
            # Release the slot whatever happened, or the NEXT build is refused.
            if _build_id is not None:
                try:
                    from services.common import build_state
                    build_state.finish(course_uid=course_uid, build_id=_build_id)
                except Exception as e:
                    logger.warning("could not release the build slot for %s: %s",
                                   course_uid, e)
            with _RESUMING_LOCK:
                _RESUMING.discard(course_uid)

    __import__("threading").Thread(target=_run, daemon=True,
                                   name=f"resume-{course_uid}").start()
    return jsonify({"status": "resuming", "course_uid": course_uid}), 202


@app.route("/api/custom_course/preview", methods=["POST"])
def preview_custom_course():
    """Generate a preview structure for a custom course without committing."""
    try:
        if not request.json:
            return jsonify({"error": "Request must contain JSON data"}), 400

        data = request.json
        title = data.get("title", "").strip()
        teaching_style = data.get("teaching_style", "").strip()
        modules = data.get("modules", [])

        if not title:
            return jsonify({"error": "Course title is required"}), 400

        if not modules or not isinstance(modules, list):
            return jsonify({"error": "At least one module is required"}), 400

        if len(modules) > 10:
            return jsonify({"error": "Maximum 10 modules allowed"}), 400

        for idx, module in enumerate(modules):
            if not isinstance(module, dict):
                return jsonify({"error": f"Module {idx + 1} has invalid format"}), 400
            m_title = module.get("title", "").strip()
            if not m_title:
                return jsonify({"error": f"Module {idx + 1} requires a title"}), 400
            m_depth = module.get("depth", 3)
            if not isinstance(m_depth, int) or m_depth < 1 or m_depth > 5:
                return jsonify(
                    {"error": f"Module {idx + 1} depth must be between 1 and 5"}
                ), 400

        logger.info(
            f"Generating preview structure for '{title}' with {len(modules)} modules"
        )
        _bind_status_owner()

        # Instantiate builder once
        from services.core.course_builder import SkeletonBuilder
        builder = SkeletonBuilder(storage=storage, status_callback=_update_status)

        structure = {
            "course_uid": f"preview_{uuid.uuid4().hex[:8]}",
            "title": title,
            "teaching_style": teaching_style,
            "modules": [],
        }

        for module_idx, module in enumerate(modules):
            m_title = module.get("title", "").strip()
            m_context = module.get("context", "").strip()
            m_depth = module.get("depth", 3)

            _update_status(
                f"Architecting module {module_idx + 1}/{len(modules)}: {m_title}"
            )

            try:
                # Use the new shared helper in SkeletonBuilder
                module_structure = builder.generate_preview_for_module(
                    m_title, m_depth, topic=title, m_context=m_context
                )
                structure["modules"].append(module_structure)
            except Exception as module_err:
                logger.error(f"Failed to generate module {m_title}: {module_err}")
                structure["modules"].append(
                    {
                        "title": m_title,
                        "context": m_context,
                        "depth": m_depth,
                        "units": [
                            {
                                "title": f"{m_title} Fundamentals",
                                "lessons": [
                                    {
                                        "title": f"Introduction to {m_title}",
                                        "concepts": ["Overview", "Key Principles"],
                                    }
                                ],
                            }
                        ],
                    }
                )

        return jsonify({"status": "ok", "structure": structure})
    except Exception as e:
        logger.error(f"Preview generation failed: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route("/api/custom_course/create", methods=["POST"])
def create_custom_course_wizard():
    """Create a custom course using the provided structure and hydrate content."""
    course_uid = None
    hydrator = None

    try:
        if not request.json:
            return jsonify({"error": "Request must contain JSON data"}), 400

        data = request.json
        title = data.get("title", "").strip()
        description = data.get("description", "").strip()
        teaching_style = data.get("teaching_style", "").strip()
        modules = data.get("modules", [])
        structure_data = data.get("structure", {})

        if not title:
            return jsonify({"error": "Course title is required"}), 400
        if not modules or not isinstance(modules, list):
            return jsonify({"error": "At least one module is required"}), 400
        if not structure_data or not isinstance(structure_data, dict):
            return jsonify({"error": "Course structure is required"}), 400

        logger.info(f"Creating custom course '{title}' with {len(modules)} modules")
        # Pin the owner before any status emit: hydration below calls back from
        # ThreadPoolExecutor workers that have no request context.
        _bind_status_owner()
        _update_status(f"Creating course: {title}")

        course_uid = f"course_{uuid.uuid4().hex[:8]}"

        # Build course dict from preview structure
        course_dict = {
            "uid": course_uid,
            "title": title,
            "overview": description or f"Custom course: {title}",
            "teaching_style": teaching_style,
            "status": "building",
            "modules": [],
        }

        total_concepts = 0
        module_ordinal = 1

        # WIZ-1: Match modules by title when possible, fall back to positional
        struct_modules = structure_data.get("modules", [])
        spec_by_title = {m.get("title", "").strip().lower(): m for m in modules if m.get("title")}

        # ICW-6: Collect user-defined concept titles for source attribution
        # ICW-7: Collect user notes per concept and per module for content influence
        user_concept_titles = set()
        user_concept_notes = {}  # title_lower → note
        user_module_notes = {}   # title_lower → note
        for m_spec in modules:
            m_note = m_spec.get("note", "").strip()
            m_title_key = m_spec.get("title", "").strip().lower()
            if m_note and m_title_key:
                user_module_notes[m_title_key] = m_note
            for c_spec in m_spec.get("concepts", []):
                c_title = c_spec.get("title", "").strip().lower() if isinstance(c_spec, dict) else str(c_spec).strip().lower()
                if c_title:
                    user_concept_titles.add(c_title)
                    c_note = c_spec.get("note", "").strip() if isinstance(c_spec, dict) else ""
                    if c_note:
                        user_concept_notes[c_title] = c_note

        if len(struct_modules) != len(modules):
            logger.warning(f"Module count mismatch: structure has {len(struct_modules)}, specs have {len(modules)}")

        for idx, module_data in enumerate(struct_modules):
            m_uid = f"mod_{uuid.uuid4().hex[:8]}"
            m_title = module_data.get("title", "").strip()

            # Try title-based match first, fall back to positional
            module_spec = spec_by_title.get(m_title.lower(), modules[idx] if idx < len(modules) else {})
            m_source_file = module_spec.get("source_file", "")

            if not m_title:
                continue

            _update_status(f"STRUCT:MODULE:{m_title}")

            module_dict = {
                "uid": m_uid,
                "title": m_title,
                "ordinal": module_ordinal,
                "source_file": m_source_file or "",
                "user_note": user_module_notes.get(m_title.lower(), ""),
                "units": [],
            }
            module_ordinal += 1

            unit_ordinal = 1
            for unit_data in module_data.get("units", []):
                u_uid = f"unit_{uuid.uuid4().hex[:8]}"
                u_title = unit_data.get("title", "").strip()
                if not u_title:
                    continue

                _update_status(f"STRUCT:UNIT:{u_title}")

                unit_dict = {
                    "uid": u_uid,
                    "title": u_title,
                    "ordinal": unit_ordinal,
                    "lessons": [],
                }
                unit_ordinal += 1

                lesson_ordinal = 1
                for lesson_data in unit_data.get("lessons", []):
                    l_uid = f"less_{uuid.uuid4().hex[:8]}"
                    l_title = lesson_data.get("title", "").strip()
                    if not l_title:
                        continue

                    _update_status(f"STRUCT:LESSON:{l_title}")

                    lesson_dict = {
                        "uid": l_uid,
                        "title": l_title,
                        "ordinal": lesson_ordinal,
                        "concepts": [],
                    }
                    lesson_ordinal += 1

                    concept_ordinal = 1
                    for concept_title in lesson_data.get("concepts", []):
                        concept_title = (
                            concept_title.strip()
                            if isinstance(concept_title, str)
                            else str(concept_title)
                        )
                        if not concept_title:
                            continue

                        c_uid = f"con_{uuid.uuid4().hex[:8]}"
                        _update_status(f"STRUCT:CONCEPT:{c_uid}:{concept_title}")

                        # ICW-6: Mark user-defined vs generated concepts
                        # ICW-7: Include user notes for content influence during hydration
                        concept_source = "user" if concept_title.strip().lower() in user_concept_titles else "generated"
                        concept_note = user_concept_notes.get(concept_title.strip().lower(), "")
                        lesson_dict["concepts"].append(
                            {
                                "uid": c_uid,
                                "title": concept_title,
                                "ordinal": concept_ordinal,
                                "learning_objectives": [],
                                "source": concept_source,
                                "user_note": concept_note,
                            }
                        )
                        concept_ordinal += 1
                        total_concepts += 1

                    unit_dict["lessons"].append(lesson_dict)
                module_dict["units"].append(unit_dict)
            course_dict["modules"].append(module_dict)

        # Write course JSON
        storage.courses.create_course(course_dict)
        logger.info(
            f"Course structure created: {module_ordinal - 1} modules, {total_concepts} concepts"
        )
        _update_status(
            f"Structure created with {total_concepts} concepts. Starting content hydration..."
        )

        # Hydrate content via LLM (ZIM/Kolibri removed)
        try:
            from services.core.course_builder import ContentHydrator
            hydrator = ContentHydrator(
                providers=[],
                status_callback=_update_status,
                course_depth=3,
                storage=storage,
            )
            hydrator.hydrate(course_uid)
            logger.info(f"Content hydration completed for course {course_uid}")
            # RE-READ THE HYDRATED COPY, AND RESPECT THE VERDICT IT RECORDED.
            #
            # hydrate() re-reads the course itself and writes its own copy back
            # (course_builder.ContentHydrator.hydrate), carrying depth_contract,
            # fact_check, level_calibration, grounding, assets, hydrated_count,
            # per-concept source_confidence — and the status it decided on.
            # `course_dict` here is the PRE-hydration snapshot taken before any
            # of that existed; json-dumping it back erased every quality verdict,
            # after which the tutor saw stub concepts as llm_fallback=False /
            # source_confidence=None. Always re-read and edit the hydrated copy.
            hydrated = storage.courses.get_course(course_uid)
            if not hydrated:
                # hydrate() returns silently (no raise) when the course is
                # missing, so a successful return proves nothing on its own.
                # Never stamp "ready" on a course that was never hydrated.
                raise RuntimeError(
                    f"Course {course_uid} not found after hydration — "
                    "nothing was hydrated"
                )
            # And never stamp "ready" OVER a verdict of "partial"/"failed":
            # hydrate() sets those when concepts came back as stubs, which is
            # exactly the honesty the stub gate exists to provide.
            if hydrated.get("status") not in ("ready", "partial", "failed"):
                hydrated["status"] = "ready"
                storage.courses.update_course(course_uid, hydrated)
        except Exception as hydration_err:
            logger.error(f"Content hydration failed: {hydration_err}", exc_info=True)
            # WIZ-4: Mark as "partial" not "failed" — allows user to retry hydration.
            # Re-read for the same reason as the success path: the hydrator
            # persists concepts as it goes, so the stale pre-hydration
            # course_dict would throw away whatever did complete. Fall back to
            # it only if the course is gone from storage entirely.
            partial = storage.courses.get_course(course_uid) or course_dict
            partial["status"] = "partial"
            storage.courses.update_course(course_uid, partial)
            raise Exception(f"Content hydration failed: {str(hydration_err)}")

        _update_status(f"Course '{title}' created successfully!")

        return jsonify(
            {
                "status": "ok",
                "course_uid": course_uid,
                "message": "Course created successfully",
                "stats": {"modules": module_ordinal - 1, "concepts": total_concepts},
            }
        )

    except Exception as e:
        logger.error(f"Custom course creation failed: {e}", exc_info=True)

        if course_uid:
            try:
                course = storage.courses.get_course(course_uid)
                if course:
                    # A hydration failure already marked this "partial" (WIZ-4)
                    # and then re-raised through here — overwriting that with
                    # "failed" made WIZ-4 a no-op and handed the half-built
                    # course to course_cleaner, which deletes "failed" on the
                    # next restart. Only stamp "failed" on courses that never
                    # got that far.
                    if course.get("status") != "partial":
                        course["status"] = "failed"
                    course["error"] = str(e)[:500]
                    storage.courses.update_course(course_uid, course)
            except Exception as e:
                logger.warning(f"Failed to mark course {course_uid} as failed: {e}")

        if hydrator:
            try:
                hydrator.close()
            except Exception as e:
                logger.warning(f"Failed to close hydrator during cleanup: {e}")

        return jsonify({"error": f"Course creation failed: {str(e)}"}), 500


# ── Memory Palace Endpoints ──────────────────────────────────────────

@app.route("/palace/start", methods=["GET"])
def palace_start():
    """Return the first locus (concept) for a course's Memory Palace."""
    course_uid = request.args.get("course_uid", "")
    if not course_uid:
        return jsonify({"error": "course_uid required"}), 400

    try:
        concepts = storage.courses.get_flat_concepts(course_uid)
        if not concepts:
            return jsonify({"error": "No concepts found in this course"}), 404

        first = concepts[0]
        # Check if this locus has an anchored concept
        anchor = _get_anchor_for_locus(course_uid, first["uid"])

        return jsonify({
            "uid": first["uid"],
            "description": first.get("title", "Unknown Location"),
            "index": 0,
            "total": len(concepts),
            "has_concept": anchor is not None,
            "concept_title": anchor.get("concept_text") if anchor else None,
            "sensory_text": anchor.get("sensory_text") if anchor else None,
        })
    except Exception as e:
        logger.error(f"Palace start error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/locus/next", methods=["GET"])
def locus_next():
    """Return the next locus in course order."""
    current_uid = request.args.get("current", "")
    course_uid = request.args.get("course_uid", "")
    if not course_uid:
        return jsonify({"error": "course_uid required"}), 400

    try:
        concepts = storage.courses.get_flat_concepts(course_uid)
        if not concepts:
            return jsonify({"error": "No concepts found"}), 404

        # Find current position
        current_idx = 0
        for i, c in enumerate(concepts):
            if c["uid"] == current_uid:
                current_idx = i
                break

        # Advance with wrapping
        next_idx = (current_idx + 1) % len(concepts)
        nxt = concepts[next_idx]
        anchor = _get_anchor_for_locus(course_uid, nxt["uid"])

        return jsonify({
            "uid": nxt["uid"],
            "description": nxt.get("title", "Unknown Location"),
            "index": next_idx,
            "total": len(concepts),
            "has_concept": anchor is not None,
            "concept_title": anchor.get("concept_text") if anchor else None,
            "sensory_text": anchor.get("sensory_text") if anchor else None,
        })
    except Exception as e:
        logger.error(f"Locus next error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/anchor", methods=["POST"])
def anchor_concept():
    """Save a sensory anchor for a concept at a locus."""
    data = request.json or {}
    course_uid = data.get("course_uid", "")
    locus_uid = data.get("locus_uid", "")
    concept_text = data.get("concept_text", "")
    sensory_text = data.get("sensory_text", "")

    if not locus_uid or not concept_text:
        return jsonify({"error": "locus_uid and concept_text required"}), 400

    try:
        storage.activity.log_activity(
            course_uid=course_uid,
            activity_type="palace_anchor",
            concept_uid=locus_uid,
            details={
                "concept_text": concept_text,
                "sensory_text": sensory_text,
                "locus_uid": locus_uid,
            },
        )
        return jsonify({"success": True, "message": f"Anchored '{concept_text}' at this locus"})
    except Exception as e:
        logger.error(f"Anchor save error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _get_anchor_for_locus(course_uid: str, locus_uid: str) -> dict:
    """Retrieve the most recent palace anchor for a specific locus.

    This returned the OLDEST anchor, not the newest. get_activities() orders
    newest-first, and the loop below iterated reversed(activities) — so the
    first match was the earliest anchor ever placed at that locus. Re-anchoring
    silently did nothing: the learner was stuck with their first association
    forever, which is exactly the thing memory-palace practice tells you to
    revise when an image is not sticking.
    """
    try:
        activities = storage.activity.get_activities(
            course_uid=course_uid,
            activity_type="palace_anchor",
        )
        # get_activities is newest-first; take the first match.
        for act in activities:
            details = act.get("details", {})
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    continue
            if details.get("locus_uid") == locus_uid:
                return details
    except Exception as e:
        logger.warning(f"Anchor lookup failed: {e}")
    return None


# --- VG-02: Due Concepts API ---

# --- A5.2: ASK — free-form questions across everything the learner has -------
#
# Socratic dialogue existed ONLY inside a concept node; there was no free-chat
# endpoint anywhere in the system. A learner could not ask a question that
# spanned their courses, which is the single largest gap versus just using a
# chatbot — and the FSM already does the hard part, so what was missing was an
# entry point that is not a lesson.
#
# The differentiator is that this answers FROM THE LEARNER'S OWN MATERIAL and
# says which concepts it used. When retrieval finds nothing it says so instead
# of free-associating: a tutor that invents an answer outside the syllabus is
# just a chatbot with extra steps, and the learner cannot tell the difference.

ASK_SYSTEM = """You are Helga, a tutor answering a learner's question using \
THEIR OWN course material, which is supplied below.

Rules:
- Answer from the supplied material. It is what this learner has actually studied.
- If the material does not contain the answer, say so plainly in one sentence
  and then answer briefly from general knowledge, clearly marked as such.
  Never blur the two.
- Be direct. This is a question, not a Socratic lesson — do not answer with a
  question.
- Refer to concepts by name when you use them.
- 120 words or fewer unless the question genuinely needs more."""

ASK_MAX_CONTEXT = 4


@app.route("/api/ask", methods=["POST"])
def ask_endpoint():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    course_uid = data.get("course_uid") or None

    if not question:
        return jsonify({"error": "question required"}), 400
    if len(question) > 1000:
        return jsonify({"error": "question too long"}), 400

    # Retrieve across the learner's courses (or scope to one when asked from
    # inside it).
    try:
        rows = storage.search.search(question, course_uid=course_uid, limit=8) or []
    except Exception as e:
        logger.warning(f"ask: retrieval failed: {e}")
        rows = []

    sources, context = [], []
    for row in rows[:ASK_MAX_CONTEXT]:
        body = (row.get("content") or "").strip()
        if not body:
            continue
        title = row.get("title") or row.get("concept_uid")
        sources.append({
            "concept_uid": row.get("concept_uid"),
            "course_uid": row.get("course_uid"),
            "title": title,
        })
        context.append(f"### {title}\n{body[:1200]}")

    grounded = bool(context)
    material = ("\n\n".join(context) if grounded
                else "(No matching material was found in this learner's courses.)")

    try:
        from services.common.llm_utils import llm_generate
        answer = llm_generate(
            prompt=f"Course material:\n{material}\n\nQuestion: {question}",
            sys_prompt=ASK_SYSTEM,
            max_tokens=500,
        )
    except Exception as e:
        logger.error(f"ask: generation failed: {e}", exc_info=True)
        # Never fabricate an answer to hide an outage.
        return jsonify({"error": "the tutor service is not responding"}), 502

    if not answer or not answer.strip():
        return jsonify({"error": "the tutor returned an empty answer"}), 502

    return jsonify({
        "answer": answer.strip(),
        "sources": sources,
        "grounded": grounded,
    })


# ---------------------------------------------------------------- review queue

_CONCEPT_TITLES = {}


def _concept_title(concept_uid):
    """A concept's own title, for naming it to the learner.

    Cached per process: this is called only when an item becomes a leech, and
    the alternative is walking every course structure on each grade."""
    if not concept_uid:
        return ""
    if concept_uid in _CONCEPT_TITLES:
        return _CONCEPT_TITLES[concept_uid]
    try:
        for course in storage.courses.list_courses() or []:
            structure = storage.courses.get_course(course["uid"]) or {}
            for module in structure.get("modules", []) or []:
                for unit in module.get("units", []) or []:
                    for lesson in unit.get("lessons", []) or []:
                        for concept in lesson.get("concepts", []) or []:
                            if concept.get("uid"):
                                _CONCEPT_TITLES[concept["uid"]] = concept.get("title", "")
    except Exception as e:
        logger.warning("could not load concept titles: %s", e)
    return _CONCEPT_TITLES.get(concept_uid, "")


def _profile_value(key, default=None):
    """Read one Settings value from the table Settings actually writes to.

    THERE ARE TWO KEY-VALUE STORES IN THIS DATABASE. The Settings page writes
    `user_profile` (through PATCH /api/profile); `storage.settings` reads
    `user_settings`, which nothing on that page has ever written. Reading the
    wrong one returns the default forever and looks exactly like a working
    setting — the retention control changed nothing, and the daily cap has been
    ignoring the learner's goal for as long as it has existed.
    """
    conn = None
    try:
        conn = _get_profile_db()
        row = conn.execute(
            "SELECT value FROM user_profile WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    except Exception as e:
        logger.warning("could not read profile key %r: %s", key, e)
        return default
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _desired_retention():
    """The learner's retention target, from Settings.

    ONE reader. The older /api/grade_card_fsrs endpoint had 0.9 hard-coded, so
    a learner who chose "lighter" got their chosen schedule from one grading
    path and the default from the other.
    """
    try:
        value = float(_profile_value("desired_retention") or 0.9)
    except (TypeError, ValueError):
        return 0.9
    return min(0.97, max(0.70, value))


def _daily_cap(student_id):
    """The learner's own daily goal, in items rather than concepts.

    daily_goal is set in Settings as concepts per day; an item bank runs about
    a dozen items to a concept, so the cap is derived rather than invented.
    """
    try:
        goal = int(_profile_value("daily_goal") or 5)
    except Exception:
        goal = 5
    return max(10, min(200, goal * 12))


def _course_titles():
    """uid -> title, so the browser never has to render a course_uid at a
    learner. Cheap enough to do per request and always current."""
    out = {}
    try:
        for c in storage.courses.list_courses() or []:
            if c.get("uid"):
                out[c["uid"]] = c.get("title") or c["uid"]
    except Exception as e:
        logger.warning("course titles unavailable: %s", e)
    return out


def _maturity(row):
    """Delegates to the scheduler, where the bands and their thresholds live
    alongside the retirement rule that shares them."""
    from services.common.review_scheduler import maturity
    return maturity(row.get("repetitions"), row.get("interval_days"))


@app.route("/api/review/stats", methods=["GET"])
def review_bank_stats_endpoint():
    """The long-horizon picture: what is known, per course.

    The daily queue answers "what now". This answers "where am I", which is the
    question that keeps someone going through a twelve-course programme when no
    single day feels like progress.
    """
    student_id = request.args.get("student_id") or DEFAULT_STUDENT_ID
    try:
        rows = storage.flashcards.get_items(student_id=student_id)
    except Exception as e:
        logger.error("review stats failed: %s", e, exc_info=True)
        return jsonify({"error": "could not read the item bank"}), 503

    titles = _course_titles()
    from services.common.review_scheduler import MATURITY_BANDS
    bands = MATURITY_BANDS
    overall = {b: 0 for b in bands}
    per_course = {}
    kinds = {}

    for r in rows:
        band = _maturity(r)
        overall[band] += 1
        kinds[r.get("kind") or "recall"] = kinds.get(r.get("kind") or "recall", 0) + 1
        cu = r.get("course_uid") or ""
        entry = per_course.setdefault(cu, {
            "course_uid": cu, "title": titles.get(cu, cu or "Unfiled"),
            "total": 0, **{b: 0 for b in bands}})
        entry["total"] += 1
        entry[band] += 1

    total = sum(overall.values())
    for entry in per_course.values():
        settled = entry["mature"] + entry["retired"]
        entry["known_pct"] = round(100 * settled / entry["total"]) if entry["total"] else 0

    settled_all = overall["mature"] + overall["retired"]
    return jsonify({
        "total": total,
        "bands": overall,
        "known_pct": round(100 * settled_all / total) if total else 0,
        "kinds": kinds,
        "courses": sorted(per_course.values(),
                          key=lambda c: (-c["total"], c["title"])),
    })


def _as_due(row, today_iso):
    from services.common.review_scheduler import Due
    return Due(
        uid=row["uid"],
        concept_uid=row.get("concept_uid") or "",
        course_uid=row.get("course_uid") or "",
        kind=row.get("kind") or "recall",
        due_date=row.get("next_review_date") or today_iso,
        interval_days=float(row.get("interval_days") or 0),
        stability=row.get("stability"),
        lapses=int(row.get("lapses") or 0),
        repetitions=int(row.get("repetitions") or 0),
        depth=int(row.get("depth") or 0),
    )


@app.route("/api/review/queue", methods=["GET"])
def review_queue_endpoint():
    """Today's review queue: one interleaved, capped, prioritised list.

    Everything the learner is NOT being shown is reported alongside it. A capped
    day that does not say it was capped reads as "you are finished", which is
    the one thing this response must never imply.
    """
    from datetime import date as _date
    from services.common.review_scheduler import build_queue, NEW_ITEMS_PER_DAY

    student_id = request.args.get("student_id") or DEFAULT_STUDENT_ID
    course_uid = request.args.get("course_uid") or None
    today_iso = _date.today().isoformat()

    try:
        rows = storage.flashcards.get_items(course_uid=course_uid,
                                            student_id=student_id)
    except Exception as e:
        logger.error("review queue: item fetch failed: %s", e, exc_info=True)
        return jsonify({"error": "could not read the item bank"}), 503

    by_uid = {r["uid"]: r for r in rows}
    try:
        cap = _daily_cap(student_id)
        # The new-item allowance is a budget for the DAY, not for the request.
        # Without spending it down, finishing a session immediately produced
        # another twelve new items and the day could never be completed — a
        # treadmill, and the surest way to stop someone reviewing at all.
        # An item introduced today is one seen exactly once, today.
        today_str = today_iso
        introduced = sum(
            1 for r in rows
            if int(r.get("repetitions") or 0) == 1
            and (r.get("last_review_date") or "")[:10] == today_str)
        remaining_new = max(0, NEW_ITEMS_PER_DAY - introduced)
        # The mix of a new session follows the Bloom level of the concepts
        # actually on offer, so a course of analysis concepts does not hand out
        # a session of pure recall.
        bloom_of = {}
        for r in rows:
            cu = r.get("concept_uid")
            if cu:
                bloom_of[cu] = max(bloom_of.get(cu, 0), int(r.get("bloom") or 2))
        plan = build_queue([_as_due(r, today_iso) for r in rows],
                           daily_cap=cap, bloom_of=bloom_of,
                           new_per_day=remaining_new)
        plan["counts"]["new_today"] = introduced
        plan["counts"]["new_remaining"] = remaining_new
    except Exception as e:
        logger.error("review queue: scheduling failed: %s", e, exc_info=True)
        return jsonify({"error": "could not build the queue"}), 500

    titles = _course_titles()

    def present(due):
        row = by_uid.get(due.uid, {})
        payload = row.get("payload") or {}
        return {
            "course_title": titles.get(due.course_uid, ""),
            "uid": due.uid,
            "kind": due.kind,
            "bloom": row.get("bloom") or 2,
            "front": row.get("front") or "",
            "back": row.get("back") or "",
            "course_uid": due.course_uid,
            "concept_uid": due.concept_uid,
            "source_section": row.get("source_section") or "",
            "payload": payload,
            "lapses": due.lapses,
            "is_new": due.is_new,
        }

    # WHICH TIERS THIS SCOPE CAN ACTUALLY OFFER.
    #
    # The item bank is built by extraction, so a concept missing the sections
    # the tutor reads yields nothing to build discrimination, application or
    # Socratic items from -- only the prose fallback, which is recall. Measured:
    # "Reading a Query Plan" holds 26 items, all recall, and scoping review to
    # it returned twelve recall questions presented as an ordinary session.
    #
    # That is the one outcome this design exists to avoid. Factual-only
    # retrieval practice is the case where the evidence says transfer is no
    # better than not practising at all, and the learner had no way to tell
    # that apart from a full session. The queue now reports what the bank holds
    # so the surface can say so.
    try:
        tiers_present = sorted(storage.flashcards.kinds_in_scope(
            course_uid=course_uid or None, student_id=student_id))
    except Exception as e:
        logger.debug("tier summary unavailable: %s", e)
        tiers_present = []

    return jsonify({
        "queue": [present(d) for d in plan["queue"]],
        "counts": plan["counts"],
        "capped": plan["capped"],
        "new_paused_for_backlog": plan["new_paused_for_backlog"],
        "daily_cap": cap,
        "new_today": introduced,
        "new_per_day": NEW_ITEMS_PER_DAY,
        "leeches": [present(d) for d in plan["leeches"]],
        "tiers_present": tiers_present,
        "recall_only": tiers_present == ["recall"],
    })


@app.route("/api/review/grade", methods=["POST"])
def review_grade_endpoint():
    """Grade one item. Every tier writes into the same FSRS state.

    The modality is only how the item was tested; an objective true/false and a
    self-rated recall both end as a 1-4 rating, which is what lets one schedule
    span the whole mix.
    """
    from services.core.fsrs_engine import FSRSEngine

    data = request.get_json(force=True, silent=True) or {}
    uid = data.get("uid")
    rating = data.get("rating")
    student_id = data.get("student_id") or DEFAULT_STUDENT_ID
    if not uid or rating is None:
        return jsonify({"error": "uid and rating are required"}), 400
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return jsonify({"error": "rating must be an integer 1-4"}), 400
    if not 1 <= rating <= 4:
        return jsonify({"error": "rating must be 1-4 (Again/Hard/Good/Easy)"}), 400

    try:
        # A policy knob, not a constant: 0.9 suits material you will be tested
        # on; 0.85 cuts the daily workload by roughly a third for slightly more
        # forgetting, which is the right trade over a multi-year programme.
        retention = _desired_retention()
    except Exception:
        retention = 0.9

    try:
        result = storage.flashcards.grade_card_fsrs(
            uid, rating, FSRSEngine(desired_retention=retention),
            student_id=student_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error("review grade failed for %s: %s", uid, e, exc_info=True)
        return jsonify({"error": "could not record that grade"}), 500

    # An item that keeps being forgotten is a teaching problem, not a
    # scheduling one: say so, so the caller can offer the Socratic repair
    # instead of showing the same card again next week.
    # EVERY REVIEW IS LOGGED. Nothing else records that a review happened: the
    # card row keeps only its LAST review date, so history is overwritten on the
    # next grade and any question about "which days did I study" has no answer.
    # activity_log is the table the day streak already reads (and which has been
    # empty for exactly this reason), so one write serves both.
    try:
        storage.activity.log_activity(
            course_uid=data.get("course_uid") or "",
            activity_type="item_reviewed",
            concept_uid=data.get("concept_uid") or "",
            grade=rating,
            details={"uid": uid, "kind": data.get("kind") or ""},
            student_id=student_id,
        )
    except Exception as e:
        # A failed log must never lose the learner their grade — the schedule is
        # already written by this point.
        logger.warning("could not log the review for %s: %s", uid, e)

    from services.common.review_scheduler import LEECH_LAPSES
    lapses = int(result.get("lapses") or 0)
    result["leech"] = lapses >= LEECH_LAPSES
    result["status"] = "ok"

    # WHERE THE MISUNDERSTANDING ACTUALLY STARTS.
    #
    # An item failed four times is a teaching problem, and the concept the item
    # belongs to is not always the one to re-teach. The hydrator recorded what
    # each concept rests on; if something underneath is ALSO failing, sending
    # the learner back to the dependent re-teaches a symptom. This names the
    # deepest weak ancestor instead, when there is one.
    if result["leech"]:
        try:
            from datetime import date as _date
            from services.common.review_scheduler import (
                concept_strength, weakest_root)
            rows = storage.flashcards.get_items(student_id=student_id)
            today_iso = _date.today().isoformat()
            strength = concept_strength([_as_due(r, today_iso) for r in rows])
            concept_uid = data.get("concept_uid") or ""
            root = weakest_root(concept_uid, storage.courses.get_prereqs, strength)
            if root and root != concept_uid:
                titles = {}
                for r in rows:
                    if r.get("concept_uid") == root:
                        titles[root] = r.get("payload", {}).get("concept_title") or ""
                result["weak_prerequisite"] = {
                    "concept_uid": root,
                    "title": _concept_title(root) or titles.get(root) or "",
                    "course_uid": next((r.get("course_uid") for r in rows
                                        if r.get("concept_uid") == root), ""),
                }
        except Exception as e:
            # A missing suggestion is not a failed grade.
            logger.warning("weak-prerequisite lookup failed for %s: %s", uid, e)

    return jsonify(result)


@app.route("/api/review/activity", methods=["GET"])
def review_activity_endpoint():
    """Days the learner actually did something, for the activity heatmap.

    Counts every logged review and every completed concept. Days before review
    logging existed cannot be reconstructed — the card row only ever kept its
    most recent review date — so the response says how far back the record
    genuinely goes rather than drawing empty squares that look like idle days.
    """
    from datetime import date as _date, timedelta as _td

    student_id = request.args.get("student_id") or DEFAULT_STUDENT_ID
    try:
        days = max(30, min(400, int(request.args.get("days") or 365)))
    except (TypeError, ValueError):
        days = 365
    today = _date.today()
    start = today - _td(days=days - 1)

    counts = {}
    try:
        for row in storage.activity.get_activities(
                start_date=start.isoformat(), student_id=student_id) or []:
            day = (row.get("created_at") or "")[:10]
            if day:
                counts[day] = counts.get(day, 0) + 1
    except Exception as e:
        logger.error("activity heatmap: log read failed: %s", e, exc_info=True)
        return jsonify({"error": "could not read your activity"}), 503

    # Before review logging existed the only trace of a study day is the last
    # review date still sitting on each card. Fold those in so the map is not
    # blank on the day the feature ships, and mark where the record starts.
    recorded_from = None
    try:
        from_cards = {}
        for r in storage.flashcards.get_items(student_id=student_id):
            day = (r.get("last_review_date") or "")[:10]
            if day and day >= start.isoformat():
                from_cards[day] = from_cards.get(day, 0) + 1
        # The LARGER of the two, never the sum and never a replacement.
        #
        # They describe overlapping reviews, so adding them double counts. But
        # preferring the log wherever it has any row at all is worse: the day
        # this feature shipped had one logged review and eighteen cards last
        # reviewed that day, and deferring to the log turned an eighteen-review
        # day into a single faint square. A card contributes at most one to its
        # last-review day while the log records every repetition, so the log
        # overtakes the estimate as history accumulates and this converges on
        # the true count without ever overstating it.
        for day, n in from_cards.items():
            counts[day] = max(counts.get(day, 0), n)
    except Exception as e:
        logger.warning("activity heatmap: card dates unavailable: %s", e)
    if counts:
        recorded_from = min(counts)

    series = []
    for n in range(days):
        d = (start + _td(days=n)).isoformat()
        series.append({"date": d, "count": counts.get(d, 0)})

    active = [d for d, n in counts.items() if n]

    # ONE STREAK, COMPUTED WHERE THE MERGED RECORD LIVES.
    #
    # There were three. ActivityStore.get_streak() reads activity_log alone and
    # fed Home's "Day streak"; progress.js counted back over `days` in the
    # browser for "currently N days in a row"; and this endpoint is the only
    # place that merges the log with the cards' last_review_date, which is what
    # makes the record complete at all.
    #
    # They disagreed, and the log-only one was wrong: measured on this machine,
    # activity_log held two days (27th, 28th) while the merged record held
    # three (26th, 27th, 28th) and a truer count for the 27th — 17 against 10 —
    # because review logging did not exist for the earlier history. Home said 2
    # while Progress said 3, on the same day, for the same learner.
    #
    # Computed here, from the merged days, so both screens read one number.
    def _run_from(end_index):
        run = 0
        for i in range(end_index, -1, -1):
            if not series[i]["count"]:
                break
            run += 1
        return run

    last = len(series) - 1
    # Today not being done yet does not break a streak; the day is not over.
    streak = _run_from(last if series[last]["count"] else last - 1) \
        if last >= 0 else 0

    longest, run = 0, 0
    for entry in series:
        run = run + 1 if entry["count"] else 0
        longest = max(longest, run)

    return jsonify({
        "days": series,
        "total": sum(counts.values()),
        "active_days": len(active),
        "recorded_from": recorded_from,
        "today": today.isoformat(),
        "streak": streak,
        "longest_streak": longest,
    })


_RUBRIC_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "integer", "minimum": 1, "maximum": 4},
        "met": {"type": "array", "items": {"type": "string"}},
        "missed": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["grade", "note"],
}


@app.route("/api/review/check_answer", methods=["POST"])
def review_check_answer_endpoint():
    """Grade an open answer against the concept's own Mastery Criteria.

    THE ONLY MODEL CALL IN THE REVIEW LOOP, and it is opt-in per item. Every
    other tier is graded without one, because a daily queue cannot wait on this
    hardware. Open questions are the tier where self-marking is weakest — a
    learner who has just read the criteria is the worst judge of whether their
    own answer met them — so this is offered where it earns its cost, and the
    learner can still self-mark instead.

    The criteria are the AUTHOR'S, extracted with the item. The model is asked
    to check an answer against them, not to invent a standard of its own.
    """
    data = request.get_json(force=True, silent=True) or {}
    uid = (data.get("uid") or "").strip()
    answer = (data.get("answer") or "").strip()
    student_id = data.get("student_id") or DEFAULT_STUDENT_ID

    if not uid or not answer:
        return jsonify({"error": "uid and answer are required"}), 400
    if len(answer) > 4000:
        answer = answer[:4000]

    try:
        rows = storage.flashcards.get_items(student_id=student_id)
    except Exception as e:
        logger.error("check_answer: item lookup failed: %s", e, exc_info=True)
        return jsonify({"error": "could not read that item"}), 503
    item = next((r for r in rows if r.get("uid") == uid), None)
    if not item:
        return jsonify({"error": "no such item"}), 404

    rubric = (item.get("payload") or {}).get("rubric") or item.get("back") or ""
    if not rubric.strip():
        # No criteria means nothing to mark against; say so rather than letting
        # the model improvise a standard the author never set.
        return jsonify({"error": "this item has no criteria to mark against",
                        "gradable": False}), 422

    prompt = (
        "A learner answered a question about \"" + (item.get("front") or "")[:400] + "\".\n\n"
        "THE AUTHOR'S CRITERIA for a good answer:\n" + rubric[:2000] + "\n\n"
        "THE LEARNER'S ANSWER:\n" + answer + "\n\n"
        "Judge the answer against those criteria ONLY — not against what you "
        "would have written. Return JSON: grade (1 = did not recall it, "
        "2 = partial, 3 = met the criteria, 4 = met them easily and completely), "
        "met (criteria they satisfied), missed (criteria they did not), and note "
        "(one or two sentences addressed to the learner, saying what was missing "
        "rather than restating what they got right)."
    )
    try:
        from services.common.llm_utils import llm_generate_json
        verdict = llm_generate_json(
            prompt,
            sys_prompt=("You mark a learner's answer against criteria someone "
                        "else wrote. Be exact and brief. Do not award a grade "
                        "the criteria do not support."),
            expected_type="dict", schema=_RUBRIC_SCHEMA, max_tokens=400)
    except Exception as e:
        logger.warning("check_answer: model call failed for %s: %s", uid, e)
        verdict = None

    if not isinstance(verdict, dict) or "grade" not in verdict:
        # A failed check must not silently become a grade. The learner keeps
        # their own judgement, which is what they had before this existed.
        return jsonify({"error": "could not mark that answer just now",
                        "gradable": False}), 503

    grade = max(1, min(4, int(verdict.get("grade") or 1)))
    return jsonify({
        "grade": grade,
        "met": verdict.get("met") or [],
        "missed": verdict.get("missed") or [],
        "note": (verdict.get("note") or "").strip()[:600],
        "gradable": True,
    })


@app.route("/api/review/forecast", methods=["GET"])
def review_forecast_endpoint():
    """Due counts per day ahead — what load balancing is flattening."""
    from datetime import date as _date
    from services.common.review_scheduler import forecast, NEW_ITEMS_PER_DAY

    student_id = request.args.get("student_id") or DEFAULT_STUDENT_ID
    try:
        days = max(7, min(120, int(request.args.get("days") or 30)))
    except (TypeError, ValueError):
        days = 30
    today_iso = _date.today().isoformat()
    try:
        rows = storage.flashcards.get_items(student_id=student_id)
    except Exception as e:
        logger.error("forecast failed: %s", e, exc_info=True)
        return jsonify({"error": "could not read the item bank"}), 503
    dues = [_as_due(r, today_iso) for r in rows]
    return jsonify({
        "forecast": forecast(dues, days=days),
        # Reported apart from the curve: new material has no due date and is
        # introduced at a rate the queue controls, so folding it into "due"
        # would misdescribe both numbers.
        "not_started": sum(1 for d in dues if d.is_new),
        "new_per_day": NEW_ITEMS_PER_DAY,
    })


@app.route("/api/due_concepts", methods=["GET"])
def due_concepts_endpoint():
    """Every concept with a review scheduled. NOT what to do today.

    Combines two sources so early-stage progress is visible before flashcards
    exist: (1) flashcards with next_review_date <= today, (2) scheduled concept
    reviews written by the FSM per-answer. Deduped on concept_uid.

    NO LEARNER-FACING SURFACE MAY COUNT THIS AS "DUE".
    ---------------------------------------------------------------------
    This answers "how much is scheduled", which is a fine question for
    acceptance tooling (tools/course_acceptance.py) and a useless one for a
    person: it ignores interleaving, the daily cap and the new-item allowance.
    Home and the notification bell both read it once and showed 186 beside a
    Practice tab showing 13 for the same learner on the same day, and someone
    holding two numbers has no reason to believe either.

    Both now read /api/review/queue, which is what there is to DO today.
    A frontend guard test fails if any client script fetches this path again.
    """
    from datetime import date as _dt_date
    course_uid = request.args.get("course_uid")
    today_iso = _dt_date.today().isoformat()
    seen_uids = set()
    results = []
    # Which of the two sources failed to answer at all. An exception here is not
    # "nothing due" — see the guard at the bottom of this handler.
    failed_sources = []
    try:
        cards = storage.flashcards.get_due_cards(
            course_uid=course_uid if course_uid else None
        )
        for c in cards:
            d = dict(c) if not isinstance(c, dict) else c
            uid = d.get("concept_uid") or d.get("uid")
            if uid and uid not in seen_uids:
                seen_uids.add(uid)
                results.append(d)
    except Exception as e:
        logger.error(f"due_concepts: flashcards fetch failed: {e}", exc_info=True)
        failed_sources.append("flashcards")
    try:
        scheduled = storage.schedule.get_scheduled_reviews(
            end_date=today_iso,
            course_uid=course_uid if course_uid else None,
        )
        for r in scheduled:
            if r.get("status") == "completed":
                continue
            uid = r.get("unit_uid")
            if uid and uid not in seen_uids:
                seen_uids.add(uid)
                results.append({
                    "concept_uid": uid,
                    "course_uid": r.get("course_uid"),
                    "front": r.get("unit_title", ""),
                    "back": "",
                    "next_review_date": r.get("scheduled_date"),
                    "source": "scheduled_review",
                })
    except Exception as e:
        logger.error(f"due_concepts: scheduled reviews fetch failed: {e}", exc_info=True)
        failed_sources.append("scheduled_reviews")

    # A failure must never be served as an empty list.
    #
    # Both fetches above used to swallow their exception and fall through to
    # `{"concepts": []}` with HTTP 200, which the Practice tab renders as the
    # "You're all caught up" empty state. For a spaced-repetition tool that is
    # the one direction you cannot fail in: the learner is told there is nothing
    # to do, closes the tab, and reviews that were genuinely due are skipped —
    # cards lapse, the schedule degrades, and nothing ever surfaces the fact
    # that the answer was a lie. An empty list is only trustworthy when every
    # source actually answered.
    if failed_sources and not results:
        return jsonify({
            "error": "due_reviews_unavailable",
            "error_code": "DUE_REVIEWS_UNAVAILABLE",
            "failed_sources": failed_sources,
            "message": (
                "Could not load your due reviews. This does NOT mean you are "
                "caught up — please retry."
            ),
        }), 503

    payload = {"concepts": results}
    if failed_sources:
        # One source answered, so showing what we have beats erroring out — but
        # the list is known-incomplete and the client must be able to say so
        # rather than presenting it as the full picture.
        payload["degraded"] = True
        payload["degraded_sources"] = failed_sources
    return jsonify(payload)


# --- VG-03: Update Mastery API ---

@app.route("/api/update_mastery", methods=["POST"])
def update_mastery_endpoint():
    """Update mastery/progress for a concept after review."""
    data = request.get_json(force=True)
    uid = data.get("uid")
    grade = data.get("grade", 3)
    bloom_level = data.get("bloom_level", 1)
    if not uid:
        return jsonify({"error": "uid required"}), 400
    try:
        # Update progress in storage
        storage.progress.update_progress(uid, "", grade=grade, bloom_level=bloom_level, status="reviewed")
        return jsonify({"status": "updated"})
    except Exception as e:
        logger.error(f"Error updating mastery for {uid}: {e}")
        return jsonify({"error": str(e)}), 500


# --- VG-05: Course Tree API (Cytoscape visualization) ---

@app.route("/api/course_tree", methods=["GET"])
def course_tree_endpoint():
    """Build a hierarchical JSON tree from the course structure for Cytoscape visualization."""
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "uid required"}), 400
    try:
        course = storage.courses.get_course(uid)
        if not course:
            return jsonify({"error": "Course not found"}), 404
        # Build tree from course structure
        nodes = []
        edges = []
        nodes.append({"data": {"id": uid, "label": course.get("title", ""), "type": "course"}})
        for mod in course.get("modules", []):
            mod_uid = mod.get("uid", "")
            nodes.append({"data": {"id": mod_uid, "label": mod.get("title", ""), "type": "module", "parent": uid}})
            edges.append({"data": {"source": uid, "target": mod_uid}})
            for unit in mod.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        con_uid = concept.get("uid", "")
                        nodes.append({"data": {"id": con_uid, "label": concept.get("title", ""), "type": "concept", "parent": mod_uid}})
                        edges.append({"data": {"source": mod_uid, "target": con_uid}})
        return jsonify({"nodes": nodes, "edges": edges})
    except Exception as e:
        logger.error(f"Course tree error: {e}")
        return jsonify({"error": str(e)}), 500


# --- VG-07: Course Details API ---

@app.route("/api/course_details", methods=["GET"])
def course_details_endpoint():
    """Return detailed course metadata."""
    uid = request.args.get("uid")
    if not uid:
        return jsonify({"error": "uid required"}), 400
    try:
        course = storage.courses.get_course(uid)
        if not course:
            return jsonify({"error": "Course not found"}), 404
        return jsonify({
            "uid": uid,
            "title": course.get("title", ""),
            "overview": course.get("overview", ""),
            "status": course.get("status", ""),
            "teaching_style": course.get("teaching_style", ""),
            "creation_mode": course.get("creation_mode", "quick"),
            "design_brief": course.get("design_brief", ""),
            "created_at": course.get("created_at", ""),
        })
    except Exception as e:
        logger.error(f"Course details error: {e}")
        return jsonify({"error": str(e)}), 500


# --- Profile & Gamification API ---

def _get_profile_db():
    """Get a SQLite connection for profile/gamification tables."""
    import sqlite3
    db_path = os.path.join(DATA_ROOT, "helga.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Ensure gamification tables exist
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_profile (
            key TEXT PRIMARY KEY, value TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS gamification (
            key TEXT PRIMARY KEY, value TEXT,
            updated_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS achievements (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
            xp_reward INTEGER DEFAULT 50, unlocked INTEGER DEFAULT 0,
            unlocked_at INTEGER
        );
    """)
    # Insert defaults if empty
    for key, val in [
        ('display_name', ''), ('theme', 'light'), ('font_scale', '1.0'),
        ('default_voice', 'af_heart'), ('gamification_enabled', 'true'),
        ('daily_goal', '5'), ('avatar_url', ''), ('desired_retention', '0.9'),
    ]:
        conn.execute("INSERT OR IGNORE INTO user_profile (key, value) VALUES (?, ?)", (key, val))
    # Settings that were removed from the product. The rows were seeded on every
    # install, and GET /api/profile returns whatever the table holds — so an
    # orphan row keeps the API advertising a setting the UI no longer offers and
    # nothing reads. Dropping them here means existing installs converge on the
    # next start instead of needing a hand-run migration.
    for key in ('sound_effects',):
        conn.execute("DELETE FROM user_profile WHERE key = ?", (key,))
    for key, val in [
        ('total_xp', '0'), ('level', '1'), ('streak_days', '0'),
        ('streak_last_date', ''), ('daily_xp', '0'), ('daily_date', ''),
        ('achievements_unlocked', '[]'),
    ]:
        conn.execute("INSERT OR IGNORE INTO gamification (key, value) VALUES (?, ?)", (key, val))
    # Insert achievement definitions
    for aid, name, desc, xp in [
        ('first_course', 'First Steps', 'Create your first course', 50),
        ('first_answer', 'Curious Mind', 'Answer your first Socratic question', 25),
        ('perfect_concept', 'Ace', 'Get grade 4 on a concept', 75),
        ('streak_3', 'On a Roll', 'Maintain a 3-day streak', 100),
        ('streak_7', 'Week Warrior', 'Maintain a 7-day streak', 200),
        ('streak_30', 'Monthly Master', 'Maintain a 30-day streak', 500),
        ('concepts_10', 'Explorer', 'Complete 10 concepts', 150),
        ('concepts_50', 'Scholar', 'Complete 50 concepts', 300),
        ('bloom_3', 'Analyst', 'Reach Bloom level 3', 100),
        ('bloom_5', 'Evaluator', 'Reach Bloom level 5', 250),
        ('courses_3', 'Polymath', 'Create 3 courses', 200),
        ('review_10', 'Reviewer', 'Complete 10 spaced reviews', 100),
        ('daily_goal', 'Goal Getter', 'Meet your daily goal', 50),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO achievements (id, name, description, xp_reward) VALUES (?,?,?,?)",
            (aid, name, desc, xp)
        )
    conn.commit()
    return conn


def _gam_get(conn, key, default='0'):
    row = conn.execute("SELECT value FROM gamification WHERE key=?", (key,)).fetchone()
    return row['value'] if row else default


def _gam_set(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO gamification (key, value, updated_at) VALUES (?, ?, strftime('%s','now'))",
        (key, str(value))
    )


# XP level thresholds
_LEVEL_THRESHOLDS = [0, 100, 300, 600, 1000, 1500, 2200, 3000, 4000, 5500, 7500, 10000]


def _level_from_xp(xp):
    for i in range(len(_LEVEL_THRESHOLDS) - 1, -1, -1):
        if xp >= _LEVEL_THRESHOLDS[i]:
            return i + 1
    return 1


@app.route("/api/profile", methods=["GET"])
def get_profile():
    conn = _get_profile_db()
    try:
        rows = conn.execute("SELECT key, value FROM user_profile").fetchall()
        profile = {}
        for r in rows:
            val = r['value']
            if val in ('true', 'false'):
                val = val == 'true'
            elif val.replace('.', '', 1).isdigit():
                val = float(val) if '.' in val else int(val)
            profile[r['key']] = val
        return jsonify(profile)
    finally:
        conn.close()


@app.route("/api/profile", methods=["PATCH"])
def update_profile():
    data = request.get_json(force=True)
    conn = _get_profile_db()
    try:
        valid_keys = {'display_name', 'theme', 'font_scale', 'default_voice',
                      'gamification_enabled', 'daily_goal', 'avatar_url',
                      'desired_retention'}
        for key, value in data.items():
            if key not in valid_keys:
                continue
            if isinstance(value, bool):
                value = 'true' if value else 'false'
            conn.execute(
                "INSERT OR REPLACE INTO user_profile (key, value, updated_at) VALUES (?, ?, strftime('%s','now'))",
                (key, str(value))
            )
        conn.commit()
        return jsonify({"status": "updated"})
    finally:
        conn.close()


@app.route("/api/profile/reset", methods=["POST"])
def reset_profile():
    """Reset all learning progress but keep course content."""
    conn = _get_profile_db()
    try:
        conn.execute("DELETE FROM gamification")
        conn.execute("UPDATE achievements SET unlocked=0, unlocked_at=NULL")
        conn.commit()
        # Re-initialize defaults
        _get_profile_db().close()
        return jsonify({"status": "reset"})
    finally:
        conn.close()


@app.route("/api/gamification", methods=["GET"])
def get_gamification():
    """B22.1: per-student gamification (v7 tables; legacy K-V adopted on the
    legacy student's first read). student_id injected by the web-ui proxy."""
    sid = request.args.get("student_id")
    try:
        # B22.5: per-student toggle — a parent can turn gamification off for
        # a learner; the API then reports a muted payload the UI hides.
        student = storage.accounts.get_student(sid) if sid else None
        if student:
            import json as _json
            settings = _json.loads(student.get("settings") or "{}")
            if settings.get("gamification_enabled") is False:
                return jsonify({"enabled": False, "total_xp": 0, "level": 1,
                                "streak_days": 0, "daily_xp": 0,
                                "achievements_unlocked": []})
        row = storage.gamification.get(student_id=sid)
        daily_goal = 5
        try:
            conn = _get_profile_db()
            goal_row = conn.execute(
                "SELECT value FROM user_profile WHERE key='daily_goal'").fetchone()
            conn.close()
            if goal_row:
                daily_goal = int(goal_row["value"])
        except Exception:
            pass
        return jsonify({
            "total_xp": row["total_xp"],
            "level": row["level"],
            "streak_days": row["streak_days"],
            "daily_xp": row["daily_xp"],
            "daily_goal": daily_goal,
            "daily_goal_met": row["daily_xp"] >= daily_goal,
            "next_level_xp": row["next_level_xp"],
            "prev_level_xp": row["prev_level_xp"],
            "achievements_unlocked": [],
            "achievements_locked": [],
        })
    except Exception as e:
        # Zeros-with-200 told the learner they had 0 XP and a 0-day streak, which
        # for a streak mechanic reads as "you lost it" — a fabricated fact
        # indistinguishable from a real reset. Both callers (base.html's XP bar
        # and session.js) already treat a non-ok response as "hide the bar",
        # which is the honest outcome.
        logger.error(f"gamification read failed: {e}", exc_info=True)
        return jsonify({
            "error": "gamification_unavailable",
            "error_code": "GAMIFICATION_UNAVAILABLE",
            "detail": str(e),
        }), 503


@app.route("/api/gamification/award_xp", methods=["POST"])
def award_xp():
    """B22.1: award per-student XP after a graded interaction (ledgered).
    Body: {grade, bloom_level, action, first_try, ref_uid, student_id}"""
    data = request.get_json(force=True)
    try:
        result = storage.gamification.award_xp(
            action=data.get("action", "answer"),
            grade=int(data.get("grade", 3)),
            bloom_level=int(data.get("bloom_level", 1)),
            first_try=bool(data.get("first_try", False)),
            ref_uid=data.get("ref_uid"),
            student_id=data.get("student_id"))
        return jsonify(result)
    except Exception as e:
        logger.error(f"award_xp failed: {e}")
        return jsonify({"xp_earned": 0, "error": str(e)}), 500


@app.route("/api/gamification/cosmetics", methods=["GET", "POST"])
def cosmetics():
    """B22.4: interest-themed cosmetic unlocks. GET lists; POST equips."""
    if request.method == "GET":
        return jsonify(storage.gamification.cosmetics_for(
            student_id=request.args.get("student_id")))
    data = request.get_json(force=True)
    ok = storage.gamification.equip_cosmetic(
        data.get("cosmetic_id", ""), student_id=data.get("student_id"))
    if not ok:
        return jsonify({"error": "cosmetic locked or unknown"}), 409
    return jsonify({"status": "equipped", "cosmetic_id": data.get("cosmetic_id")})


@app.route("/api/admin/xapi/<student_id>", methods=["GET"])
@_admin_required
def admin_xapi(student_id):
    """B27.3: xAPI statement export for one learner (admin/analytics)."""
    from services.common.xapi import statements_for_student
    return jsonify({"statements": statements_for_student(
        storage, student_id, since=request.args.get("since"))})


@app.route("/api/gamification/check_streak", methods=["POST"])
def check_streak():
    """B22.1: check/update the per-student daily streak."""
    data = request.get_json(force=True) if request.data else {}
    try:
        return jsonify(storage.gamification.check_streak(
            student_id=(data or {}).get("student_id")))
    except Exception as e:
        logger.error(f"check_streak failed: {e}")
        return jsonify({"streak_days": 0, "incremented": False}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
