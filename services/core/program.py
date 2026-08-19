"""A Program: an ordered set of courses with prerequisite edges.

THE ABSTRACTION
---------------
A Course stays atomic. Everything larger is a Program, which collapses five
features into one:

    College Course, 1 semester   -> Program of 1
    College Course, 2 semesters  -> Program of 2  (Linear Algebra I -> II)
    Associate                    -> Program of ~20
    Bachelor's                   -> Program of ~40
    Seminar series               -> Program of 1-3

A two-semester sequence is NOT a stretched course. Stretching one course to twice
the length is exactly the spread-too-thin failure; Linear Algebra II is a
different course with its own syllabus and a prerequisite edge. Treating both as
Programs means the sequencing logic is written once.

PLAN CHEAP, BUILD LAZILY
------------------------
Measured on this hardware: 90 s per concept, ~144 concepts per semester course,
so ~3.6 h per course and ~135 h for a bachelor's. Generating a degree up front is
135 hours of compute for an artifact representing four years of study, most of
which the learner will never reach.

So a plan is titles, prerequisites and term placement — cheap, and what the
learner sees immediately. Courses materialise on enrolment, one ahead at most
(`build_scheduler.MAX_LOOKAHEAD`).

DEGREE SHAPE IS A TEMPLATE, NOT A SCRAPE
----------------------------------------
The obvious approach is to have the research service find real degree syllabi.
Measured: SearXNG's engine pool is exhausted after ~14 queries, and a planner
needs dozens — building this on the least reliable source would make the whole
feature as fragile as its weakest input.

Degree *shape* is stable public knowledge, so it lives in a template and research
fills the slots. The pay-off is that **the same code path serves "Associate in
Nursing" and "Associate in Dungeons & Dragons"**: one has real syllabi to match
and one does not, but neither changes the algorithm. No custom-degree branch.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Verified against published sources (docs/AI_UNIVERSITY_DESIGN.md): an associate
# is 60 credits / ~20 courses / 2 years, a bachelor's 120 / ~40 / 4, at 4-6
# courses per term over two terms a year. The arithmetic and the stated duration
# agree independently, which is the check worth trusting.
TEMPLATES = {
    "course": {"label": "Single course", "courses": 1, "terms": 1,
               "slots": {"core": 1}},
    "sequence": {"label": "Two-semester sequence", "courses": 2, "terms": 2,
                 "slots": {"core": 2}},
    "associate": {"label": "Associate", "courses": 20, "terms": 4,
                  "slots": {"gen_ed": 7, "core": 9, "elective": 3, "capstone": 1}},
    "bachelors": {"label": "Bachelor's", "courses": 40, "terms": 8,
                  "slots": {"gen_ed": 12, "core": 16, "elective": 9, "capstone": 3}},
}


class ProgramError(ValueError):
    """A plan that cannot be taught — a cycle, or a prerequisite that never
    appears. Raised rather than logged: an incoherent programme is invisible
    until a learner reaches a course they cannot follow, months in."""


def sequence_titles(subject, n):
    """Course titles for an n-part sequence: 'Linear Algebra I', '... II'."""
    numerals = ["I", "II", "III", "IV", "V", "VI"]
    base = re.sub(r"\s+(i{1,3}|1|2|3)$", "", (subject or "").strip(),
                  flags=re.I).strip()
    if n == 1:
        return [base]
    return [f"{base} {numerals[i]}" for i in range(min(n, len(numerals)))]


def plan_sequence(subject, parts=2, preset="college"):
    """A Program of `parts` courses, each requiring the previous.

    This is the MVP shape. It exercises the planner, the DAG, lazy
    materialisation and the scheduler with ~2 courses of build instead of 40 —
    and it has real textbook equivalents (Linear Algebra I/II, Calculus I/II), so
    coverage is measurable from day one.
    """
    titles = sequence_titles(subject, parts)
    courses = []
    for i, title in enumerate(titles):
        courses.append({
            "title": title,
            "slot": "core",
            "term": i + 1,
            "preset": preset,
            "requires": [titles[i - 1]] if i else [],
            "built": False,
            "chosen": True,          # a sequence has no choices to make
        })
    validate(courses)
    return {"subject": subject, "template": "sequence", "terms": parts,
            "courses": courses}


def plan_from_template(subject, template, slot_subjects=None, preset="college"):
    """A Program shaped by a credit template, with research filling the slots.

    `slot_subjects` maps a slot name to candidate subject titles. Missing slots
    are filled with placeholders rather than failing: a sourceless subject (the
    D&D case) must produce the same shape as a well-documented one, or it needs
    its own code path.
    """
    tpl = TEMPLATES.get(template)
    if not tpl:
        raise ProgramError(f"unknown template {template!r}")
    slot_subjects = slot_subjects or {}

    courses, term = [], 1
    per_term = max(1, round(tpl["courses"] / max(1, tpl["terms"])))
    for slot, count in tpl["slots"].items():
        pool = list(slot_subjects.get(slot) or [])
        for i in range(count):
            title = pool[i] if i < len(pool) else f"{subject}: {slot} {i + 1}"
            courses.append({
                "title": title,
                "slot": slot,
                # A capstone must come last; everything else fills terms in
                # order. Placing a capstone mid-programme is the kind of error
                # nobody notices until a learner reaches it.
                "term": tpl["terms"] if slot == "capstone" else term,
                "preset": preset,
                "requires": [],
                "built": False,
                "chosen": slot != "elective",
            })
            if slot != "capstone" and len(courses) % per_term == 0:
                term = min(term + 1, tpl["terms"])
    validate(courses)
    return {"subject": subject, "template": template, "terms": tpl["terms"],
            "courses": courses}


def validate(courses):
    """P1 + P4: the prerequisite graph must be teachable.

    Three failures, all invisible until a learner hits them:
      * a cycle — A requires B requires A
      * a prerequisite that is not in the programme at all
      * a prerequisite scheduled in the same term or later
    """
    titles = [c["title"] for c in courses]
    seen = {}
    for t in titles:
        key = t.strip().lower()
        if key in seen:
            raise ProgramError(f"duplicate course {t!r} — the same subject "
                               f"filling two slots under one name")
        seen[key] = True

    index = {c["title"].strip().lower(): c for c in courses}
    for c in courses:
        for req in (c.get("requires") or []):
            r = index.get(req.strip().lower())
            if r is None:
                raise ProgramError(
                    f"{c['title']!r} requires {req!r}, which is not in this "
                    f"programme")
            if r["term"] >= c["term"]:
                raise ProgramError(
                    f"{c['title']!r} (term {c['term']}) requires {req!r} "
                    f"(term {r['term']}) — a prerequisite must be earlier")

    # Cycle detection over the whole graph, not just adjacent terms: term
    # ordering catches most cases, but a plan edited later can still close a loop.
    colour = {}

    def visit(title):
        state = colour.get(title)
        if state == "done":
            return
        if state == "open":
            raise ProgramError(f"prerequisite cycle through {title!r}")
        colour[title] = "open"
        node = index.get(title)
        for req in ((node or {}).get("requires") or []):
            visit(req.strip().lower())
        colour[title] = "done"

    for key in index:
        visit(key)
    return True


def next_course(program):
    """The next course to study: earliest term, not yet built or completed."""
    pending = [c for c in (program or {}).get("courses", [])
               if not c.get("completed")]
    if not pending:
        return None
    return sorted(pending, key=lambda c: (c["term"], c["title"]))[0]


def scheduler_state(program, progress=0.0, seconds_since_turn=None,
                    builds_in_flight=0, build_paused=False):
    """Translate a programme into the state `build_scheduler.decide` consumes.

    Kept here rather than in the scheduler so the scheduler stays a pure
    decision function over a plain dict, testable without a programme.
    """
    nxt = next_course(program)
    return {
        "progress": progress,
        "seconds_since_turn": seconds_since_turn,
        "next_course_chosen": bool(nxt and nxt.get("chosen")),
        "next_course_built": bool(nxt and nxt.get("built")),
        "builds_in_flight": builds_in_flight,
        "build_paused": build_paused,
    }


def propose_slot_subjects(subject, template, llm_json_fn, brief_fn=None):
    """Real course titles for each slot, or {} .

    THE GAP THIS FILLS
    ------------------
    `plan_from_template` accepted `slot_subjects` and nothing ever populated it,
    so an "Associate in Nursing" produced twenty courses named "Nursing: gen_ed
    1" through "Nursing: elective 3". The structure was right — 20 courses, 4
    terms, a validated prerequisite graph — wrapped around twenty empty names.

    A degree is the one place where naming the courses IS the design. Getting
    "Anatomy & Physiology I, Microbiology, Pharmacology" rather than "core 1,
    core 2, core 3" is the difference between a curriculum and a placeholder.

    THE DIVISION OF LABOUR, AGAIN
    -----------------------------
    The model PROPOSES the course list — which subjects make up a nursing
    associate is world knowledge, and there is no machine-readable registry of
    degree compositions to look it up in. Deterministic code DISPOSES: each
    proposed course is a subject in its own right, and the ordinary per-course
    machinery then decides whether it has evidence, how long it should be, and
    whether it is over-stretched.

    So the model never gets to say "this degree is well-sourced". It only says
    "these are the courses", and each course is then held to the same bar as any
    other course — which is exactly the guarantee asked for.
    """
    tpl = TEMPLATES.get(template)
    if not tpl:
        return {}
    wanted = {slot: n for slot, n in tpl["slots"].items() if n > 0}
    if not wanted:
        return {}

    lines = "\n".join(f"  {slot}: {n} courses" for slot, n in wanted.items())
    try:
        data = llm_json_fn(
            prompt=(f"Name the actual courses in a real {tpl['label']} programme "
                    f"in {subject}. Use the titles a real institution would "
                    f"print in its catalogue.\n\nSlots to fill:\n{lines}\n\n"
                    f"gen_ed is general education outside the major; core is the "
                    f"major itself in teaching order; elective is optional "
                    f"depth; capstone is the final project or practicum."),
            sys_prompt="You know how degree programmes are composed. JSON only.",
            schema={"type": "object", "properties": {
                slot: {"type": "array", "items": {"type": "string"}}
                for slot in wanted}, "required": list(wanted)},
            max_tokens=900,
        )
    except Exception as e:
        logger.warning(f"slot proposal failed for {subject!r}: {e}")
        return {}

    # Shape drift: the schema asks for an object and a list wrapping it comes
    # back often enough that rejecting it has cost real builds three times.
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict)), None)
    if not isinstance(data, dict):
        return {}

    out = {}
    for slot, n in wanted.items():
        titles = [t.strip() for t in (data.get(slot) or [])
                  if isinstance(t, str) and t.strip()]
        # Deduplicate: the same subject filling two slots under one name is the
        # padding `validate()` rejects, and it is better caught here where a
        # retry is cheap.
        seen, uniq = set(), []
        for t in titles:
            k = t.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(t)
        if uniq:
            out[slot] = uniq[:n]
    return out
