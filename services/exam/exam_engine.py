"""Assessment engine (B18.1–B18.5, design spec 05).

Formal exams on top of the Socratic grader + standards layer: per-attempt item
generation (no fixed bank in v1), interest theming with a two-layer validity
guard, deterministic objective grading, Socratic free-response grading,
per-standard subscores, Utah cut scores (GFL 74%, Civics 35/50), and
checkpoint gating that feeds — never bypasses — the mastery engine.

Mounted as a Flask blueprint on the RAG process (shares StorageManager and
llm_utils); the FSM and web-ui are HTTP clients.
"""

import json
import logging
import random
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

ITEM_TYPES = ("mcq", "free", "numeric", "ordering")

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "item_type": {"type": "string", "enum": list(ITEM_TYPES)},
        "prompt": {"type": "string"},
        "choices": {"type": "array", "items": {"type": "string"}},
        "answer_index": {"type": "integer", "minimum": 0},
        "answer_value": {"type": "number"},
        "tolerance": {"type": "number"},
        "unit": {"type": "string"},
        "ordering": {"type": "array", "items": {"type": "string"}},
        "rubric": {"type": "string"},
        "exemplar": {"type": "string"},
        "standard_code": {"type": "string"},
        "bloom": {"type": "integer", "minimum": 1, "maximum": 6},
        "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
    },
    "required": ["item_type", "prompt", "standard_code", "bloom", "difficulty"],
}

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "same_standard": {"type": "boolean"},
        "same_bloom": {"type": "boolean"},
        "same_answer": {"type": "boolean"},
        "same_difficulty": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["same_standard", "same_bloom", "same_answer", "same_difficulty"],
}

# item_fraction for free-response grades (spec 05 §2.4)
FREE_GRADE_FRACTIONS = {1: 0.0, 2: 0.5, 3: 0.85, 4: 1.0}

# answer-key fields never sent to the browser (spec 05 §9.2)
_ANSWER_FIELDS = ("answer_index", "answer_value", "tolerance", "ordering",
                  "rubric", "exemplar")

_BLOOM_LABELS = {
    1: ("Remember", "recall of facts and definitions"),
    2: ("Understand", "explain the idea in one's own words"),
    3: ("Apply", "apply the concept to a new situation"),
    4: ("Analyze", "break down, compare, or relate parts"),
    5: ("Evaluate", "judge, critique, or defend a position"),
    6: ("Create", "design or synthesize something new"),
}


def _numeric_tokens(text):
    """Multiset of numeric tokens in a prompt — a re-skin that changed 3→4
    fails structurally without any LLM call."""
    return sorted(re.findall(r"-?\d+(?:\.\d+)?", text or ""))


def validate_item_shape(item):
    """Per-type required fields (spec 05 §2.2). Returns (ok, reason)."""
    t = item.get("item_type")
    if t not in ITEM_TYPES:
        return False, f"bad item_type {t!r}"
    if not item.get("prompt"):
        return False, "empty prompt"
    if t == "mcq":
        choices = item.get("choices") or []
        idx = item.get("answer_index")
        if not (3 <= len(choices) <= 5):
            return False, "mcq needs 3-5 choices"
        if not isinstance(idx, int) or not (0 <= idx < len(choices)):
            return False, "mcq answer_index out of range"
    elif t == "numeric":
        if not isinstance(item.get("answer_value"), (int, float)):
            return False, "numeric needs answer_value"
    elif t == "ordering":
        seq = item.get("ordering") or []
        if not (3 <= len(seq) <= 5):
            return False, "ordering needs 3-5 steps"
    elif t == "free":
        if not item.get("rubric"):
            return False, "free needs rubric"
    return True, "ok"


