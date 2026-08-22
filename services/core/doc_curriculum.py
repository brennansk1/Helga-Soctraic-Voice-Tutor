"""Turn a documentation set into a CURRICULUM, not a reordered index.

THE FAILURE THIS EXISTS FOR
---------------------------
Building a dbt course from dbt's documentation produced, measured:

    1 module, 36 units, 40 lessons
    units: Introduction, Platform, Build, Local, Dbt Apis, Dbt Ai, Deploy,
           Dbt Versions, Explore, Fusion, Platform Integrations, Mesh, ...

Against `SCHOOL_SHAPE` (2-4 units per module, 2-5 lessons per unit) that is out
of band on both axes. Against a real dbt Fundamentals curriculum — modelling,
sources, testing, documentation, deployment — it is not the same kind of object
at all. It teaches the VENDOR'S PRODUCT SURFACE: `Fusion`, `Dbt Ai`, `Mesh` are
product lines, not learning units, and because coverage tracked page count the
121-page Platform section outweighed the 66-page Build section, so the course
over-taught platform administration and under-taught modelling — the actual
subject.

WHY THE BOOK PATH WAS THE WRONG MODEL
-------------------------------------
`build_from_docs` was written to preserve source structure, like the book path,
because a BOOK'S AUTHOR SEQUENCED IT: chapter 3 follows chapter 2 for a reason,
and re-deriving that would throw away the one thing a book gives you free.

Documentation has no author's sequence. It is organised for lookup. So the doc
path needed the RESEARCHED path's move instead — `curriculum_research` gathers
evidence and the builder's job is explicitly "SYNTHESIS: reconcile them, select
against the preset, and sequence for this learner", with a standing warning that
a copied table of contents "is somebody else's course".

Sequencing the pages (which `doc_reader.sequence` does) fixed the worst symptom:
it stopped the course teaching `defer` before installation. It could not turn a
reference structure into a pedagogical one, because that is a different
operation — selection and synthesis, not ordering.

WHAT THIS DOES
--------------
Gives the model the full page inventory as MATERIAL, plus the shape a course
has to fit, and asks for a curriculum designed around what a learner needs.
Every lesson must cite the pages it is built from, so:

  * coverage is real — a lesson with no pages behind it is rejected
  * nothing is invented — the model selects from the inventory, it does not
    recall a syllabus
  * pages that serve no learning objective are DROPPED, which is the whole
    point: 491 pages of vendor documentation is not 491 lessons.
"""
import json
import logging

logger = logging.getLogger(__name__)

#: From tools.structure_quality.SCHOOL_SHAPE — the bands a course is measured
#: against. Imported lazily so this module has no hard dependency on tools/.
DEFAULT_SHAPE = {
    "units_per_module": (2, 4),
    "lessons_per_unit": (2, 5),
    "concepts_per_lesson": (2, 4),
}


def _shape(subject=None):
    """The shape bands for this subject.

    A DOMAIN may override them. `SCHOOL_SHAPE` is a school timetable — its own
    comments measure modules in weeks and lessons in 50-minute sessions — and
    that is the wrong unit for a subject whose topic sizes are set by how much
    documentation exists. Computer science widens the ceiling and keeps the
    floor; a subject with no domain extension gets the school bands unchanged.
    """
    if subject:
        try:
            from services.domains.registry import for_subject
            ext = for_subject(subject)
            shape = getattr(ext, "SHAPE", None) if ext else None
            if shape:
                return dict(shape)
        except Exception:
            pass
    try:
        from tools.structure_quality import SCHOOL_SHAPE
        return dict(SCHOOL_SHAPE)
    except Exception:
        return dict(DEFAULT_SHAPE)


