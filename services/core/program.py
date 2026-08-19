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


def curated_degree(subject, template):
    """A transcribed real degree curriculum for this subject, or None.

    THE CASCADE THIS BELONGS TO
    ---------------------------
    The course tier prefers a real textbook's chapter order to an invented one,
    falling through four priorities before it invents anything. A degree needs
    the same cascade one level up, because **a published programme is to a degree
    what a textbook is to a course**:

        1. a published curriculum for this exact degree   <- this function
        2. research: a curriculum found for the subject
        3. the model proposes the course list
        4. the research loop fills whatever is still empty

    Jumping straight to (3) is what the degree tier did at first, and it is the
    same mistake as generating a course skeleton without looking for a textbook.
    """
    import glob
    import json
    import os

    key = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ",
                                     (subject or "").lower())).strip()
    if not key:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "../.."))
    for path in sorted(glob.glob(os.path.join(root, "tools", "references",
                                              "degrees", "*.json"))):
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception as e:
            logger.debug(f"degree reference {path} unreadable: {e}")
            continue
        if data.get("template") != template:
            continue
        aliases = [a.lower() for a in (data.get("aliases") or [])]
        aliases.append((data.get("subject") or "").lower())
        if key in aliases and data.get("slots"):
            return data
    return None


def source_degree_slots(subject, template, llm_json_fn=None, search_fn=None):
    """Fill a degree's slots, preferring published curricula over invention.

    Returns {"slots": {...}, "source": str, "authoritative": bool, "gaps": [...]}.

    `gaps` names slots a real curriculum did not cover, which is what the
    research loop is for — the same gap-driven pattern that took course coverage
    from 70% to 100%, applied to the programme.
    """
    tpl = TEMPLATES.get(template) or {}
    wanted = {k: v for k, v in (tpl.get("slots") or {}).items() if v > 0}

    # 1. A transcribed real curriculum.
    curated = curated_degree(subject, template)
    if curated:
        slots = {k: list(v)[:wanted.get(k, len(v))]
                 for k, v in (curated.get("slots") or {}).items() if v}
        gaps = [k for k, n in wanted.items() if len(slots.get(k) or []) < n]
        logger.info(f"[DEGREE] using the curated curriculum for {subject!r} "
                    f"({curated.get('source')})"
                    + (f"; gaps in {gaps}" if gaps else ""))

        # FILL THE GAPS, DO NOT DISCARD THE CURRICULUM.
        #
        # A transcribed programme may not cover every slot a template asks for —
        # published curricula differ in how many electives they name. Falling
        # back to a fully-invented list because of a partial gap would throw away
        # the authoritative part; filling only the gap keeps it. Same pattern as
        # the concept backfill, which took course coverage from 70% to 100%.
        if gaps and llm_json_fn:
            try:
                proposed = propose_slot_subjects(subject, template,
                                                 llm_json_fn) or {}
            except Exception as e:
                logger.warning(f"[DEGREE] gap fill failed: {e}")
                proposed = {}
            filled = []
            for slot in gaps:
                have = list(slots.get(slot) or [])
                seen = {t.lower() for t in have}
                for cand in (proposed.get(slot) or []):
                    if len(have) >= wanted[slot]:
                        break
                    if cand.lower() not in seen:
                        have.append(cand)
                        seen.add(cand.lower())
                if have:
                    slots[slot] = have
                    filled.append(slot)
            if filled:
                logger.info(f"[DEGREE] filled gap slot(s) {filled} from the model "
                            f"— the transcribed courses are unchanged")
            gaps = [k for k, n in wanted.items() if len(slots.get(k) or []) < n]

        return {"slots": slots, "source": "published curriculum",
                "authoritative": True, "gaps": gaps,
                "partially_proposed": bool(curated and gaps == [] and llm_json_fn
                                           and any(len(slots.get(k) or []) >
                                                   len(curated["slots"].get(k) or [])
                                                   for k in wanted)),
                "reference": curated.get("source")}

    # 2. Research — a curriculum published for this subject anywhere we look.
    if search_fn:
        try:
            found = search_fn(f"{subject} {tpl.get('label', template)} curriculum")
            if found:
                logger.info(f"[DEGREE] research returned {len(found)} candidate "
                            f"curriculum source(s) for {subject!r}")
        except Exception as e:
            logger.debug(f"[DEGREE] curriculum search failed: {e}")

    # 3. The model proposes. Labelled, because it did not meet a published
    #    standard and the record should say so.
    slots = {}
    if llm_json_fn:
        slots = propose_slot_subjects(subject, template, llm_json_fn) or {}
    gaps = [k for k, n in wanted.items() if len(slots.get(k) or []) < n]
    return {"slots": slots, "source": "model-proposed", "authoritative": False,
            "gaps": gaps,
            "note": ("no published curriculum found for this degree — the course "
                     "list is proposed rather than transcribed, and each course "
                     "is still evidence-gated individually")}


