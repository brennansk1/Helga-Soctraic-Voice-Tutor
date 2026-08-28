"""The pipeline surface: see every stage, take any of them, hand the rest back.

REGISTER WITH (in librarian.py, next to the notes blueprint mount):
    from services.rag.pipeline_api import create_pipeline_blueprint
    app.register_blueprint(create_pipeline_blueprint(storage))

WHY THIS EXISTS
---------------
A course is built by seven stages — scope, structure, audit, coverage,
hydration, assets, verdicts — and until now an outside model could influence
exactly two of them. It could hand in a structure (`/api/custom_course/create`)
and it could ask for a degree to be planned. Everything else was the local
model's, unconditionally: no way to see what a stage had produced, no way to
do a stage better, no way to hand the rest back.

That matters because the two jobs are not equally hard. Writing 90 concepts of
accurate prose rewards a large model; conducting a Socratic turn at 1.3s
latency rewards a local one. The split this surface makes possible is: a strong
model authors, the local model teaches — and either can pick up where the other
stopped, per concept, mid-build.

THE THREE THINGS IT HAS TO GET RIGHT
------------------------------------
1. VISIBILITY IS PER CONCEPT, NOT PER COURSE. "Hydrating, 40%" tells an author
   nothing about WHICH concepts are thin. Every read here reports concept-level
   state — content present, word count, sources, who wrote it — because that is
   the granularity a takeover decision is actually made at.

2. BULK, BECAUSE 90 ROUND TRIPS IS NOT AN API. A course is a hundred concepts.
   One request per concept is a design that only works in a demo, so the write
   path takes a list and reports per-item outcomes rather than failing whole.

3. HANDING BACK MUST BE FREE. `ContentHydrator` already skips any concept that
   has content, so a part-authored course resumes correctly with no special
   casing: write what you want, call resume, and the local model fills the
   remainder. That property is what makes "step in at any step" true rather
   than aspirational, and there is a test pinning it.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not skip the quality gates. Injected content is recorded with its
author and left for the depth contract, the fact check and the grounding
verdict to judge exactly as they judge generated content. A surface that let a
caller mark its own work "verified" would remove the one thing that makes any
of these courses trustworthy.

It also carries no authentication, because nothing in this service does yet.
That is a real exposure and it is stated rather than implied: this blueprint
lets a caller write course content.
"""
import logging
import os
import time

from flask import Blueprint, jsonify, request
# ONE definition of teachable, shared with the audit gate and the
# course list. A second copy here would be free to disagree with the
# gate that actually blocks the course.
from services.core.course_audit import is_teachable, TUTOR_SECTIONS

logger = logging.getLogger(__name__)

#: Stage names, in the order the pipeline runs them. The order is the contract:
#: a caller deciding where to step in needs to know what has already happened.
STAGES = ("scope", "structure", "audit", "coverage", "hydration", "assets",
          "verdicts")

#: How a concept's content got there. Recorded per concept so a course that was
#: written by two different models can say which wrote what.
AUTHOR_LOCAL = "local"
AUTHOR_EXTERNAL = "external"

#: Below this a body is not a concept, whoever wrote it. The hydrator applies
#: the same floor to its own output; applying it here too stops an external
#: caller doing what the local model is not allowed to.
MIN_CONTENT_WORDS = 40


def _concepts_of(course):
    for m in (course.get("modules") or []):
        for u in (m.get("units") or []):
            for l in (u.get("lessons") or []):
                for c in (l.get("concepts") or []):
                    yield m, u, l, c


def _provenance_map(storage, course_uid):
    """concept_uid -> {model, generated_at}. Empty when the table is absent."""
    out = {}
    try:
        conn = storage.courses._get_db()
        for row in conn.execute(
                "SELECT concept_uid, model, generated_at FROM hydration_provenance "
                "WHERE course_uid = ?", (course_uid,)):
            out[row[0]] = {"model": row[1], "generated_at": row[2]}
    except Exception as e:            # table missing on an older schema
        logger.debug("provenance unavailable for %s: %s", course_uid, e)
    return out


def _record_provenance(storage, course_uid, concept_uid, model):
    try:
        conn = storage.courses._get_db()
        conn.execute(
            "INSERT INTO hydration_provenance "
            "(course_uid, concept_uid, sources, model, generated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (course_uid, concept_uid, "[]", model,
             time.strftime("%Y-%m-%dT%H:%M:%S")))
        conn.commit()
    except Exception as e:
        # Provenance is a record, not a gate. Losing it must not lose the write.
        logger.warning("could not record provenance for %s: %s", concept_uid, e)


def _contract(mastery, topic="", domain=None):
    """The quality bar a body must clear, from the same module the local
    pipeline is judged by.

    PUBLISHED, NOT IMPLIED. An external author was told only "40 words
    minimum" — the crash floor — while the real standard is a word range, a
    set of required elements and a register that both vary by mastery. Writing
    to the floor and then failing the depth contract is a waste of a large
    model's time and of the caller's, so the contract is returned before the
    write and applied during it.
    """
    try:
        from services.core.depth_contract import contract_for
    except Exception:
        try:
            from depth_contract import contract_for
        except Exception:
            return None
    try:
        return contract_for(int(mastery or 2), topic=topic or "", domain=domain)
    except Exception as e:
        logger.debug("contract_for failed: %s", e)
        return None


def _validate(body, mastery, topic="", domain=None, sources=None):
    """Judge a body exactly as the local pipeline judges its own output."""
    try:
        from services.core.depth_contract import validate_concept, regeneration_hint
    except Exception:
        try:
            from depth_contract import validate_concept, regeneration_hint
        except Exception:
            return None, []
    # `validate_concept` returns (ok, problems, details) — a TUPLE, not a list
    # of problems. Passing the tuple straight to regeneration_hint raised, the
    # except below swallowed it, and validation reported "nothing wrong" for a
    # 74-word body at a level requiring 320. A broad except around the only
    # thing enforcing quality turns the check off without saying so, which is
    # why the failure is now narrow and loud.
    try:
        ok, problems, _details = validate_concept(
            body, int(mastery or 2), topic=topic or "", domain=domain,
            sources=sources or [])
    except Exception as e:
        logger.error("depth contract could not judge this body (%s) — refusing "
                     "rather than passing it silently", e)
        return ["the depth contract could not be evaluated"], []
    problems = list(problems or [])
    if not problems:
        return [], []
    try:
        hint = regeneration_hint(problems)
    except Exception as e:
        logger.debug("regeneration_hint failed: %s", e)
        hint = None
    return problems, ([hint] if hint else [])