def target_size(n_pages, shape=None, subject=None):
    """A module/unit/lesson target for this much material.

    Sized so the course sits INSIDE the bands rather than at their edge: a
    course generated at the maximum of every band has no room to lose a lesson
    to a failed hydration without falling out of shape.
    """
    s = shape or _shape(subject)
    lo_u, hi_u = s["units_per_module"]
    lo_l, hi_l = s["lessons_per_unit"]
    lessons = max(6, min(n_pages, 60))

    # MODULE COUNT IS NOT DERIVED FROM THE BANDS.
    #
    # It was, and that was backwards: computing modules as
    # `units / midpoint(units_per_module)` meant WIDENING the ceiling produced
    # FEWER modules — the CS bands asked for 2 modules where the school bands
    # asked for 5. Wider bands are meant to let a big capability hold more
    # units, not to collapse the course into two.
    #
    # A course has a roughly fixed number of CAPABILITY AREAS regardless of how
    # much material sits under each: dbt Fundamentals has five (setup,
    # modelling, sources, testing, deployment), and so does almost every
    # professional course of this size. So modules scale with the SUBJECT, and
    # units per module absorb the variance in material.
    # Calibrated against a real course: dbt Fundamentals is 5 modules over
    # roughly this much material, and the model independently proposed 5 when
    # left to choose. ~9 lessons per module is what a professional course of
    # this size actually runs.
    modules = max(3, min(7, round(lessons / 9)))
    per_module = max(lo_u, min(hi_u, round(lessons / modules / 3)))
    per_unit = max(lo_l, min(hi_l, round(lessons / max(1, modules * per_module))))
    units = modules * per_module
    return {"modules": modules, "units": units, "lessons": lessons,
            "units_per_module": per_module, "lessons_per_unit": per_unit}


#: Roughly how many inventory lines fit alongside the prompt scaffolding in an
#: 8192-token window. Measured: 120 dbt pages produced a prompt that exceeded
#: it, `llm_generate_json` failed, and the caller fell back to the structural
#: path — which is how a 118-lesson course came out shaped like the
#: documentation's own index despite the synthesiser existing.
MAX_INVENTORY_LINES = 90


def inventory(pages, max_items=180):
    """The material, as the model will see it: one line per page, with an id.

    Ids rather than titles in the response, so a lesson's sources can be
    resolved exactly instead of by fuzzy title match — the model paraphrases
    titles, and a paraphrase cannot be looked up.
    """
    lines, index = [], {}
    for i, p in enumerate(pages[:max_items]):
        title = (p.get("title") or "").strip()
        if not title:
            continue
        section = (p.get("section") or "").strip()
        code = p.get("code_blocks") or 0
        index[i] = p
        lines.append(f"[{i}] {title}"
                     + (f"  (section: {section})" if section else "")
                     + (f"  [{code} code blocks]" if code else ""))
    return "\n".join(lines), index


PROMPT = """You are designing a professional course from a documentation set.

THE MATERIAL AVAILABLE — every page, with an id:
{inventory}

THE COURSE
Subject: {subject}
Level: {level}
Learner goal: {goal}

DESIGN IT LIKE A REAL COURSE, NOT LIKE THE DOCUMENTATION.
The documentation is organised by the vendor's product surface. A course is
organised by what a learner has to understand, in the order they can understand
it. A real course on this subject has modules like "modelling", "testing",
"deployment" — capabilities — not modules named after product lines.

RULES
- Produce EXACTLY {modules} modules, each with {units_per_module} units, each
  with about {lessons_per_unit} lessons.
- Every lesson MUST list the page ids it is built from, from the inventory
  above. A lesson with no pages cannot be taught and will be rejected.
- You do NOT have to use every page. Pages that serve no learning objective —
  release notes, product marketing, pricing, vendor-specific integrations a
  learner does not need — should be left out. Leaving material out is correct.
- Order modules so each depends only on earlier ones. Setup before practice.
- The material is listed in its SOURCE ORDER. When that source is a book, the
  author already sequenced it deliberately — follow that order unless a
  prerequisite genuinely demands otherwise, and group it into modules rather
  than reordering it.
- Name modules and lessons for what the learner will be able to DO, not for the
  documentation section they came from.
- A lesson title is 3-8 words. Module titles are 2-5 words.

Return STRICT JSON only:
{{"modules": [{{"title": "...", "units": [{{"title": "...",
  "lessons": [{{"title": "...", "pages": [0, 3, 7]}}]}}]}}],
  "dropped_reason": "one sentence on what you left out and why"}}"""


