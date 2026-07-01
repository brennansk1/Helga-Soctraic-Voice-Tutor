from flask import Flask, request, jsonify
import logging
import sys
import os
import time
import json
import requests
import re
import uuid
from services.common.storage import StorageManager

logger = logging.getLogger(__name__)

# Env Vars
WEB_UI_URL = os.getenv("WEB_UI_URL", "http://web-ui:5000")
LLM_API_URL = os.getenv(
    "LLM_API_URL", "http://host.docker.internal:11434/v1/chat/completions"
)
DATA_ROOT = os.getenv("DATA_ROOT", "/app/data")


def _update_status(message: str, log: str = None):
    try:
        payload = {"message": message}
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


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer  # heavy import, lazy
        name = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
        logger.info(f"Loading embedding model: {name}")
        _embed_model = SentenceTransformer(name)
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
                    "title": concept["title"],
                    "text": content[:500] if content else "",
                    "type": "Concept",
                }
            )
            if len(results) >= 10:
                break
    return results


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
    #   - If dense deps are absent the hybrid path silently degrades to FTS5.
    #   - Never raises — always falls back to substring search on any exception.
    try:
        if mode == "hybrid" and storage.search.is_dense_available():
            # Lazy-load the embedding model; if it fails, fall back to FTS5.
            try:
                embed_fn = get_embed_model().encode
            except Exception as embed_err:
                logger.warning(f"hybrid search: embed model unavailable ({embed_err}), using FTS5")
                embed_fn = None
            concept_rows = storage.search.hybrid_search(
                query,
                embed_fn=embed_fn,
                course_uid=course_uid,
                limit=10,
            )
        else:
            concept_rows = storage.search.search(query, course_uid=course_uid, limit=10)

        for row in concept_rows:
            content = row.get("content") or ""
            results.append(
                {
                    "uid": row["concept_uid"],
                    "title": row.get("title", ""),
                    "text": content[:500] if content else "",
                    "type": "Concept",
                }
            )
    except Exception as e:
        logger.warning(f"Search failed, falling back to substring: {e}")
        results = _substring_concept_search(query, course_uid)

    return jsonify({"results": results})