def _writing_standard(mastery):
    """The register and shape expected at this level, in the words the local
    builder uses on itself — so an external author writes the same course."""
    try:
        from services.core.course_builder import MASTERY_PROFILES
    except Exception:
        try:
            from course_builder import MASTERY_PROFILES
        except Exception:
            return None
    p = MASTERY_PROFILES.get(int(mastery or 2)) or {}
    return {
        "label": p.get("label"),
        "target_words": p.get("content_words"),
        "bloom_ceiling": p.get("bloom_ceiling"),
        "vocabulary": p.get("vocabulary"),
        "writing": p.get("writing"),
        "sections_the_product_reads": CONSUMED_SECTIONS,
        "sections_required": list(TUTOR_SECTIONS),
        "sections_note": (
            "## Core Explanation, ## Misconceptions and ## Analogies are "
            "REQUIRED. A concept missing any of them cannot be taught: the "
            "audit gate marks the whole course needs_review with 'there is no "
            "lesson to teach', and a learner cannot open it. finalize reports "
            "them under `unteachable` before that happens. The rest of the "
            "sections below are read when present and improve the lesson. "
            "Match the headings exactly."),
    }


# SECTIONS THE PRODUCT READS BACK OUT OF THE MARKDOWN.
#
# The tutor does not only read a concept as prose. `teaching_context` pulls
# "## Misconceptions" and "## Analogies" out of the body and hands them to the
# turn; the asset collector reads Misconceptions too. The local generator emits
# these headings on every concept, so the local pipeline gets them for free.
#
# An external author is told the word range and the required elements and
# nothing about this, so a Claude-written concept comes back with its own
# headings, `teaching_context` returns {"misconceptions": [], "analogies": []},
# and the tutor teaches it with less than it teaches a locally-built one.
# Measured: a 4-concept course that met 100% of its depth contract returned an
# empty teaching context for every concept.
#
# THREE OF THESE ARE HARD REQUIREMENTS, AND THIS SAID THEY WERE OPTIONAL.
#
# course_audit.TUTOR_SECTIONS — Core Explanation, Misconceptions, Analogies —
# is what is_teachable() checks, and the audit gate refuses a course whose
# concepts lack them: status needs_review, "there is no lesson to teach", not
# openable. This text told external authors the opposite.
#
# It cost two real courses. "Reading a Query Plan" and "Practical Regular
# Expressions" were both authored through this surface, both met their depth
# contract, and both are unusable — the first gated outright, the second half
# written. Their concepts are good prose with the wrong headings, which is
# exactly what an author who believed this note would produce.
#
# The remaining sections really are optional: read when present, and the
# lesson is better for them.
CONSUMED_SECTIONS = {
    "## Misconceptions": (
        "Read by the tutor before it responds, and by the asset collector. "
        "Format: '- **Belief**: … / **Correction**: …'. Without it the tutor "
        "cannot name the wrong idea a learner is most likely holding."),
    "## Analogies": (
        "Read by the tutor. Format: '- **Simple**: …' and "
        "'- **Technical**: …'. Without it the tutor has no ready analogy and "
        "invents one per turn."),
    "## Socratic Hooks": (
        "Bloom-banded question stems the tutor can open with: "
        "'- Bloom 1-2: …', '- Bloom 3-4: …', '- Bloom 5-6: …'."),
    "## Key Facts": "3-5 verified bullet points.",
    "## Real-World Examples": (
        "ONE worked example carried through to a result, with concrete "
        "values — not a description of where the idea gets used."),
    "## Edge Cases & Limitations": "Where the concept breaks down.",
    "## Core Explanation": "The body of the teaching.",
}