def propose(pages, subject, llm_json_fn, level="college", goal=None,
            shape=None, status_callback=None):
    """A synthesised curriculum over `pages`, or None.

    Returns {"modules": [...], "coverage": {...}, "target": {...}} where each
    lesson carries `pages` — the page dicts it is built from, resolved from
    ids. None when the model cannot be reached or returns nothing usable; the
    caller then falls back to the structural path rather than failing the build.
    """
    if not pages or not llm_json_fn:
        return None
    tgt = target_size(len(pages), shape, subject=subject)

    # TOO MUCH MATERIAL FOR ONE CALL — SAMPLE, DO NOT TRUNCATE.
    #
    # A doc set can run to hundreds of pages and the whole inventory will not
    # fit in one prompt. Taking the first N is the wrong reduction: sitemaps
    # are alphabetical, so the first 90 of 491 dbt pages are all "about-*" and
    # the model would design a curriculum for a subject it had only seen one
    # corner of.
    #
    # Sampling ROUND-ROBIN ACROSS SECTIONS keeps every part of the subject
    # visible, which is the same reasoning the crawler's interleaving uses. The
    # unsampled pages are not lost: they are attached to whichever lesson cites
    # their section, in `_backfill`.
    sampled = pages
    if len(pages) > MAX_INVENTORY_LINES:
        buckets = {}
        for p in pages:
            buckets.setdefault(p.get("section") or "", []).append(p)
        order = sorted(buckets, key=lambda k: (-len(buckets[k]), k))
        sampled = []
        while len(sampled) < MAX_INVENTORY_LINES and any(buckets[k] for k in order):
            for k in order:
                if buckets[k] and len(sampled) < MAX_INVENTORY_LINES:
                    sampled.append(buckets[k].pop(0))
        logger.info(f"[DOCS] inventory sampled {len(sampled)} of {len(pages)} "
                    f"pages across {len(buckets)} sections for synthesis")

    inv, index = inventory(sampled)
    if not inv:
        return None
    prompt = PROMPT.format(
        inventory=inv, subject=subject, level=level,
        goal=goal or f"become competent with {subject} in practice",
        modules=tgt["modules"], units_per_module=tgt["units_per_module"],
        lessons_per_unit=tgt["lessons_per_unit"])
    try:
        # OUTPUT BUDGET SIZED TO THE ANSWER, not to a round number.
        #
        # 2400 was too small and the symptom was misleading: the response was
        # truncated mid-object, `llm_generate_json` retried, and the SECOND
        # attempt happened to fit — so at 45 pages it looked like a success and
        # at 118 it looked like a context problem. It was neither. A curriculum
        # emits one JSON object per lesson plus a page-id array, so the budget
        # has to scale with the lesson count.
        budget = max(2400, min(6000, tgt["lessons"] * 90 + 600))
        raw = llm_json_fn(prompt=prompt, schema=CURRICULUM_SCHEMA,
                          expected_type="dict", max_tokens=budget)
    except Exception as e:
        # LOUD, not quiet. The caller falls back to the structural shape, which
        # is the right call — a worse course beats no course — but the previous
        # version failed silently and a build reported success while shipping
        # the documentation's own index as a curriculum. The only visible
        # symptom was `doc_curriculum: None`, in a field nobody thinks to read.
        logger.error(f"[DOCS] CURRICULUM SYNTHESIS FAILED — the course will "
                     f"use the raw documentation shape: {e}")
        if status_callback:
            try:
                status_callback(f"DOCS:SYNTHESIS_FAILED:{str(e)[:80]}")
            except Exception:
                pass
        return None
    return _resolve(raw, index, tgt, status_callback)