# Course codes and numerals are how real catalogues ENCODE prerequisites, and
# they are deterministic: "NUR 101" precedes "NUR 201" because a registrar said
# so, not because a model guessed. Reading them costs nothing and cannot drift.
_CODE = re.compile(r"^\s*([A-Z]{2,4})\s*[- ]?\s*(\d{3})\b")
_NUMERAL = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_TRAIL_NUM = re.compile(r"\b(i{1,3}|iv|vi?|[1-6])\s*$", re.I)


def _code_of(title):
    """(subject-prefix, level) from a catalogue code, e.g. NUR 201 -> (NUR, 2)."""
    m = _CODE.match(title or "")
    if not m:
        return None, None
    return m.group(1).upper(), int(m.group(2)) // 100


def _series_of(title):
    """(base-name, part) for a numbered series, e.g. 'Calculus II' -> (calculus, 2).

    Roman numerals and digits both, because catalogues use both and a learner
    should not meet part II before part I either way.
    """
    t = re.sub(r"^\s*[A-Z]{2,4}\s*[- ]?\s*\d{3}\s*:?\s*", "", (title or "")).strip()
    m = _TRAIL_NUM.search(t)
    if not m:
        return None, None
    tok = m.group(1).lower()
    part = _NUMERAL.get(tok) or (int(tok) if tok.isdigit() else None)
    if not part:
        return None, None
    base = _TRAIL_NUM.sub("", t).strip(" :-").lower()
    return (base or None), part


def infer_prerequisites(courses, propose_fn=None):
    """Add prerequisite edges. Deterministic first, model second.

    Without this a degree has RIGHT NAMES AND NO ORDER: plan_from_template set
    `requires: []` for every course, so validate() passed trivially and
    "Medical-Surgical Nursing II" could sit in term 1 ahead of "Nursing
    Foundations I". A programme that teaches II before I is not a programme.

    Two sources, in the order this codebase always uses:

      1. **Catalogue conventions** — NUR 101 -> NUR 201 -> NUR 301, and
         "Calculus I" -> "Calculus II". These encode real prerequisites, decided
         by registrars, and reading them is arithmetic.
      2. **The model** — cross-subject dependencies conventions cannot express,
         such as Statistics before Econometrics. Proposed, then filtered: an
         edge is accepted only if both endpoints exist and it does not create a
         cycle, so a confident wrong answer cannot make the programme
         unteachable.
    """
    by_title = {c["title"]: c for c in courses}

    # 1a. Same subject prefix, ascending level: NUR 101 -> NUR 201.
    by_prefix = {}
    for c in courses:
        prefix, level = _code_of(c["title"])
        if prefix and level:
            by_prefix.setdefault(prefix, []).append((level, c))
    for prefix, items in by_prefix.items():
        items.sort(key=lambda x: x[0])
        for i, (level, c) in enumerate(items):
            earlier = [p for lv, p in items[:i] if lv < level]
            if earlier:
                # The immediately preceding level only. Requiring every earlier
                # course would make the graph dense and the term assignment
                # impossible for no pedagogical gain.
                prev_level = max(lv for lv, _ in items[:i] if lv < level)
                for lv, p in items[:i]:
                    if lv == prev_level and p["title"] not in c["requires"]:
                        c["requires"].append(p["title"])

    # 1b. Numbered series: Calculus I -> Calculus II.
    by_series = {}
    for c in courses:
        base, part = _series_of(c["title"])
        if base and part:
            by_series.setdefault(base, []).append((part, c))
    for base, items in by_series.items():
        items.sort(key=lambda x: x[0])
        for i, (part, c) in enumerate(items):
            for prev_part, p in items[:i]:
                if prev_part == part - 1 and p["title"] not in c["requires"]:
                    c["requires"].append(p["title"])

    # 2. Model-proposed cross-subject edges, each one filtered.
    if propose_fn:
        try:
            proposed = propose_fn([c["title"] for c in courses]) or []
        except Exception as e:
            logger.warning(f"prerequisite proposal failed: {e}")
            proposed = []
        for edge in proposed:
            try:
                course, needs = edge.get("course"), edge.get("requires")
            except AttributeError:
                continue
            c, p = by_title.get(course), by_title.get(needs)
            if not c or not p or c is p or needs in c["requires"]:
                continue
            c["requires"].append(needs)
            if _creates_cycle(courses):
                c["requires"].remove(needs)
                logger.info(f"rejected proposed edge {course!r} <- {needs!r}: "
                            f"it would create a cycle")
    return courses