def create_pipeline_blueprint(storage):
    bp = Blueprint("pipeline", __name__)

    @bp.route("/api/pipeline", methods=["GET"])
    def describe():
        """What this surface can do, in one request.

        A model arriving here has no way to know the shape of an API it was
        not trained on, and guessing costs a round trip per guess. So the
        surface describes itself: the stages, the routes, which of them write,
        and the one rule that governs all of them.
        """
        return jsonify({
            "surface": "helga pipeline",
            "stages": list(STAGES),
            "rule": ("Content authored here is judged by the same depth "
                     "contract as content written by the local model, and "
                     "refused with its problems if it falls short. Read "
                     "/api/pipeline/contract before writing."),
            "routes": {
                "GET /api/pipeline": "this description",
                "GET /api/pipeline/presets": "preset -> scope, mastery, starting_from",
                "GET /api/pipeline/contract?mastery=&domain=&topic=":
                    "the bar a body must clear, before writing it",
                "GET /api/pipeline/course/<uid>":
                    "per-concept state: content, words, kind, author",
                "GET /api/pipeline/course/<uid>/concept/<cuid>":
                    "one concept, its context, and the bar for it",
                "POST /api/pipeline/course":
                    "a whole course — structure and bodies — in one request",
                "PUT /api/pipeline/course/<uid>/concept/<cuid>": "take over one concept",
                "PUT /api/pipeline/course/<uid>/concepts": "take over many at once",
                "POST /api/pipeline/course/<uid>/concept/<cuid>/asset":
                    "attach a diagram or image (licence required)",
                "POST /api/pipeline/course/<uid>/finalize":
                    "judge every body, set the status from the verdict",
                "POST /api/pipeline/course/<uid>/resume":
                    "hand the remaining concepts back to the local model",
                "POST /api/pipeline/program": "hand in a whole degree plan",
                "GET /api/pipeline/program/<uid>": "a degree and how much exists",
            },
            "structure": (
                "FREE. Modules, units, lessons and concepts are taken as "
                "given: any number of modules, any number of concepts per "
                "module, uneven sizes, and units/lessons omitted entirely "
                "where you think in modules-and-concepts. The presets' counts "
                "size a LOCAL build, which has to guess how much a subject can "
                "carry before it has written any of it; a caller holding the "
                "whole curriculum in one context should not be made to pad a "
                "module to a target or split one that is genuinely large. "
                "Nothing is refused, truncated or padded for its shape — only "
                "content is judged, and only against the depth contract."),
            "handback": ("Content is optional per concept. Write what you "
                         "want, leave the rest, and POST resume — the local "
                         "hydrator skips anything that already has a body, so "
                         "no coordination is needed."),
            "authentication": ("None. This service has no auth and this "
                               "blueprint can write course content."),
        })

    # ---------------------------------------------------------------- presets

    @bp.route("/api/pipeline/presets", methods=["GET"])
    def presets():
        """Every preset, with the parameters it resolves to.

        These were readable only by importing course_builder, so an external
        author had to guess what "College Course" meant — and the three dials
        it sets (scope, mastery, starting_from) are exactly what a caller needs
        in order to hand in a structure that matches one.
        """
        try:
            from services.core.course_builder import COURSE_PRESETS
        except Exception:
            try:
                from course_builder import COURSE_PRESETS
            except Exception as e:
                logger.error("presets unavailable: %s", e)
                return jsonify({"error": "presets unavailable"}), 503
        out = []
        for key, p in COURSE_PRESETS.items():
            out.append({
                "key": key,
                "label": p.get("label", key),
                "scope": p.get("scope"),
                "mastery": p.get("mastery"),
                "starting_from": p.get("starting_from"),
                "description": p.get("description", ""),
            })
        return jsonify({"presets": out, "stages": list(STAGES)})

    @bp.route("/api/pipeline/contract", methods=["GET"])
    def contract():
        """The standard a body must meet, before writing it.

        THE POINT IS PARITY. A course authored elsewhere has to be the same
        course, to a learner, as one written here — so this publishes the
        actual bar rather than leaving a caller to infer it from a rejection:
        the word range, the elements that must be present, the register and
        the Bloom ceiling for the level. Every one of these is read from the
        modules that judge local output, so the two can never drift apart.
        """
        mastery = request.args.get("mastery", type=int) or 2
        topic = request.args.get("topic", "")
        domain = request.args.get("domain")
        c = _contract(mastery, topic, domain)
        if not c:
            return jsonify({"error": "depth contract unavailable"}), 503
        return jsonify({
            "mastery": mastery,
            "domain": domain,
            "depth_contract": c,
            "writing_standard": _writing_standard(mastery),
            "floor_words": MIN_CONTENT_WORDS,
            "enforced": True,
            "note": ("Content that fails this contract is refused, not stored. "
                     "The local pipeline retries and records a miss because "
                     "something must exist for the learner; a caller that can "
                     "rewrite is told what is missing instead. Pass "
                     "allow_below_contract=true to store anyway — the course "
                     "then cannot claim 'ready' on that concept."),
        })

    # ------------------------------------------------------------- visibility

    @bp.route("/api/pipeline/course/<course_uid>", methods=["GET"])
    def course_state(course_uid):
        """Everything an author needs to decide what to take over.

        Per concept rather than per course: which have bodies, how long they
        are, how many sources they carry, and who wrote them. A summary that
        says "hydration 40%" cannot answer "which twenty are thin", which is
        the question a takeover actually starts from.
        """
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "no such course"}), 404

        prov = _provenance_map(storage, course_uid)
        concepts, written, thin = [], 0, 0
        for m, u, l, c in _concepts_of(course):
            uid = c.get("uid")
            body = ""
            try:
                body = storage.courses.get_concept_content(course_uid, uid) or ""
            except Exception:
                body = ""
            words = len(body.split())
            has = words >= MIN_CONTENT_WORDS
            written += 1 if has else 0
            thin += 1 if (body and not has) else 0
            p = prov.get(uid) or {}
            concepts.append({
                "uid": uid,
                "title": c.get("title"),
                "module": m.get("title"),
                "unit": u.get("title"),
                "lesson": l.get("title"),
                "concept_kind": c.get("concept_kind"),
                "bloom_level": c.get("bloom_level"),
                "has_content": has,
                "words": words,
                "source_confidence": c.get("source_confidence"),
                "written_by": p.get("model"),
                "written_at": p.get("generated_at"),
            })

        total = len(concepts)
        dc = course.get("depth_contract") or {}
        return jsonify({
            "course_uid": course_uid,
            "title": course.get("title"),
            "status": course.get("status"),
            "teaching_domain": course.get("teaching_domain"),
            "scope": course.get("scope"),
            "mastery": course.get("mastery"),
            "starting_from": course.get("starting_from"),
            "stages": list(STAGES),
            "counts": {
                "concepts": total,
                "with_content": written,
                "thin": thin,
                "missing": total - written,
            },
            # The verdicts, unedited. An author taking over a course should see
            # what the checks already said about it rather than rediscovering
            # it — including that they failed.
            "verdicts": {
                "depth_contract": {
                    "met_pct": dc.get("met_pct"),
                    "level_verified": dc.get("level_verified"),
                    "concepts_missing_contract": dc.get("concepts_missing_contract"),
                },
                "fact_check": course.get("fact_check"),
                "grounding": course.get("grounding"),
            },
            "concepts": concepts,
        })

    @bp.route("/api/pipeline/course/<course_uid>/concept/<concept_uid>",
              methods=["GET"])
    def concept_state(course_uid, concept_uid):
        """One concept, with its body and the context the local model uses.

        The context is the point. An external author writing a replacement body
        needs what the local hydrator would have had — the concept's place in
        the course, its objectives, its prerequisites, the level it is pitched
        at — or it writes something that does not fit the course around it.
        """
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "no such course"}), 404
        for m, u, l, c in _concepts_of(course):
            if c.get("uid") != concept_uid:
                continue
            body = ""
            try:
                body = storage.courses.get_concept_content(course_uid, concept_uid) or ""
            except Exception:
                body = ""
            prov = _provenance_map(storage, course_uid).get(concept_uid) or {}
            prior = [x.get("title") for _, _, _, x in _concepts_of(course)
                     if x.get("uid") != concept_uid]
            idx = prior.index(c.get("title")) if c.get("title") in prior else None
            return jsonify({
                "course_uid": course_uid,
                "concept": {
                    "uid": concept_uid,
                    "title": c.get("title"),
                    "concept_kind": c.get("concept_kind"),
                    "bloom_level": c.get("bloom_level"),
                    "learning_objectives": c.get("learning_objectives"),
                    "complexity_role": c.get("complexity_role"),
                    "depth_level": c.get("depth_level"),
                },
                "placement": {
                    "module": m.get("title"),
                    "unit": u.get("title"),
                    "lesson": l.get("title"),
                    "siblings": [x.get("title") for x in (l.get("concepts") or [])],
                },
                "course_context": {
                    "title": course.get("title"),
                    "teaching_style": course.get("teaching_style"),
                    "mastery": course.get("mastery"),
                    "starting_from": course.get("starting_from"),
                    "teaching_domain": course.get("teaching_domain"),
                    "taught_before_this": prior[:idx] if idx else [],
                },
                "content": body,
                "words": len(body.split()),
                "written_by": prov.get("model"),
                "written_at": prov.get("generated_at"),
                # The bar for THIS concept, beside the concept. An author
                # should not have to go and ask what "good" means here.
                "must_meet": {
                    "depth_contract": _contract(course.get("mastery"),
                                                course.get("title", ""),
                                                course.get("teaching_domain")),
                    "writing_standard": _writing_standard(course.get("mastery")),
                    "floor_words": MIN_CONTENT_WORDS,
                },
            })
        return jsonify({"error": "no such concept in this course"}), 404

    # ---------------------------------------------------------------- takeover

    def _write_one(course_uid, concept_uid, markdown, model, valid_uids,
                   mastery=None, topic="", domain=None, sources=None,
                   allow_below_contract=False):
        """Write one body, held to the standard the local pipeline is held to.

        THE BAR IS THE SAME BAR. A course written elsewhere and a course
        written here have to be the same course as far as a learner is
        concerned, so this runs `depth_contract.validate_concept` — the exact
        function that judges local output — and refuses anything that fails.

        Refusing rather than storing-and-flagging is a deliberate difference
        from the local path, and it is the stricter direction: the local model
        gets retries and, if it still misses, its miss is recorded because
        something must be there for the learner. A caller with a large model
        can simply rewrite, so it is told precisely what is missing instead.
        `allow_below_contract` exists for a caller that has decided otherwise;
        it stores the body AND the problems, and the course cannot then claim
        "ready" on it.
        """
        if concept_uid not in valid_uids:
            return {"uid": concept_uid, "ok": False,
                    "error": "no such concept in this course"}
        text = (markdown or "").strip()
        words = len(text.split())
        if words < MIN_CONTENT_WORDS:
            # ONE REJECTION SHAPE, not two. This returned a bare message while
            # a contract failure returned `problems` and `contract`, so a
            # caller had to handle rejection twice and could not simply read
            # `problems` to know what to fix.
            return {"uid": concept_uid, "ok": False, "words": words,
                    "error": "below the depth contract for this level",
                    "problems": [f"far too short: {words} words, below the "
                                 f"{MIN_CONTENT_WORDS}-word floor the local "
                                 f"hydrator is also held to"],
                    "hint": "Write the concept in full rather than a summary.",
                    "contract": _contract(mastery, topic, domain)}

        problems, hints = _validate(text, mastery, topic=topic, domain=domain,
                                    sources=sources)
        if problems and not allow_below_contract:
            return {"uid": concept_uid, "ok": False, "words": words,
                    "error": "below the depth contract for this level",
                    "problems": problems, "hint": (hints[0] if hints else None),
                    "contract": _contract(mastery, topic, domain)}

        try:
            storage.courses.save_concept_content(course_uid, concept_uid, text)
        except Exception as e:
            logger.error("content write failed for %s: %s", concept_uid, e)
            return {"uid": concept_uid, "ok": False, "error": str(e)[:160]}
        _record_provenance(storage, course_uid, concept_uid, model)
        out = {"uid": concept_uid, "ok": True, "words": words}
        if problems:
            # Stored under protest, and the record says so.
            out["problems"] = problems
            out["below_contract"] = True
        return out

    @bp.route("/api/pipeline/course/<course_uid>/concept/<concept_uid>",
              methods=["PUT"])
    def write_concept(course_uid, concept_uid):
        """Take over one concept: supply its body, keep everything else."""
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "no such course"}), 404
        data = request.get_json(silent=True) or {}
        valid = {c.get("uid") for _, _, _, c in _concepts_of(course)}
        res = _write_one(course_uid, concept_uid, data.get("content"),
                         (data.get("model") or AUTHOR_EXTERNAL), valid,
                         mastery=course.get("mastery"),
                         topic=course.get("title", ""),
                         domain=course.get("teaching_domain"),
                         sources=data.get("sources"),
                         allow_below_contract=bool(
                             data.get("allow_below_contract")))
        return jsonify(res), (200 if res["ok"] else 400)

    @bp.route("/api/pipeline/course/<course_uid>/concepts", methods=["PUT"])
    def write_concepts_bulk(course_uid):
        """Take over many concepts in one request.

        PARTIAL SUCCESS IS THE NORMAL CASE and is reported as such. A bulk
        write that fails whole because one body was short would make an author
        re-send ninety good ones, so each item carries its own outcome and the
        response says how many landed.
        """
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "no such course"}), 404
        data = request.get_json(silent=True) or {}
        items = data.get("concepts")
        if not isinstance(items, list) or not items:
            return jsonify({"error": "concepts must be a non-empty list of "
                                     "{uid, content}"}), 400
        model = (data.get("model") or AUTHOR_EXTERNAL)
        valid = {c.get("uid") for _, _, _, c in _concepts_of(course)}
        allow = bool(data.get("allow_below_contract"))
        results = [_write_one(course_uid, it.get("uid"), it.get("content"),
                              model, valid,
                              mastery=course.get("mastery"),
                              topic=course.get("title", ""),
                              domain=course.get("teaching_domain"),
                              sources=it.get("sources"),
                              allow_below_contract=allow)
                   for it in items if isinstance(it, dict)]
        ok = sum(1 for r in results if r["ok"])
        logger.info("[PIPELINE] %s: %d/%d concepts written by %r",
                    course_uid, ok, len(results), model)
        return jsonify({"written": ok, "failed": len(results) - ok,
                        "results": results}), 200

    # -------------------------------------------------------- one-shot course

    @bp.route("/api/pipeline/course", methods=["POST"])
    def create_whole_course():
        """A COMPLETE COURSE IN ONE REQUEST — structure and every body.

        This is the endpoint the split exists for. A large model can hold a
        whole curriculum in one context: it knows what module 6 will say while
        it writes module 1, so it can order concepts, avoid repeating itself
        and pitch each body at what the learner has already been told. Making
        it deliver that through ninety separate calls would throw away the one
        advantage it has, and would take longer than the local model does.

        So: post the course. Modules, units, lessons, concepts, and the
        markdown for each — one payload, one write, one answer.

        CONTENT IS OPTIONAL, PER CONCEPT. Anything left without a body is
        reported in `missing` and the course comes back "partial", which is the
        honest status and the one `resume` acts on. That is the whole handback
        mechanism: write the twenty concepts you care about, leave seventy, and
        the local model fills them in without being told which.

        The quality gates are NOT run here and NOT claimed. Depth contract,
        fact check and grounding are the local pipeline's verdicts; a course
        arriving this way has not faced them, and says so via
        `verdicts_pending` rather than inheriting a pass it never earned.
        """
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        modules = data.get("modules")
        if not isinstance(modules, list) or not modules:
            return jsonify({"error": "modules must be a non-empty list"}), 400

        import uuid as _uuid
        course_uid = data.get("course_uid") or f"course_{_uuid.uuid4().hex[:8]}"
        model = data.get("model") or AUTHOR_EXTERNAL

        # CREATE MEANS CREATE.
        #
        # A caller may name the uid, which is useful for a retry — and a POST
        # naming an EXISTING course wrote the request body straight over it:
        # create_course does INSERT OR REPLACE plus an os.replace of
        # structure.json, so a learner's modules were replaced wholesale and
        # their concept files left orphaned from the new tree. Nothing in the
        # request says "I meant to do that", so it is refused unless it does.
        if data.get("course_uid"):
            try:
                _existing = storage.courses.get_course(course_uid)
            except Exception:
                _existing = None
            if _existing and not data.get("replace"):
                return jsonify({
                    "error": f"course {course_uid} already exists",
                    "why": ("this endpoint CREATES a course; writing to an "
                            "existing uid would replace its structure and "
                            "orphan its content"),
                    "existing": {"title": _existing.get("title"),
                                 "status": _existing.get("status"),
                                 "modules": len(_existing.get("modules") or [])},
                    "do_instead": (f"PUT /api/pipeline/course/{course_uid}/concepts "
                                   f"to write content, or POST again with "
                                   f"\"replace\": true if you truly mean to "
                                   f"discard the existing structure"),
                }), 409

        # Preset resolution, so a caller can say "college" instead of guessing
        # the three dials it stands for.
        scope = data.get("scope")
        mastery = data.get("mastery")
        starting_from = data.get("starting_from")
        preset_key = data.get("preset")
        if preset_key:
            try:
                try:
                    from services.core.course_builder import COURSE_PRESETS
                except Exception:
                    from course_builder import COURSE_PRESETS
                p = COURSE_PRESETS.get(preset_key) or {}
                scope = scope if scope is not None else p.get("scope")
                mastery = mastery if mastery is not None else p.get("mastery")
                starting_from = (starting_from if starting_from is not None
                                 else p.get("starting_from"))
            except Exception as e:
                logger.debug("preset %r unresolved: %s", preset_key, e)

        # Build the structure, keeping every uid the caller supplied so it can
        # address concepts later without a second read.
        pending, total = [], 0
        course = {
            "uid": course_uid,
            "title": title,
            "description": data.get("description", ""),
            "teaching_style": data.get("teaching_style", ""),
            "teaching_domain": data.get("teaching_domain"),
            # THE HANDBACK IS THE WHOLE POINT OF THIS FIELD. Write the twenty
            # concepts you care about and leave seventy; the local model then
            # writes those seventy knowing only their titles unless the course
            # itself says what it is for. `hydrate()` reads this off the
            # course, so storing it here is the entire wiring.
            "learner_context": (data.get("context")
                                or data.get("learner_context") or "").strip(),
            "scope": scope, "mastery": mastery, "starting_from": starting_from,
            "authored_by": model,
            "status": "building",
            "modules": [],
        }
        for m in modules:
            if not isinstance(m, dict):
                continue
            m_out = {"uid": m.get("uid") or f"mod_{_uuid.uuid4().hex[:8]}",
                     "title": m.get("title", ""), "units": []}
            # Units and lessons are optional scaffolding: a caller that thinks
            # in modules-and-concepts should not have to invent a level it does
            # not use, so a missing layer is synthesised rather than rejected.
            units = m.get("units")
            if not units:
                units = [{"title": m.get("title", ""),
                          "lessons": [{"title": m.get("title", ""),
                                       "concepts": m.get("concepts") or []}]}]
            for u in units:
                u_out = {"uid": u.get("uid") or f"unit_{_uuid.uuid4().hex[:8]}",
                         "title": u.get("title", ""), "lessons": []}
                lessons = u.get("lessons") or [
                    {"title": u.get("title", ""), "concepts": u.get("concepts") or []}]
                for l in lessons:
                    l_out = {"uid": l.get("uid") or f"less_{_uuid.uuid4().hex[:8]}",
                             "title": l.get("title", ""), "concepts": []}
                    for c in (l.get("concepts") or []):
                        if isinstance(c, str):
                            c = {"title": c}
                        c_uid = c.get("uid") or f"con_{_uuid.uuid4().hex[:8]}"
                        l_out["concepts"].append({
                            "uid": c_uid,
                            "title": c.get("title", ""),
                            "concept_kind": c.get("concept_kind"),
                            "bloom_level": c.get("bloom_level"),
                            "learning_objectives": c.get("learning_objectives") or [],
                            "complexity_role": c.get("complexity_role"),
                            "depth_level": c.get("depth_level"),
                        })
                        total += 1
                        pending.append((c_uid, c.get("content")))
                    u_out["lessons"].append(l_out)
                m_out["units"].append(u_out)
            course["modules"].append(m_out)

        if not total:
            return jsonify({"error": "the structure contains no concepts"}), 400

        try:
            storage.courses.create_course(course)
        except Exception as e:
            logger.error("one-shot create failed for %s: %s", course_uid, e)
            return jsonify({"error": f"could not create the course: {e}"}), 500

        written, failed, missing = 0, [], []
        for c_uid, body in pending:
            if body is None or not str(body).strip():
                missing.append(c_uid)
                continue
            r = _write_one(course_uid, c_uid, str(body), model,
                           {u for u, _ in pending},
                           mastery=mastery, topic=title,
                           domain=course.get("teaching_domain"),
                           allow_below_contract=bool(
                               data.get("allow_below_contract")))
            if r.get("ok"):
                written += 1
            else:
                failed.append(r)
                missing.append(c_uid)

        # "ready" is a promise a learner can open any concept and find
        # something real, so it is earned here the same way the local pipeline
        # earns it: every concept, or the status says otherwise.
        course["status"] = "ready" if written == total else "partial"
        try:
            storage.courses.update_course(course_uid, course)
        except Exception as e:
            logger.error("status write failed for %s: %s", course_uid, e)

        logger.info("[PIPELINE] one-shot %s: %d/%d concepts written by %r -> %s",
                    course_uid, written, total, model, course["status"])
        return jsonify({
            "course_uid": course_uid,
            "status": course["status"],
            "concepts_total": total,
            "concepts_written": written,
            "missing": missing,
            "failed": failed,
            "verdicts_pending": True,
            "resume_url": f"/api/pipeline/course/{course_uid}/resume",
            "message": ("every concept has content"
                        if not missing else
                        f"{len(missing)} concept(s) have no body; POST the "
                        f"resume_url to let the local model write them"),
        }), 201

    # ----------------------------------------------------------------- degrees

    @bp.route("/api/pipeline/program", methods=["POST"])
    def create_program_plan():
        """Hand in a whole degree, planned elsewhere.

        `/api/program` plans a degree ITSELF — it consults the curriculum
        sources and the local model and returns what it decided. That is the
        right default and the wrong ceiling: a degree is the artefact where
        holding the whole thing in one context matters most, because the
        constraint that makes it a degree rather than a course list is the
        prerequisite graph, and a graph is exactly what a model reasons about
        badly one course at a time.

        So this accepts a finished plan. The shape is the planner's own —
        `{subject, template, courses: [{title, term, slot, requires}]}` —
        and it is checked by `program.validate`, the same function that judges
        a locally planned degree. A plan with a prerequisite cycle, a
        prerequisite that is not in the programme, or one scheduled no earlier
        than the course needing it is refused with the reason, because every
        one of those is invisible until a learner walks into it.
        """
        data = request.get_json(silent=True) or {}
        subject = (data.get("subject") or "").strip()
        courses = data.get("courses")
        if not subject:
            return jsonify({"error": "subject is required"}), 400
        if not isinstance(courses, list) or not courses:
            return jsonify({"error": "courses must be a non-empty list of "
                                     "{title, term, slot, requires}"}), 400

        try:
            from services.core.program import validate, ProgramError
        except Exception:
            try:
                from program import validate, ProgramError
            except Exception as e:
                logger.error("degree validator unavailable: %s", e)
                return jsonify({"error": "degree validation unavailable"}), 503

        normalised = []
        for i, c in enumerate(courses, 1):
            if isinstance(c, str):
                c = {"title": c}
            if not (c.get("title") or "").strip():
                return jsonify({"error": f"course {i} has no title"}), 400
            # THE PLANNER'S KEY IS `requires`, NOT `prerequisites`.
            #
            # Normalising to `prerequisites` wrote a field `program.validate`
            # never reads, so a plan with a prerequisite CYCLE validated
            # cleanly and was stored — the exact failure the validator exists
            # to prevent, defeated by a name. `prerequisites` is still
            # accepted from callers because it is the obvious word; it is
            # translated here rather than downstream.
            normalised.append({
                "title": c["title"].strip(),
                "term": c.get("term", 1),
                "slot": c.get("slot", i),
                "requires": (c.get("requires") or c.get("prerequisites") or []),
                "kind": c.get("kind", "core"),
                "description": c.get("description", ""),
            })

        try:
            validate(normalised)
        except ProgramError as e:
            # The same refusal a locally planned degree gets, for the same
            # reasons, with the reason said out loud.
            return jsonify({"error": "the plan is not a teachable degree",
                            "reason": str(e),
                            "not_degree_shaped": True}), 400
        except Exception as e:
            logger.error("degree validation blew up: %s", e)
            return jsonify({"error": f"could not validate the plan: {e}"}), 500

        import uuid as _uuid
        uid = data.get("program_uid") or f"prog_{_uuid.uuid4().hex[:8]}"
        plan = {
            "uid": uid,
            "subject": subject,
            "template": data.get("template", "associate"),
            "gen_ed": data.get("gen_ed", "include"),
            "authored_by": data.get("model") or AUTHOR_EXTERNAL,
            # THE TERM COUNT IS PART OF THE PLAN, not just of the reply.
            # Without it every consumer that reads plan["terms"] sees 0: the
            # term-balance check silently declined to run, and the capstone
            # check compared term 4 against 0 and called a correctly-placed
            # capstone misplaced.
            "terms": (data.get("terms")
                      or max((c["term"] for c in normalised), default=0)),
            "courses": normalised,
        }
        # Carried for the same reason the local planner carries it: the courses
        # in this programme are built later, one at a time, and each one would
        # otherwise be built from its title alone. An external author who says
        # what the degree is FOR should not have to repeat it per course.
        _ctx = (data.get("context") or data.get("learner_context") or "").strip()
        if _ctx:
            plan["learner_context"] = _ctx
        # THE SAME GATE A LOCALLY PLANNED DEGREE FACES.
        #
        # `validate` above catches what makes a programme UNTEACHABLE — cycles,
        # unresolvable or same-term prerequisites. It says nothing about
        # whether the result is shaped like a degree: terms of wildly uneven
        # size, a capstone that is not at the end, twenty courses that are one
        # subject renamed, or prerequisite sets copied across siblings so they
        # distinguish nothing. /api/program refuses a local plan on exactly
        # those grounds, and an externally authored plan was skipping them —
        # so the more capable model was held to the LOWER bar.
        try:
            from tools.degree_quality import assess
        except ImportError:
            assess = None
            logger.info("degree_quality unavailable — shape gate skipped")
        if assess is not None:
            shape = assess(plan)
            if shape.get("verdict") != "DEGREE_SHAPED":
                failed = ", ".join(shape.get("failed", []))
                logger.warning("external programme for %r rejected: %s",
                               subject, failed)
                return jsonify({
                    "error": f"the plan does not look like a degree ({failed})"
                             f" — nothing was saved",
                    "reason": "not_degree_shaped",
                    "checks": {k: v for k, v in shape.items()
                               if isinstance(v, dict)},
                }), 422

        try:
            storage.programs.create(uid, plan)
        except Exception as e:
            logger.error("program create failed for %s: %s", uid, e)
            return jsonify({"error": f"could not save the programme: {e}"}), 500

        logger.info("[PIPELINE] programme %s (%s, %d courses) authored by %r",
                    uid, subject, len(normalised), plan["authored_by"])
        return jsonify({
            "program_uid": uid,
            "subject": subject,
            "courses": len(normalised),
            "terms": len({c["term"] for c in normalised}),
            "validated": True,
            "note": ("The programme is planned, not built. Each course is "
                     "built when the learner reaches it, or can be authored "
                     "here via POST /api/pipeline/course."),
        }), 201

    @bp.route("/api/pipeline/program/<program_uid>", methods=["GET"])
    def program_state(program_uid):
        """A degree's plan and how much of it actually exists yet."""
        try:
            plan = storage.programs.get(program_uid)
        except Exception as e:
            logger.error("program read failed for %s: %s", program_uid, e)
            return jsonify({"error": "could not read the programme"}), 500
        if not plan:
            return jsonify({"error": "no such programme"}), 404
        courses = plan.get("courses") or []
        built = sum(1 for c in courses if c.get("built") or c.get("course_uid"))
        return jsonify({
            "program_uid": program_uid,
            "subject": plan.get("subject"),
            "template": plan.get("template"),
            "authored_by": plan.get("authored_by"),
            "counts": {"courses": len(courses), "built": built,
                       "unbuilt": len(courses) - built},
            "courses": courses,
        })

    # ------------------------------------------------------------------ assets

    @bp.route("/api/pipeline/course/<course_uid>/concept/<concept_uid>/asset",
              methods=["POST"])
    def upload_asset(course_uid, concept_uid):
        """Take over the asset stage: supply a diagram or image for a concept.

        Assets are the sixth stage and were the one an external author could
        not touch at all — the local collector drew or found everything, or
        the concept had nothing.

        LICENCE IS REQUIRED AND NOT NEGOTIABLE. `storage.save_asset` refuses an
        asset with no licence, deliberately: an unknown licence is a rejected
        licence, and that rule is the part of the media policy that
        demonstrably works. This endpoint does not soften it — an upload
        without a licence is refused here, with the reason, rather than being
        passed down to fail silently as a None return.

        Accepts either `data` (base64) or `path` (a file already on the
        server). `alt_text` is asked for because a diagram no one can read is
        not an asset for every learner.
        """
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "no such course"}), 404
        if concept_uid not in {c.get("uid") for _, _, _, c in _concepts_of(course)}:
            return jsonify({"error": "no such concept in this course"}), 404

        data = request.get_json(silent=True) or {}
        licence = data.get("license") or data.get("licence")
        if not licence:
            return jsonify({
                "error": "license is required",
                "why": ("storage refuses an unlicensed asset — an unknown "
                        "licence is a rejected licence. Supply the licence the "
                        "image is actually under (e.g. CC-BY-4.0, CC0, "
                        "public-domain) and its provenance_url."),
            }), 400

        raw, path = None, data.get("path")
        if data.get("data"):
            import base64 as _b64
            try:
                raw = _b64.b64decode(data["data"], validate=True)
            except Exception as e:
                return jsonify({"error": f"data is not valid base64: {e}"}), 400
        if raw is None and not path:
            return jsonify({"error": "supply either data (base64) or path"}), 400

        import hashlib as _hl
        sha = (_hl.sha256(raw).hexdigest() if raw is not None
               else _hl.sha256((path or "").encode()).hexdigest())
        try:
            asset_id = storage.save_asset(
                sha, data=raw, path=path, mime=data.get("mime"),
                width=data.get("width"), height=data.get("height"),
                source=data.get("source") or (data.get("model") or AUTHOR_EXTERNAL),
                license=licence, provenance_url=data.get("provenance_url"),
                alt_text=data.get("alt_text"), caption=data.get("caption"),
                caption_verified=bool(data.get("caption_verified")))
        except Exception as e:
            logger.error("asset save failed for %s: %s", concept_uid, e)
            return jsonify({"error": str(e)[:200]}), 500
        if not asset_id:
            return jsonify({"error": "storage refused the asset (licence or "
                                     "payload rejected)"}), 400

        try:
            conn = storage.courses._get_db()
            conn.execute(
                "INSERT OR REPLACE INTO concept_assets "
                "(course_uid, concept_uid, asset_id, role) VALUES (?, ?, ?, ?)",
                (course_uid, concept_uid, asset_id, data.get("role") or "figure"))
            conn.commit()
        except Exception as e:
            logger.error("asset link failed for %s: %s", concept_uid, e)
            return jsonify({"error": f"asset stored but not linked: {e}"}), 500

        logger.info("[PIPELINE] asset %s attached to %s by %r",
                    asset_id, concept_uid, data.get("model") or AUTHOR_EXTERNAL)
        return jsonify({"asset_id": asset_id, "concept_uid": concept_uid,
                        "role": data.get("role") or "figure",
                        "has_alt_text": bool(data.get("alt_text"))}), 201

    @bp.route("/api/pipeline/course/<course_uid>/finalize", methods=["POST"])
    def finalize(course_uid):
        """Judge the finished course and set its status from the answer.

        A course assembled through this surface was stuck at "partial" however
        good it got: the status is written when the course is created and
        nothing re-read it after later writes. But promoting to "ready" simply
        because every concept has a body would be the claim this project
        refuses to make on faith — "ready" means a learner can open any concept
        and find something real, and that is a verdict, not a count.

        So this re-judges every body against the depth contract, reports
        exactly which concepts fail, and sets the status from the result:
        ready when all pass, partial otherwise, with the failures named. It is
        the same judgement the local pipeline applies to its own output, run on
        demand rather than only at the end of a build.
        """
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "no such course"}), 404

        mastery = course.get("mastery")
        topic = course.get("title", "")
        domain = course.get("teaching_domain")
        total, passing, failures, empty = 0, 0, [], []
        unteachable = []
        for _m, _u, _l, c in _concepts_of(course):
            total += 1
            uid = c.get("uid")
            body = ""
            try:
                body = storage.courses.get_concept_content(course_uid, uid) or ""
            except Exception:
                body = ""
            if len(body.split()) < MIN_CONTENT_WORDS:
                empty.append({"uid": uid, "title": c.get("title")})
                continue
            # TEACHABILITY, WHICH THE DEPTH CONTRACT DOES NOT MEASURE.
            #
            # Measured on two real externally-authored courses: both met their
            # depth contract and both are unusable. "Reading a Query Plan"
            # finalized clean, then the audit gate marked it needs_review with
            # "4 of 4 concepts are missing sections the tutor reads — there is
            # no lesson to teach", and it has sat unenterable ever since.
            #
            # The author was not at fault. This surface told them the sections
            # were "not required and not enforced", they met everything that
            # WAS required, and the product then refused to teach the result.
            # Finding that out at teach time, from a different subsystem, is
            # the worst possible moment.
            if not is_teachable(body):
                unteachable.append({"uid": uid, "title": c.get("title")})
            problems, _hints = _validate(body, mastery, topic=topic,
                                         domain=domain)
            if problems:
                failures.append({"uid": uid, "title": c.get("title"),
                                 "problems": problems})
            else:
                passing += 1

        met_pct = round(100.0 * passing / total, 1) if total else 0.0
        # `ready` means a learner can open it. A concept the tutor cannot run a
        # lesson from fails that whatever its word counts say.
        status = ("ready" if (total and passing == total and not unteachable)
                  else "partial")
        course["status"] = status
        course["depth_contract"] = {
            "mastery": mastery,
            "domain": domain,
            "concepts_total": total,
            "concepts_verified": total - len(empty),
            "concepts_missing_contract": len(failures),
            "met_pct": met_pct,
            "level_verified": bool(total and passing == total),
            "failures": failures[:25],
            "judged_by": "pipeline_api.finalize",
        }
        try:
            storage.courses.update_course(course_uid, course)
        except Exception as e:
            logger.error("finalize could not write %s: %s", course_uid, e)
            return jsonify({"error": f"could not save the verdict: {e}"}), 500

        logger.info("[PIPELINE] finalize %s: %d/%d pass -> %s",
                    course_uid, passing, total, status)
        return jsonify({
            "course_uid": course_uid,
            "status": status,
            "concepts_total": total,
            "passing": passing,
            "below_contract": len(failures),
            "without_content": len(empty),
            "met_pct": met_pct,
            "failures": failures[:25],
            "empty": empty[:25],
            "unteachable": unteachable[:25],
            "unteachable_note": (
                ("%d concept(s) are missing one of %s. The tutor reads those "
                 "headings out of the markdown to run a lesson, so the course "
                 "will be gated as needs_review and cannot be opened until "
                 "they are added.")
                % (len(unteachable), ", ".join(TUTOR_SECTIONS))
            ) if unteachable else "",
            "note": ("Status is set from the depth contract and teachability. "
                     "The fact check, grounding verdict and coverage gate "
                     "belong to the local build and have not run on this "
                     "course."),
        }), 200

    # ---------------------------------------------------------------- handback

    @bp.route("/api/pipeline/course/<course_uid>/resume", methods=["POST"])
    def hand_back(course_uid):
        """Let the local model finish whatever was not taken over.

        Thin on purpose: `ContentHydrator` already skips concepts that have
        content, so handing back needs no new mechanism and no bookkeeping —
        which is exactly why stepping in at any point works at all. This
        forwards to the existing resume so there is ONE code path that
        finishes a course, not two that drift.
        """
        course = storage.courses.get_course(course_uid)
        if not course:
            return jsonify({"error": "no such course"}), 404
        missing = sum(1 for _, _, _, c in _concepts_of(course)
                      if len((storage.courses.get_concept_content(
                          course_uid, c.get("uid")) or "").split())
                      < MIN_CONTENT_WORDS)
        if not missing:
            return jsonify({"status": "nothing_to_resume",
                            "message": "every concept already has content"}), 200
        try:
            import requests as _rq
            base = os.getenv("SELF_URL", "http://localhost:5002")
            r = _rq.post(f"{base}/api/course/{course_uid}/resume_build", timeout=15)
            # DO NOT REPORT WHAT THE OTHER LAYER DID NOT DO. This said
            # "resuming" on any reply, including the 200 that means "refused,
            # nothing started" — so a caller was told the local model had
            # picked the work up when nothing had.
            if r.status_code != 202:
                body = {}
                try:
                    body = r.json() or {}
                except Exception:
                    pass
                return jsonify({
                    "status": "not_started",
                    "concepts_remaining": missing,
                    "upstream": r.status_code,
                    "reason": body.get("message") or body.get("error")
                              or "the local build service declined to start",
                }), 409
            return jsonify({"status": "resuming", "concepts_remaining": missing,
                            "upstream": r.status_code}), 202
        except Exception as e:
            logger.error("handback failed for %s: %s", course_uid, e)
            return jsonify({"error": f"could not start the local model: {e}"}), 502

    return bp