def structural_ok(base, themed):
    """Deterministic first layer of the validity guard (spec 05 §4.1)."""
    for field, label in (("standard_code", "standard"), ("bloom", "bloom"),
                         ("difficulty", "difficulty"), ("item_type", "type")):
        if themed.get(field) != base.get(field):
            return False, f"{label} drift"
    t = base.get("item_type")
    if t == "mcq":
        if len(themed.get("choices") or []) != len(base.get("choices") or []):
            return False, "choice count changed"
        if themed.get("answer_index") != base.get("answer_index"):
            return False, "answer index moved"
    elif t == "numeric":
        if themed.get("answer_value") != base.get("answer_value"):
            return False, "numeric answer changed"
        if themed.get("tolerance", 0) != base.get("tolerance", 0):
            return False, "tolerance changed"
    elif t == "ordering":
        if len(themed.get("ordering") or []) != len(base.get("ordering") or []):
            return False, "step count changed"
    if _numeric_tokens(themed.get("prompt")) != _numeric_tokens(base.get("prompt")):
        return False, "quantities changed"
    return True, "ok"


def strip_answer_key(item):
    """Answer-stripped view sent to the browser (spec 05 §9.2)."""
    return {k: v for k, v in item.items() if k not in _ANSWER_FIELDS}


def extract_answer_key(item):
    return {k: item[k] for k in _ANSWER_FIELDS if k in item}


def grade_objective(item, correct, response):
    """Deterministic, LLM-free grading (spec 05 §2.4). Returns (is_correct,
    fraction) or None for free items."""
    t = item.get("item_type")
    if t == "mcq":
        ok = response.get("response_index") == correct.get("answer_index")
    elif t == "numeric":
        try:
            val = float(response.get("response_value"))
        except (TypeError, ValueError):
            return 0, 0.0
        unit = correct.get("unit")
        if unit and response.get("unit") and response["unit"].strip().lower() != unit.strip().lower():
            return 0, 0.0
        ok = abs(val - float(correct.get("answer_value", 0))) <= float(correct.get("tolerance") or 0)
    elif t == "ordering":
        ok = list(response.get("response_order") or []) == list(correct.get("ordering") or [])
    else:
        return None
    return (1, 1.0) if ok else (0, 0.0)


def score_attempt(items):
    """Attempt score + per-standard subscores from graded item rows
    (spec 05 §6). `items` are ExamStore rows with fraction info resolved."""
    if not items:
        return 0.0, {}
    fractions = []
    by_code = {}
    for it in items:
        if it.get("grade") is not None and it["item_type"] == "free":
            frac = FREE_GRADE_FRACTIONS.get(int(it["grade"]), 0.0)
        else:
            frac = 1.0 if it.get("is_correct") else 0.0
        fractions.append(frac)
        code = it.get("standard_code") or "_untagged"
        by_code.setdefault(code, []).append(frac)
    score = sum(fractions) / len(fractions)
    subscores = {code: sum(v) / len(v) for code, v in by_code.items()}
    return score, subscores


def civics_display(score, n_items=50):
    return f"{round(score * n_items)} / {n_items}"