CURRICULUM_SCHEMA = {
    "type": "object",
    "properties": {
        "modules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "units": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "lessons": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "title": {"type": "string"},
                                            "pages": {
                                                "type": "array",
                                                "items": {"type": "integer"},
                                            },
                                        },
                                        "required": ["title", "pages"],
                                    },
                                },
                            },
                            "required": ["title", "lessons"],
                        },
                    },
                },
                "required": ["title", "units"],
            },
        },
        "dropped_reason": {"type": "string"},
    },
    "required": ["modules"],
}


def _resolve(raw, index, tgt, status_callback=None):
    """Turn the model's id references back into pages, dropping what is unusable."""
    if not isinstance(raw, dict):
        return None
    used, kept_modules, dropped_lessons = set(), [], 0
    for m in (raw.get("modules") or []):
        if not isinstance(m, dict) or not (m.get("title") or "").strip():
            continue
        kept_units = []
        for u in (m.get("units") or []):
            if not isinstance(u, dict):
                continue
            kept_lessons = []
            for l in (u.get("lessons") or []):
                if not isinstance(l, dict):
                    continue
                title = (l.get("title") or "").strip()
                ids = [i for i in (l.get("pages") or [])
                       if isinstance(i, int) and i in index]
                # A LESSON WITH NO SOURCE CANNOT BE TAUGHT. This is the check
                # that stops synthesis becoming invention: the model may design
                # any curriculum it likes, but every lesson has to be backed by
                # material that actually exists in the crawl.
                if not title or not ids:
                    dropped_lessons += 1
                    continue
                used.update(ids)
                kept_lessons.append({"title": title,
                                     "pages": [index[i] for i in ids]})
            if kept_lessons:
                kept_units.append({"title": (u.get("title") or "").strip()
                                   or "Unit", "lessons": kept_lessons})
        if kept_units:
            kept_modules.append({"title": m["title"].strip(),
                                 "units": kept_units})
    if not kept_modules:
        return None
    out = {
        "modules": kept_modules,
        "target": tgt,
        "coverage": {
            "pages_available": len(index),
            "pages_used": len(used),
            "pages_dropped": len(index) - len(used),
            "lessons_dropped_unsourced": dropped_lessons,
            "dropped_reason": (raw.get("dropped_reason") or "")[:300],
        },
    }
    if status_callback:
        try:
            status_callback(f"DOCS:CURRICULUM:{len(kept_modules)}:"
                            f"{sum(len(u['lessons']) for m in kept_modules for u in m['units'])}:"
                            f"{out['coverage']['pages_used']}")
        except Exception:
            pass
    return out


def shape_report(curriculum, shape=None, subject=None):
    """How well the proposal fits the bands — the loop's feedback signal."""
    s = shape or _shape(subject)
    mods = (curriculum or {}).get("modules") or []
    upm = [len(m.get("units") or []) for m in mods]
    lpu = [len(u.get("lessons") or []) for m in mods for u in (m.get("units") or [])]
    lo_u, hi_u = s["units_per_module"]
    lo_l, hi_l = s["lessons_per_unit"]

    def _in(vals, lo, hi):
        return round(sum(1 for v in vals if lo <= v <= hi) / max(1, len(vals)), 2)

    return {
        "modules": len(mods),
        "units": len(upm) and sum(upm),
        "lessons": sum(lpu),
        "units_per_module": upm,
        "units_per_module_in_band": _in(upm, lo_u, hi_u),
        "lessons_per_unit_in_band": _in(lpu, lo_l, hi_l),
        "ok": (_in(upm, lo_u, hi_u) >= 0.8 and _in(lpu, lo_l, hi_l) >= 0.8
               and len(mods) >= 2),
    }