@app.route("/api/courses", methods=["GET", "DELETE"])
def courses():
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
                    "description": course.get("overview", ""),
                    "status": course.get("status", "unknown"),
                    "teaching_style": course.get("teaching_style", ""),
                    "progress": 0,
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
        return jsonify(
            {"courses": len(courses), "concepts": total_concepts, "streak": streak}
        )
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
        progress_by_concept = {
            p["concept_uid"]: p for p in storage.progress.get_course_progress(uid)
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
                        completed = (
                            progress["status"] == "completed" if progress else False
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
        return jsonify({"misconceptions": [], "analogies": []}), 200
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
        logger.error(f"teaching_context error: {e}")
        return jsonify({"misconceptions": [], "analogies": []}), 200


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

        course_dict["status"] = "ready"
        storage.courses.create_course(course_dict)

        logger.info(f"Custom course created: {course_uid} with {len(modules)} modules")
        return jsonify(
            {
                "status": "ok",
                "course_uid": course_uid,
                "message": f'Course "{title}" created with {len(modules)} modules',
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
    try:
        # Pass course_uid only if non-empty; otherwise fetch all due cards
        cards = storage.flashcards.get_due_cards(
            course_uid=course_uid if course_uid else None
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
        fsrs = FSRSEngine(desired_retention=0.9)
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
        # Also include streak from activity store
        try:
            streak = storage.activity.get_streak()
        except Exception:
            streak = 0
        stats["streak"] = streak
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

        question = llm_generate(user_prompt, sys_prompt=sys_prompt, max_tokens=200)
        if not question or len(question.strip()) < 10:
            question = f"Explain the key principles of {chosen.get('title', 'this concept')} and give an example."

        return jsonify({
            "question": question.strip(),
            "concept_uid": chosen["uid"],
            "concept_title": chosen.get("title", ""),
            "course_uid": chosen["_course_uid"],
            "course_title": chosen.get("_course_title", ""),
            "context_text": content[:3000],
        })

    except Exception as e:
        logger.error(f"Quiz generation error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


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

        result = llm_generate_json(user_prompt, sys_prompt=sys_prompt, max_tokens=400)

        if not result:
            result = {
                "grade": "FAIL",
                "score": 0,
                "feedback": "Could not grade the answer. Please try again.",
                "missing_concepts": [],
                "key_point": "",
            }

        # Anki integration: If FAIL or low score, create flashcards for weak areas
        grade = result.get("grade", "FAIL")
        score = int(result.get("score", 0))
        missing = result.get("missing_concepts", [])
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

                    # Downgrade existing cards' stability (they need more review)
                    if existing:
                        from services.core.fsrs_engine import FSRSEngine
                        fsrs = FSRSEngine()
                        for card in existing[:5]:
                            try:
                                storage.flashcards.grade_card_fsrs(card["uid"], 1, fsrs)
                            except Exception:
                                pass

            except Exception as e:
                logger.warning(f"Failed to create quiz-based flashcards: {e}")

        result["cards_created"] = cards_created
        if cards_created > 0:
            result["flashcard_note"] = f"{cards_created} flashcard(s) added for review"

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
                    except Exception:
                        pass
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
            course_dict["status"] = "ready"
            storage.courses.update_course(course_uid, course_dict)
        except Exception as hydration_err:
            logger.error(f"Content hydration failed: {hydration_err}", exc_info=True)
            # WIZ-4: Mark as "partial" not "failed" — allows user to retry hydration
            course_dict["status"] = "partial"
            storage.courses.update_course(course_uid, course_dict)
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
    """Retrieve the most recent palace anchor for a specific locus."""
    try:
        activities = storage.activity.get_activities(
            course_uid=course_uid,
            activity_type="palace_anchor",
        )
        # Find the latest anchor for this locus
        for act in reversed(activities):
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

@app.route("/api/due_concepts", methods=["GET"])
def due_concepts_endpoint():
    """Return concepts due for spaced repetition review.

    Combines two sources so early-stage progress is visible before flashcards
    exist: (1) flashcards with next_review_date <= today, (2) scheduled concept
    reviews written by the FSM per-answer. Deduped on concept_uid.
    """
    from datetime import date as _dt_date
    course_uid = request.args.get("course_uid")
    today_iso = _dt_date.today().isoformat()
    seen_uids = set()
    results = []
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
        logger.warning(f"due_concepts: flashcards fetch failed: {e}")
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
        logger.warning(f"due_concepts: scheduled reviews fetch failed: {e}")
    return jsonify({"concepts": results})


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
        ('sound_effects', 'true'), ('daily_goal', '5'), ('avatar_url', ''),
    ]:
        conn.execute("INSERT OR IGNORE INTO user_profile (key, value) VALUES (?, ?)", (key, val))
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
                      'gamification_enabled', 'sound_effects', 'daily_goal', 'avatar_url'}
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
    conn = _get_profile_db()
    try:
        total_xp = int(_gam_get(conn, 'total_xp', '0'))
        level = _level_from_xp(total_xp)
        streak_days = int(_gam_get(conn, 'streak_days', '0'))
        daily_xp = int(_gam_get(conn, 'daily_xp', '0'))
        daily_date = _gam_get(conn, 'daily_date', '')

        # Reset daily XP if it's a new day
        from datetime import date as dt_date
        today = dt_date.today().isoformat()
        if daily_date != today:
            daily_xp = 0

        # Get daily goal
        goal_row = conn.execute("SELECT value FROM user_profile WHERE key='daily_goal'").fetchone()
        daily_goal = int(goal_row['value']) if goal_row else 5

        # Get unlocked achievements
        achievements = conn.execute(
            "SELECT id, name, description, xp_reward, unlocked, unlocked_at FROM achievements"
        ).fetchall()
        unlocked = [dict(a) for a in achievements if a['unlocked']]
        locked = [dict(a) for a in achievements if not a['unlocked']]

        # Next level threshold
        next_threshold = _LEVEL_THRESHOLDS[level] if level < len(_LEVEL_THRESHOLDS) else _LEVEL_THRESHOLDS[-1] + 2000
        prev_threshold = _LEVEL_THRESHOLDS[level - 1] if level > 1 else 0

        return jsonify({
            "total_xp": total_xp,
            "level": level,
            "streak_days": streak_days,
            "daily_xp": daily_xp,
            "daily_goal": daily_goal,
            "daily_goal_met": daily_xp >= daily_goal,
            "next_level_xp": next_threshold,
            "prev_level_xp": prev_threshold,
            "achievements_unlocked": unlocked,
            "achievements_locked": locked,
        })
    finally:
        conn.close()


@app.route("/api/gamification/award_xp", methods=["POST"])
def award_xp():
    """Award XP after a graded interaction.
    Body: {grade: 1-4, bloom_level: 1-6, action: 'answer'|'complete_concept'|'complete_module'|'review'}
    """
    data = request.get_json(force=True)
    grade = data.get('grade', 3)
    bloom_level = data.get('bloom_level', 1)
    action = data.get('action', 'answer')
    first_try = data.get('first_try', False)

    # Base XP by action
    base_xp = {'answer': 10, 'complete_concept': 25, 'complete_module': 100, 'review': 15}.get(action, 10)

    # Only award XP for correct answers (grade >= 3)
    if action == 'answer' and grade < 3:
        return jsonify({"xp_earned": 0, "total_xp": 0, "level_up": False})

    # Multipliers
    multiplier = 1.0
    if first_try:
        multiplier *= 1.5
    if bloom_level >= 4:
        multiplier *= 2.0

    xp_earned = int(base_xp * multiplier)

    conn = _get_profile_db()
    try:
        from datetime import date as dt_date
        today = dt_date.today().isoformat()

        old_xp = int(_gam_get(conn, 'total_xp', '0'))
        new_xp = old_xp + xp_earned
        old_level = _level_from_xp(old_xp)
        new_level = _level_from_xp(new_xp)

        _gam_set(conn, 'total_xp', new_xp)
        _gam_set(conn, 'level', new_level)

        # Daily XP tracking
        daily_date = _gam_get(conn, 'daily_date', '')
        if daily_date != today:
            _gam_set(conn, 'daily_xp', xp_earned)
            _gam_set(conn, 'daily_date', today)
        else:
            old_daily = int(_gam_get(conn, 'daily_xp', '0'))
            _gam_set(conn, 'daily_xp', old_daily + xp_earned)

        conn.commit()

        return jsonify({
            "xp_earned": xp_earned,
            "total_xp": new_xp,
            "level": new_level,
            "level_up": new_level > old_level,
            "new_level": new_level if new_level > old_level else None,
        })
    finally:
        conn.close()


@app.route("/api/gamification/check_streak", methods=["POST"])
def check_streak():
    """Check and update streak. Call on each daily session start."""
    conn = _get_profile_db()
    try:
        from datetime import date as dt_date, timedelta
        today = dt_date.today().isoformat()
        yesterday = (dt_date.today() - timedelta(days=1)).isoformat()

        last_date = _gam_get(conn, 'streak_last_date', '')
        streak = int(_gam_get(conn, 'streak_days', '0'))

        if last_date == today:
            # Already checked today
            return jsonify({"streak_days": streak, "incremented": False})
        elif last_date == yesterday:
            streak += 1
        elif last_date == '':
            streak = 1
        else:
            streak = 1  # Reset — missed a day

        _gam_set(conn, 'streak_days', streak)
        _gam_set(conn, 'streak_last_date', today)

        # Check streak achievements
        newly_unlocked = []
        for threshold, ach_id in [(3, 'streak_3'), (7, 'streak_7'), (30, 'streak_30')]:
            if streak >= threshold:
                row = conn.execute("SELECT unlocked FROM achievements WHERE id=?", (ach_id,)).fetchone()
                if row and not row['unlocked']:
                    conn.execute(
                        "UPDATE achievements SET unlocked=1, unlocked_at=strftime('%s','now') WHERE id=?",
                        (ach_id,)
                    )
                    ach = conn.execute("SELECT * FROM achievements WHERE id=?", (ach_id,)).fetchone()
                    newly_unlocked.append(dict(ach))
                    # Award achievement XP
                    old_xp = int(_gam_get(conn, 'total_xp', '0'))
                    _gam_set(conn, 'total_xp', old_xp + ach['xp_reward'])

        conn.commit()

        return jsonify({
            "streak_days": streak,
            "incremented": True,
            "newly_unlocked": newly_unlocked,
        })
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