class ExamEngine:
    """Core logic, storage- and LLM-injected so it is unit-testable without a
    live model. `llm_json(system, user, schema)` returns a parsed dict or None."""

    GATING_KINDS = ("checkpoint", "unit")
    NON_GATING_KINDS = ("diagnostic", "standardized_prep", "summative")
    ATTEMPT_LIMITS = {"checkpoint": 3, "unit": 3, "summative": 1}   # per day

    def __init__(self, storage, llm_json):
        self.storage = storage
        self.llm_json = llm_json

    # ---------------------------------------------------------------- start

    def start_attempt(self, exam_id, student_id, course_uid=None):
        exam = self.storage.exams.get_exam(exam_id)
        if not exam:
            return None, ("exam not found", 404)
        existing = self.storage.exams.in_progress_attempt(exam_id, student_id=student_id)
        if existing:
            return self._attempt_view(existing, exam), None

        limit = self.ATTEMPT_LIMITS.get(exam["kind"])
        if limit:
            since = (datetime.utcnow() - timedelta(days=1)).isoformat()
            used = self.storage.exams.count_attempts_since(exam_id, since, student_id=student_id)
            if used >= limit:
                return None, (f"attempt limit reached ({limit}/day)", 429)

        accommodations = self._accommodation_snapshot(student_id)
        theme = self._pick_theme(student_id)
        aid = self.storage.exams.create_attempt(
            exam_id, course_uid=course_uid or exam.get("course_uid"),
            theme=theme, accommodations=accommodations, student_id=student_id)
        attempt = self.storage.exams.get_attempt(aid, student_id=student_id)
        return self._attempt_view(attempt, exam), None

    def _attempt_view(self, attempt, exam):
        n_items = sum(s.get("count", 1) for s in exam["blueprint"].get("slots", []))
        return {
            "attempt_id": attempt["id"], "status": attempt["status"],
            "n_items": n_items,
            "accommodations": attempt["accommodations"]
            if isinstance(attempt.get("accommodations"), dict)
            else json.loads(attempt.get("accommodations") or "{}"),
            "timer_seconds": None,   # untimed in v1; extended_time honored when timers land
            "theme": attempt.get("theme"),
        }

    def _accommodation_snapshot(self, student_id):
        """Snapshot IEP/504 flags at attempt start (spec 05 §7). The
        accommodations table lands in v8; until then read student settings."""
        try:
            student = self.storage.accounts.get_student(student_id)
            settings = json.loads(student.get("settings") or "{}") if student else {}
            return settings.get("accommodations", {})
        except Exception:
            return {}

    def _pick_theme(self, student_id):
        try:
            student = self.storage.accounts.get_student(student_id)
            interests = json.loads(student.get("interests") or "[]") if student else []
            return interests[0] if interests else None
        except Exception:
            return None

    # ----------------------------------------------------------------- next

    def next_item(self, attempt_id, student_id):
        attempt = self.storage.exams.get_attempt(attempt_id, student_id=student_id)
        if not attempt or attempt["status"] != "in_progress":
            return None, ("attempt not found or closed", 404)
        exam = self.storage.exams.get_exam(attempt["exam_id"])
        slots = self._expand_slots(exam["blueprint"])
        existing = self.storage.exams.items_for_attempt(attempt_id)
        idx = len(existing)
        if idx >= len(slots):
            return {"done": True}, None

        slot = slots[idx]
        student = self.storage.accounts.get_student(student_id) or {}
        grade_band = student.get("grade_band", "6-8")
        simplified = bool(self._accommodation_snapshot(student_id).get("simplified_language"))

        base = self._generate_item(slot, grade_band, simplified)
        item, validated = base, False
        theme = attempt.get("theme")
        if theme and base.get("item_type") != "ordering":
            themed = self._theme_item(base, theme)
            if themed is not None:
                ok, reason = self.validity_guard(base, themed)
                if ok:
                    item, validated = themed, True
                else:
                    logger.info(f"theme rejected ({reason}); serving base item")

        # shuffle mcq choices server-side, remapping the key (spec 05 §9.4)
        if item.get("item_type") == "mcq":
            item = self._shuffle_mcq(item)

        rid = self.storage.exams.add_item(attempt_id, strip_answer_key(item),
                                          extract_answer_key(item),
                                          theme_validated=validated)
        view = strip_answer_key(item)
        view["response_id"] = rid
        view["themed"] = validated
        view["theme_validated"] = validated
        return {"item_index": idx, "n_items": len(slots), "item": view}, None

    @staticmethod
    def _expand_slots(blueprint):
        slots = []
        for s in blueprint.get("slots", []):
            slots.extend([s] * max(1, int(s.get("count", 1))))
        if blueprint.get("shuffle"):
            rng = random.Random(blueprint.get("version", 1))
            rng.shuffle(slots)
        return slots

    def _generate_item(self, slot, grade_band, simplified=False):
        """Constrained-JSON generation with one retry, then a stock fallback
        (spec 05 §2.2-2.3). Never raises."""
        standard = self.storage.standards.get(slot["standard_code"]) or {}
        bloom = int(slot.get("bloom", 2))
        name, directive = _BLOOM_LABELS.get(bloom, _BLOOM_LABELS[2])
        simple = ("Use simplified, ELL-friendly language; keep the same standard, "
                  "Bloom, numbers, and answer. " if simplified else "")
        system = ("You are an expert K-12 assessment item writer for the Utah Core "
                  "Standards. Write exactly ONE assessment item. Output ONLY valid "
                  "JSON matching the schema — no prose.")
        user = (f"STANDARD: {slot['standard_code']} — \"{standard.get('text', '')}\"\n"
                f"STRAND: {standard.get('strand', '')}\n"
                f"BLOOM: Level {bloom} — {name}: {directive}\n"
                f"ITEM TYPE: {slot['item_type']}\nGRADE BAND: {grade_band}\n"
                f"{simple}"
                "RULES: test the standard at the given Bloom level; mcq: exactly one "
                "correct choice with 3-5 plausible distractors; numeric: give "
                "answer_value, tolerance, unit; ordering: 3-5 steps in `ordering`; "
                "free: write a `rubric` (the Grade-3 bar) and an `exemplar`. NEVER "
                "reveal the answer in the prompt. Set item_type, standard_code, "
                "bloom, and difficulty exactly as given.")
        for _ in range(2):
            item = self.llm_json(system, user, ITEM_SCHEMA)
            if item:
                item.setdefault("item_type", slot["item_type"])
                item["standard_code"] = slot["standard_code"]
                item["bloom"] = bloom
                item.setdefault("difficulty", "medium")
                ok, reason = validate_item_shape(item)
                if ok and item["item_type"] == slot["item_type"]:
                    return item
                logger.warning(f"generated item invalid ({reason}); retrying")
        logger.error(f"item generation failed for {slot}; serving stock item")
        return self._stock_item(slot, standard)

    @staticmethod
    def _stock_item(slot, standard):
        """Slot-level fallback so an attempt never dies on generation failure."""
        code = slot["standard_code"]
        t = slot["item_type"]
        base = {"standard_code": code, "bloom": int(slot.get("bloom", 2)),
                "difficulty": "medium", "item_type": "free",
                "prompt": (f"In your own words, explain the key idea of this "
                           f"standard: {standard.get('text', code)}"),
                "rubric": "Correct explanation of the standard's core idea.",
                "exemplar": standard.get("text", "")}
        # objective stock items are riskier than a free item — always fall back
        # to free-response, graded by the Socratic grader
        return base

    def _theme_item(self, base, interest):
        """Surface re-skin only; the answer key is re-attached out of band
        (spec 05 §3.1) so the themer physically cannot move it."""
        system = ("You are rewriting an assessment item to be about a student's "
                  "interest WITHOUT changing what it tests or its answer. You are "
                  "a surface re-skinner, not an item writer. Output ONLY JSON.")
        user = (f"STUDENT INTEREST: {interest}\n"
                f"ORIGINAL ITEM (JSON): {json.dumps(strip_answer_key(base))}\n"
                f"CHOICES (mcq, keep same count and same correct position): "
                f"{json.dumps(base.get('choices'))}\n"
                "Rewrite the scenario, names, objects, and setting to fit the "
                "interest. DO NOT change numbers, quantities, relationships, the "
                "correct answer, choice count, Bloom, difficulty, or the standard.")
        themed = self.llm_json(system, user, ITEM_SCHEMA)
        if not themed:
            return None
        # re-attach the authoritative answer key + invariants out of band
        for k in ("standard_code", "bloom", "difficulty", "item_type"):
            themed[k] = base.get(k)
        for k in ("answer_index", "answer_value", "tolerance", "unit",
                  "ordering", "rubric", "exemplar"):
            if k in base:
                themed[k] = base[k]
        if base.get("item_type") == "mcq" and len(themed.get("choices") or []) != len(base.get("choices") or []):
            themed["choices"] = base.get("choices")
        return themed

    def validity_guard(self, base, themed):
        """Structural + LLM verifier; both must pass (spec 05 §4)."""
        ok, reason = structural_ok(base, themed)
        if not ok:
            return False, reason
        verdict = self.llm_json(
            "Compare two assessment items. Answer ONLY the JSON. Judge equivalence.",
            (f"ORIGINAL: {json.dumps(base)}\nREWRITTEN: {json.dumps(themed)}\n"
             f"Does the rewritten item test the SAME standard "
             f"({base.get('standard_code')}) at the SAME Bloom level, with the "
             "SAME correct answer and SAME difficulty?"),
            VERIFY_SCHEMA)
        if not verdict:
            return False, "verifier unavailable"
        if all(verdict.get(k) for k in
               ("same_standard", "same_bloom", "same_answer", "same_difficulty")):
            return True, "ok"
        return False, verdict.get("reason", "verifier rejected")

    @staticmethod
    def _shuffle_mcq(item):
        choices = list(item.get("choices") or [])
        correct_text = choices[item["answer_index"]]
        random.shuffle(choices)
        item = dict(item)
        item["choices"] = choices
        item["answer_index"] = choices.index(correct_text)
        return item

    # ------------------------------------------------------------- response

    def record_response(self, attempt_id, response_id, payload, student_id):
        attempt = self.storage.exams.get_attempt(attempt_id, student_id=student_id)
        if not attempt or attempt["status"] != "in_progress":
            return None, ("attempt not found or closed", 404)
        row = self.storage.exams.get_item(response_id)
        if not row or row["attempt_id"] != attempt_id:
            return None, ("item not found", 404)

        item, correct = row["prompt"], row["correct"]
        update = {"response": payload}
        graded = grade_objective(item, correct, payload)
        if graded is not None:
            is_correct, _ = graded
            update["is_correct"] = is_correct   # stored now, revealed at grade time
        self.storage.exams.update_item(response_id, **update)

        items = self.storage.exams.items_for_attempt(attempt_id)
        answered = sum(1 for it in items if it.get("response"))
        # NO correctness leaked pre-grade (spec 05 §9.2)
        return {"recorded": True, "answered": answered, "n_items": len(items)}, None

    # --------------------------------------------------------------- submit

    def submit_attempt(self, attempt_id, student_id):
        attempt = self.storage.exams.get_attempt(attempt_id, student_id=student_id)
        if not attempt:
            return None, ("attempt not found", 404)
        exam = self.storage.exams.get_exam(attempt["exam_id"])
        if attempt["status"] == "graded":
            return self._grade_view(attempt, exam,
                                    self.storage.exams.items_for_attempt(attempt_id)), None

        items = self.storage.exams.items_for_attempt(attempt_id)
        # grade free items via the Socratic grader (spec 05 §2.4)
        for it in items:
            if it["item_type"] == "free" and it.get("response") and it.get("grade") is None:
                grade = self._grade_free(it, student_id)
                self.storage.exams.update_item(
                    it["id"], grade=grade, is_correct=1 if grade >= 3 else 0)
        items = self.storage.exams.items_for_attempt(attempt_id)

        score, subscores = score_attempt(items)
        passed = 1 if score >= exam["pass_threshold"] else 0
        self.storage.exams.update_attempt(
            attempt_id, student_id=student_id, status="graded", score=score,
            passed=passed, submitted_at=datetime.utcnow().isoformat())
        attempt = self.storage.exams.get_attempt(attempt_id, student_id=student_id)

        result = self._grade_view(attempt, exam, items)
        # gating side effects (spec 05 §5) — only gating kinds ever touch enrollment
        if exam["kind"] in self.GATING_KINDS:
            result["gating"] = self._apply_gating(exam, attempt, result, student_id)
        else:
            result["gating"] = {"kind": exam["kind"], "unlocked_next_unit": False,
                                "remediation_concepts": []}
        return result, None

    def _grade_free(self, item_row, student_id):
        """Reuse the Socratic grading prompt + schema; failure → grade 2
        (never a silent pass, mirroring B3.3)."""
        from services.common.prompts import get_socratic_grading_prompt, GRADE_JSON_SCHEMA
        item = item_row["prompt"]
        correct = item_row["correct"]
        response = item_row.get("response")
        answer_text = (json.loads(response) if isinstance(response, str) else response
                       ).get("response_text", "")
        student = self.storage.accounts.get_student(student_id) or {}
        messages = get_socratic_grading_prompt(
            concept=item.get("standard_code", ""),
            question=item.get("prompt", ""),
            user_answer=answer_text,
            context_text=correct.get("rubric", ""),
            bloom_level=item.get("bloom"),
            mastery_criteria=correct.get("rubric", ""),
            grade_band=student.get("grade_band"))
        system = messages[0]["content"]
        verdict = self.llm_json(system, "Grade the fenced answer now.", GRADE_JSON_SCHEMA)
        if not verdict or not isinstance(verdict.get("grade"), int):
            return 2
        return max(1, min(4, verdict["grade"]))

    def _grade_view(self, attempt, exam, items):
        score = attempt.get("score") or 0.0
        _, subscores = score_attempt(items)
        per_standard = {code: {"subscore": round(sub, 4),
                               "passed": sub >= exam["pass_threshold"]}
                        for code, sub in subscores.items()}
        missed = [c for c, v in per_standard.items() if not v["passed"]]
        display = {"raw": f"{sum(1 for i in items if i.get('is_correct'))}/{len(items)}",
                   "civics": None}
        if exam["kind"] == "standardized_prep" and abs(exam["pass_threshold"] - 0.70) < 1e-9:
            display["civics"] = civics_display(score)
        return {
            "attempt_id": attempt["id"], "status": attempt["status"],
            "score": round(score, 4), "passed": bool(attempt.get("passed")),
            "pass_threshold": exam["pass_threshold"],
            "display": display, "per_standard": per_standard,
            "missed_standards": missed, "theme": attempt.get("theme"),
        }

    # --------------------------------------------------------------- gating

    def _apply_gating(self, exam, attempt, result, student_id):
        """Spec 05 §5: pass → mark mastery + advance enrollment; fail →
        remediation queue from the missed standards' concepts."""
        course_uid = attempt.get("course_uid") or exam.get("course_uid")
        gating = {"kind": exam["kind"], "unlocked_next_unit": False,
                  "remediation_concepts": []}
        try:
            if result["passed"]:
                course = (self.storage.courses.get_course(course_uid)
                          or self.storage.catalog_courses.get_course(course_uid))
                unit_concepts = self._unit_concepts(course, exam.get("scope_uid"))
                for con in unit_concepts:
                    self.storage.progress.update_progress(
                        con["uid"], course_uid, student_id=student_id,
                        status="completed")
                nxt = self._next_unit_first_concept(course, exam.get("scope_uid"))
                if nxt:
                    self.storage.enrollments.enroll(student_id, course_uid)
                    self.storage.enrollments.update(
                        student_id, course_uid, current_concept_uid=nxt)
                    gating["unlocked_next_unit"] = True
            else:
                remediation = []
                for code in result["missed_standards"]:
                    for row in self.storage.standards.concepts_for_standard(code):
                        remediation.append(row["concept_uid"])
                        # reset mastery so the FSM re-teaches (spec 05 §5.2)
                        self.storage.progress.update_progress(
                            row["concept_uid"], course_uid, student_id=student_id,
                            status="in_progress")
                gating["remediation_concepts"] = sorted(set(remediation))
        except Exception as e:
            logger.error(f"gating side effects failed: {e}", exc_info=True)
        return gating

    @staticmethod
    def _iter_units(course):
        for mod in (course or {}).get("modules", []):
            for unit in mod.get("units", []):
                yield unit

    def _unit_concepts(self, course, unit_uid):
        for unit in self._iter_units(course):
            if unit.get("uid") == unit_uid:
                return [c for lesson in unit.get("lessons", [])
                        for c in lesson.get("concepts", [])]
        return []

    def _next_unit_first_concept(self, course, unit_uid):
        units = list(self._iter_units(course))
        for i, unit in enumerate(units):
            if unit.get("uid") == unit_uid and i + 1 < len(units):
                for lesson in units[i + 1].get("lessons", []):
                    for con in lesson.get("concepts", []):
                        return con.get("uid")
        return None