def _creates_cycle(courses):
    index = {c["title"]: c for c in courses}
    colour = {}

    def visit(title):
        state = colour.get(title)
        if state == "done":
            return False
        if state == "open":
            return True
        colour[title] = "open"
        for req in (index.get(title, {}).get("requires") or []):
            if visit(req):
                return True
        colour[title] = "done"
        return False

    return any(visit(t) for t in index)


def assign_terms(courses, terms, per_term=None):
    """Place courses in terms so every prerequisite comes strictly earlier.

    Fill order is not good enough once edges exist: a course can be scheduled
    before something it needs simply because it appeared earlier in the slot
    list. This is a topological layering — a course sits one term after the
    latest thing it requires — with a per-term cap so a term does not balloon.
    """
    index = {c["title"]: c for c in courses}
    placed = {}

    def depth(title, seen=None):
        seen = seen or set()
        if title in placed:
            return placed[title]
        if title in seen:                 # defensive; validate() rejects cycles
            return 1
        seen.add(title)
        reqs = (index.get(title, {}).get("requires") or [])
        d = 1 if not reqs else 1 + max(depth(r, seen) for r in reqs if r in index)
        placed[title] = d
        return d

    # A CHAIN DEEPER THAN THE PROGRAMME CANNOT BE HONOURED.
    #
    # Clamping depth to the term count silently puts a course in the same term as
    # its prerequisite — validate() caught exactly that twice: first "NURS 201
    # (term 4) requires NURS 109 (term 4)", then again after a partial fix,
    # because a final min(depth, terms) re-collapsed what pruning had just
    # separated. A four-term associate cannot carry a six-deep chain, so the
    # chain must be SHORTENED until it fits — never clamped afterwards.
    def _recompute():
        placed.clear()
        for c in courses:
            c["term"] = depth(c["title"])
        return max((c["term"] for c in courses), default=1)

    deepest = _recompute()
    if deepest > terms:
        logger.warning(
            f"prerequisite chains run {deepest} deep in a {terms}-term "
            f"programme — pruning inferred edges until the ordering can actually "
            f"be honoured")
    guard = 0
    while deepest > terms and guard < 200:
        guard += 1
        # Drop one edge from the deepest course. These are INFERRED from
        # catalogue conventions, not stated by a registrar, so they are the
        # right thing to give up when the programme cannot carry them.
        worst = max(courses, key=lambda c: (c["term"], len(c["requires"] or [])))
        if not worst["requires"]:
            logger.warning("cannot shorten further — a course with no "
                           "prerequisites is still too deep; giving up")
            break
        dropped = worst["requires"].pop()
        logger.info(f"  dropped inferred prerequisite {worst['title']!r} "
                    f"<- {dropped!r}")
        deepest = _recompute()

    # Capacity: spill overflowing terms later, but never earlier than a
    # prerequisite allows.
    cap = per_term or max(1, round(len(courses) / max(1, terms)))
    for t in range(1, terms + 1):
        while True:
            here = [c for c in courses if c["term"] == t]
            if len(here) <= cap or t >= terms:
                break
            # A course may only be pushed later if BOTH directions still hold:
            # its own prerequisites stay earlier, AND nothing that depends on it
            # is already at or before the new term. Checking only the first
            # direction let a prerequisite overtake its dependent — validate()
            # caught "NUR 201 (term 4) requires NUR 109 (term 4)" three separate
            # times before this was the fix.
            dependents = {}
            for other in courses:
                for r in (other.get("requires") or []):
                    dependents.setdefault(r, []).append(other)

            def _can_move(c):
                if any(index[r]["term"] >= t + 1
                       for r in (c.get("requires") or []) if r in index):
                    return False
                return all(d["term"] > t + 1 for d in dependents.get(c["title"], []))

            movable = [c for c in here if _can_move(c)]
            if not movable:
                break
            movable[-1]["term"] = t + 1

    # A capstone belongs at the end regardless of how shallow its graph is —
    # but only if nothing depends on it, which for a capstone should be nothing
    # at all. Moving it blindly would repeat the overtaking bug above.
    dependents_of = set()
    for c in courses:
        dependents_of.update(c.get("requires") or [])
    for c in courses:
        if c.get("slot") == "capstone" and c["title"] not in dependents_of:
            c["term"] = terms
    return courses