# ---------------------------------------------------------------- blueprint

def create_exam_blueprint(storage, llm_json=None):
    """Flask blueprint factory — mounted on the RAG process (spec 05 §0)."""
    from flask import Blueprint, jsonify, request

    if llm_json is None:
        from services.common.llm_utils import llm_generate_json

        def llm_json(system, user, schema):
            # The kwarg is `schema`, not `json_schema` — llm_generate_json uses
            # it BOTH to grammar-constrain generation and to validate the parse.
            # Misspelled, this raised TypeError on EVERY call, and nothing on
            # the exam path catches it: item generation, theming, the validity
            # guard and free-response grading all call self.llm_json bare, so
            # the exception escaped to Flask as a 500. It survived because the
            # tests inject their own llm_json and never construct this closure —
            # the default branch only runs in production.
            return llm_generate_json(user, sys_prompt=system, schema=schema)

    engine = ExamEngine(storage, llm_json)
    bp = Blueprint("exam", __name__)

    def _sid():
        body = request.get_json(silent=True) or {}
        return body.get("student_id") or request.args.get("student_id")

    @bp.route("/api/exam/list", methods=["GET"])
    def list_exams():
        exams = storage.exams.list_exams(
            course_uid=request.args.get("course_uid"),
            scope_uid=request.args.get("scope_uid"),
            kind=request.args.get("kind"))
        # blueprints stay server-side; expose metadata only
        return jsonify({"exams": [
            {k: e[k] for k in ("id", "course_uid", "scope_uid", "kind",
                               "standard_codes", "pass_threshold")}
            for e in exams]})

    @bp.route("/api/exam/attempt/start", methods=["POST"])
    def start():
        body = request.get_json(force=True)
        view, err = engine.start_attempt(body.get("exam_id"), _sid(),
                                         course_uid=body.get("course_uid"))
        if err:
            return jsonify({"error": err[0]}), err[1]
        return jsonify(view)

    @bp.route("/api/exam/attempt/<attempt_id>/next", methods=["GET"])
    def next_item(attempt_id):
        view, err = engine.next_item(attempt_id, _sid())
        if err:
            return jsonify({"error": err[0]}), err[1]
        return jsonify(view)

    @bp.route("/api/exam/attempt/<attempt_id>/response", methods=["POST"])
    def response(attempt_id):
        body = request.get_json(force=True)
        rid = body.pop("response_id", None)
        view, err = engine.record_response(attempt_id, rid, body, _sid())
        if err:
            return jsonify({"error": err[0]}), err[1]
        return jsonify(view)

    @bp.route("/api/exam/attempt/<attempt_id>/submit", methods=["POST"])
    def submit(attempt_id):
        view, err = engine.submit_attempt(attempt_id, _sid())
        if err:
            return jsonify({"error": err[0]}), err[1]
        return jsonify(view)

    @bp.route("/api/exam/attempt/<attempt_id>", methods=["GET"])
    def get_attempt(attempt_id):
        sid = _sid()
        attempt = storage.exams.get_attempt(attempt_id, student_id=sid)
        if not attempt:
            return jsonify({"error": "not found"}), 404
        exam = storage.exams.get_exam(attempt["exam_id"])
        items = storage.exams.items_for_attempt(attempt_id)
        view = engine._grade_view(attempt, exam, items)
        if attempt["status"] == "graded":
            view["items"] = [{
                "item": it["prompt"], "response": it.get("response"),
                "grade": it.get("grade"), "is_correct": it.get("is_correct"),
            } for it in items]
        return jsonify(view)

    return bp