def plan_degree(subject, template, llm_json_fn=None, search_fn=None,
                preset="college"):
    """The single entry point: a subject and a degree tier in, a plan out.

    Everything above this is a step; this is the thing a caller invokes. The
    pieces existed and nothing joined them, so the whole degree tier was
    orphaned from the build pipeline — real course names, a real prerequisite
    graph, a real term layout, and no way to ask for any of it.

    The full cascade, in the order the course tier established:

        1. a transcribed curriculum for this degree, if one exists
        2. gaps in it filled rather than the curriculum discarded
        3. otherwise the model proposes the course list, labelled as such
        4. prerequisites inferred from catalogue conventions, then the model
        5. terms laid out topologically, chains shortened to fit the programme
        6. validated — cycles, missing prerequisites, duplicates, ordering

    Raises ProgramError only if the result is genuinely unteachable, which is
    worth failing on: an incoherent programme is invisible until a learner
    reaches a course they cannot follow, months in.
    """
    tpl = TEMPLATES.get(template)
    if not tpl:
        raise ProgramError(f"unknown template {template!r}")

    sourced = source_degree_slots(subject, template,
                                  llm_json_fn=llm_json_fn, search_fn=search_fn)
    plan = plan_from_template(subject, template,
                              slot_subjects=sourced.get("slots"), preset=preset)

    propose = None
    if llm_json_fn:
        def propose(titles):
            data = llm_json_fn(
                prompt=("Which of these courses require another as a "
                        "prerequisite? Only real dependencies — a course that "
                        "genuinely cannot be taken first.\n\n"
                        + "\n".join(f"  - {t}" for t in titles)),
                sys_prompt="You know degree prerequisites. JSON only.",
                schema={"type": "object", "properties": {"edges": {
                    "type": "array", "items": {"type": "object", "properties": {
                        "course": {"type": "string"},
                        "requires": {"type": "string"}},
                        "required": ["course", "requires"]}}},
                    "required": ["edges"]},
                max_tokens=700)
            if isinstance(data, list):
                data = next((d for d in data if isinstance(d, dict)), None)
            return (data or {}).get("edges") or []

    infer_prerequisites(plan["courses"], propose_fn=propose)
    assign_terms(plan["courses"], tpl["terms"])
    validate(plan["courses"])

    plan["curriculum_source"] = sourced.get("source")
    plan["authoritative"] = sourced.get("authoritative")
    if sourced.get("reference"):
        plan["reference"] = sourced["reference"]
    if sourced.get("note"):
        plan["note"] = sourced["note"]
    plan["prerequisite_edges"] = sum(len(c.get("requires") or [])
                                     for c in plan["courses"])
    logger.info(f"[DEGREE] {subject!r} {tpl['label']}: "
                f"{len(plan['courses'])} courses, "
                f"{plan['prerequisite_edges']} prerequisite edges, "
                f"{tpl['terms']} terms, source={plan['curriculum_source']}")
    return plan
