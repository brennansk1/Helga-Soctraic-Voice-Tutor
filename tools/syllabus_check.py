#!/usr/bin/env python3
"""syllabus_check.py — coverage vs a REAL syllabus (quality gate criterion 6).

WHY THIS ONE MATTERS MOST
-------------------------
Every other criterion in the gate is self-referential: an LLM judging output
produced by an LLM. They catch a lot (missing rigor, false claims, wrong
level), but they cannot tell you that a course on causal inference simply never
mentions instrumental variables. Only comparison against something written by a
human who teaches the subject can do that.

So this is the gate's external anchor, and its degraded mode is labelled
loudly rather than quietly substituted.

KNOWN LIMITATION — read before trusting a number
------------------------------------------------
With a 9B judge this instrument DISCRIMINATES reliably but UNDERCOUNTS. In
self-test it correctly separates a complete outline (ADEQUATE) from a gutted one
(0%, INADEQUATE), but scores the complete outline at only ~71% because the model
omits plainly-present topics from its "covered" list — "Potential outcomes" is
declared missing from an outline whose first module is literally "Potential
Outcomes". Token-overlap matching was added and did not close the gap, so the
shortfall is in the model's judgement, not the string matching.

Treat the verdict as meaningful and the percentage as a LOWER BOUND. A larger
judge model would tighten this; until then, do not chase the last 20% of
reported coverage, and investigate a LOW score rather than trusting a high one.

WHAT IT CHECKS (and deliberately does NOT)
------------------------------------------
Checks:  topic COVERAGE — are the discipline's core topics present?
         SEQUENCING — is anything taught before its prerequisite?
Ignores: format, pacing, lesson length, assessment structure, credit hours.

Helga is "the Duolingo of a college course": the delivery format is SUPPOSED to
differ from a university syllabus. Penalising that would be measuring the wrong
thing — see docs/SPRINT_PLAN.md.

USAGE
    # Best: compare against a real syllabus you supply
    python3 tools/syllabus_check.py --course course_x --reference syllabus.txt

    # Degraded: no external reference, judge from model knowledge. Clearly
    # weaker — it is the same model that wrote the course.
    python3 tools/syllabus_check.py --course course_x --no-reference

    # Validate the instrument itself
    python3 tools/syllabus_check.py --self-test
"""

import argparse
import json
import os
import logging
import re
import sys

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

COURSES_DIR = os.path.join(_ROOT, "data", "courses")

# TWO CALLS, not one.
#
# A single "enumerate the core topics AND mark which are covered" call failed
# twice with a 9B model: first it returned a bare holistic verdict rating a
# GUTTED outline as ADEQUATE, then, asked for a checklist, it emitted
# {"topic_id","name","description"} and omitted the `present` boolean entirely
# — it enumerated topics and never did the judging step.
#
# Splitting the work makes each call a simple task the model does reliably:
#   1. extract core topics from the reference   -> list of strings
#   2. mark which of those the outline covers   -> list of booleans
# The verdict is then computed IN CODE from the result, never left to the model.

_TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "is_core": {"type": "boolean"}
                },
                "required": ["topic", "is_core"]
            }
        }
    },
    "required": ["topics"],
}

_TOPICS_SYSTEM = """List the CORE and SECONDARY topics a real course on this subject must cover,
taken from the reference material. 8-15 topics, short noun phrases, using the
reference's own terminology. Mark each as either core (is_core: true, i.e. a Tier I fundamental) or secondary (is_core: false, i.e. a Tier II/optional topic).
Return STRICT JSON."""

_COVER_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "introduced": {"type": "boolean"},
                    "practiced": {"type": "boolean"},
                    "assessed": {"type": "boolean"},
                    "evidence": {"type": "string"},
                    "legacy_covered": {"type": "boolean"}
                },
                "required": ["topic", "introduced", "practiced", "assessed", "evidence", "legacy_covered"]
            }
        },
        "sequencing_problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["topics"],
}

_COVER_SYSTEM = """You are given a list of required topics and a course outline.

For each topic, provide:
1. legacy_covered: true if the outline mentions or teaches the topic (the old definition of covered).
2. introduced: true if the course explicitly introduces or explains the topic.
3. practiced: true if there is a worked example or exercise for the topic.
4. assessed: true if there is a question or check whose answer requires the topic.
5. evidence: A short snippet of evidence for your judgement.

Also list any SEQUENCING problems: a concept taught before something it needs.

IGNORE lesson length, pacing, weeks, assessment style and formatting entirely —
this course is short interactive micro-lessons BY DESIGN, which is intended and
is NOT a defect.

Marking an absent topic as covered hides a real hole in someone's education.
Return STRICT JSON."""

def outline_of(struct):
    """Compact outline from a structure DICT, on disk or still in memory.

    Split out from _outline so the generator can run this check pre-persist.
    Criterion 6 was the only gate criterion that ran by hand, which meant the
    one check with external ground truth was the one nobody ran.
    """
    lines = []
    for m in (struct or {}).get("modules", []) or []:
        lines.append(f"MODULE: {m.get('title')}")
        for u in m.get("units", []) or []:
            # UNIT TITLES WERE DROPPED ENTIRELY. Any canonical topic name that
            # landed on a unit was invisible to coverage scoring, so the judge
            # was asked whether a course teaches something while being shown an
            # outline with that level deleted. Cheap to include, and it is a
            # level the generator actively names.
            if u.get("title"):
                lines.append(f"  UNIT: {u.get('title')}")
            for les in u.get("lessons", []) or []:
                names = [c.get("title") for c in (les.get("concepts") or [])]
                if names:
                    lines.append(f"  {les.get('title')}: " + "; ".join(names))
    return "\n".join(lines)


def _outline(uid):
    """Compact outline of a generated course: modules -> lessons -> concepts."""
    path = os.path.join(COURSES_DIR, uid, "structure.json")
    if not os.path.exists(path):
        return None, None
    with open(path) as f:
        s = json.load(f)
    return s, outline_of(s)


def core_topics(reference_text, subject, mastery, model=None):
    """Step 1: what must a real course cover?"""
    from services.common.fact_check import _post, _field
    if reference_text:
        user = (f"Subject: {subject} (level {mastery}/5)\n\n"
                f"Reference material:\n{reference_text[:6000]}")
    else:
        user = (f"Subject: {subject}, taught at level {mastery}/5. Use your own "
                f"knowledge of how this subject is actually taught.")
    d = _post([{"role": "system", "content": _TOPICS_SYSTEM},
               {"role": "user", "content": user}],
              schema=_TOPICS_SCHEMA, max_tokens=800, model=model)
    raw = _field(d or {}, "topics", "core_topics", "items", default=[]) or []
    out = []
    for t in raw:
        if isinstance(t, str) and t.strip():
            out.append({"topic": t.strip(), "is_core": True})
        elif isinstance(t, dict):
            # Tolerate {"name": ...} / {"topic": ...} shapes.
            v = _field(t, "topic", "name", "title")
            if v:
                is_core = t.get("is_core", True)
                if "tier" in t and str(t["tier"]).lower() == "secondary":
                    is_core = False
                out.append({"topic": str(v).strip(), "is_core": is_core})
    return out


def coverage(topics, outline, model=None):
    """Step 2: which of those does the outline actually teach?"""
    from services.common.fact_check import _post, _field
    if not topics:
        return None
    listing = "\n".join(f"- {t['topic'] if isinstance(t, dict) else t}" for t in topics)
    d = _post([{"role": "system", "content": _COVER_SYSTEM},
               {"role": "user", "content":
                f"REQUIRED TOPICS:\n{listing}\n\nCOURSE OUTLINE:\n{outline[:6000]}"}],
              schema=_COVER_SCHEMA, max_tokens=1200, model=model)
    if not d:
        return None

    analysis = _field(d, "topics", "topics_analysis", default=[]) or []

    # ACCEPT THE TOPIC-KEYED SHAPE TOO.
    #
    # The schema asks for a list under "topics_analysis". The model instead
    # returns the topics as TOP-LEVEL KEYS:
    #
    #     {"qubit representation": {...}, "superposition principle": {...},
    #      "sequencing_problems": [...]}
    #
    # The list came back empty, every topic scored un-introduced, and the
    # strict rubric reported 0% core / 0% secondary on a course containing
    # worked examples — a number that reads as a damning finding and is purely
    # a shape mismatch. This is the third time today a judge was blamed for a
    # key name: "covered_topics" vs "covered", and before that a silent judge
    # scored 0% instead of reporting itself unavailable.
    if not analysis and isinstance(d, dict):
        recovered = []
        for key, val in d.items():
            if key in ("sequencing_problems", "sequencing") or not isinstance(val, dict):
                continue
            entry = dict(val)
            entry.setdefault("topic", key)
            recovered.append(entry)
        if recovered:
            logger.info(f"[SYLLABUS] recovered {len(recovered)} topic-keyed "
                        f"entries the schema did not produce as a list")
            analysis = recovered
    seq = [str(x) for x in (_field(d, "sequencing_problems", "sequencing", default=[]) or [])]

    def _toks(x):
        return {w for w in re.findall(r"[a-z0-9]+", x.lower()) if len(w) > 2}

    analysis_by_toks = []
    for item in analysis:
        t_name = _field(item, "topic", "name", default="")
        analysis_by_toks.append((_toks(t_name), item))

    def _get_analysis(topic_name):
        tt = _toks(topic_name)
        if not tt: return None
        for ct, item in analysis_by_toks:
            if not ct: continue
            overlap = len(tt & ct) / len(tt)
            if overlap >= 0.6:
                return item
        return None

    if not analysis:
        logger.warning(
            "[SYLLABUS] judge returned no topic analysis — "
            "treating as NOT MEASURED rather than 0%% coverage")
        return None

    result_topics = []
    for t in topics:
        t_name = t["topic"] if isinstance(t, dict) else t
        is_core = t["is_core"] if isinstance(t, dict) else True
        item = _get_analysis(t_name)
        if item:
            legacy = _field(item, "legacy_covered", "covered", default=False)
            intro = _field(item, "introduced", default=False)
            prac = _field(item, "practiced", default=False)
            ass = _field(item, "assessed", default=False)
            ev = _field(item, "evidence", default="")
            result_topics.append({
                "topic": t_name,
                "is_core": is_core,
                "legacy_covered": legacy,
                "introduced": intro,
                "practiced": prac,
                "assessed": ass,
                "evidence": ev,
                "is_covered_strict": intro and prac and ass
            })
        else:
            result_topics.append({
                "topic": t_name,
                "is_core": is_core,
                "legacy_covered": False,
                "introduced": False,
                "practiced": False,
                "assessed": False,
                "evidence": "Not found in analysis.",
                "is_covered_strict": False
            })

    return {"topics_analysis": result_topics, "sequencing": seq}


def check_structure(struct, reference_text=None, model=None):
    """Run criterion 6 against an in-memory structure. Never raises.

    Returns a summary dict, or {"error": ...} when the instrument could not
    run. Course generation calls this, so a judge outage must degrade to
    "not measured" rather than taking down the build.
    """
    try:
        outline = outline_of(struct)
        if not outline:
            return {"error": "empty outline"}
        topics = core_topics(reference_text, struct.get("title"),
                             struct.get("mastery"), model=model) or []
        if not topics:
            return {"error": "judge could not derive core topics"}
        cov = coverage(topics, outline, model=model)
        # `or {"covered": [], "missing": []}` used to sit here, which turned a
        # judge outage into a scored 0% — the one thing this function's
        # docstring promises it will never do. An unmeasurable run is an error,
        # not a grade.
        if cov is None:
            return {"error": "judge unavailable — coverage not measured"}
        return _summarise(
            cov,
            uid=struct.get("uid"), title=struct.get("title"),
            mastery=struct.get("mastery"),
            grounding=("external" if reference_text
                       else "model-knowledge (WEAK — same model family "
                            "wrote the course)"))
    except Exception as e:  # instrument failure must not fail the course
        return {"error": f"syllabus check unavailable: {e}"}


def check(uid, reference_text=None, model=None, url=None):
    struct, outline = _outline(uid)
    if not outline:
        return {"error": f"no structure for {uid}"}
    title = struct.get("title")
    mastery = struct.get("mastery")
    grounding = ("external" if reference_text else
                 "model-knowledge (WEAK — same model family wrote the course)")

    topics = core_topics(reference_text, title, mastery, model=model)
    if not topics:
        return {"error": "could not derive core topics"}
    cov = coverage(topics, outline, model=model)
    if not cov:
        return {"error": "coverage judge unavailable"}
    return _summarise(cov, uid=uid, title=title, mastery=mastery,
                      grounding=grounding)


# Coverage below this is a real hole in the curriculum, not a stylistic gap.
MIN_COVERAGE = int(os.getenv("HELGA_MIN_COVERAGE_PCT", "70"))
CORE_MIN_COVERAGE = int(os.getenv("HELGA_CORE_MIN_COVERAGE_PCT", "100"))
SECONDARY_MIN_COVERAGE = int(os.getenv("HELGA_SECONDARY_MIN_COVERAGE_PCT", "80"))
LEGACY_COVERAGE_METRIC = os.getenv("HELGA_LEGACY_COVERAGE_METRIC", "0") == "1"


def observed_flags(topic, concept_bodies):
    """introduced / practiced / assessed, computed IN CODE from the documents.

    WHY NOT ASK THE JUDGE
    ---------------------
    The rubric was first asked of the model directly, and a 9B judge cannot
    make the distinction. Measured output, self-contradictory inside a single
    object:

        {"legacy_covered": true, "introduced": false, "practiced": false,
         "assessed": false,
         "evidence": "The outline mentions 'Bloch Sphere Coordinates' ..."}

    It marks the topic covered, cites evidence that the course teaches it, and
    sets introduced=false in the same breath — so the strict rubric reported
    0% core / 0% secondary on a course full of worked examples.

    The research warned about this failure in the OPPOSITE direction: LLMs
    over-credit a mention as teaching (100% coverage at kappa = 0.076). Either
    way the model cannot draw the line, so this repo's own rule applies —
    compute the verdict in code and never ask the model for an overall call.

    The three flags map onto sections the pipeline already guarantees:

        introduced  the topic is named in a concept title, Core Explanation
                    or Key Facts
        practiced   it appears in Worked Example / Real-World Examples —
                    the section the depth contract already requires
        assessed    it appears in Mastery Criteria, Socratic Hooks or an
                    exercise — where a learner is asked to produce something

    Substring matching is deliberate and matches how `_is_covered` already
    works: the generator rewords topics as it teaches them.
    """
    toks = {w for w in re.findall(r"[a-z0-9]+", (topic or "").lower())
            if len(w) > 3}
    if not toks:
        return {"introduced": False, "practiced": False, "assessed": False}

    def _mentions(text):
        low = (text or "").lower()
        hit = sum(1 for t in toks if t in low)
        return hit * 2 >= len(toks)          # half the topic's content words

    intro = practiced = assessed = False
    for body in concept_bodies:
        sections = {}
        current = "_"
        for line in (body or "").splitlines():
            if line.startswith("## "):
                current = line[3:].strip().lower()
                sections[current] = []
            else:
                sections.setdefault(current, []).append(line)
        joined = {k: "\n".join(v) for k, v in sections.items()}

        def _sec(*names):
            return "\n".join(joined.get(n, "") for n in names)

        if _mentions(_sec("_", "core explanation", "key facts")):
            intro = True
        if _mentions(_sec("real-world examples", "worked example", "examples")):
            practiced = True
        if _mentions(_sec("mastery criteria", "socratic hooks", "exercise",
                          "exercises")):
            assessed = True

    return {"introduced": intro, "practiced": practiced, "assessed": assessed}


def _summarise(cov, uid=None, title=None, mastery=None, grounding=None):
    """Compute the verdict IN CODE. Never ask the model for an overall call —
    asked directly, it rated a gutted outline ADEQUATE."""
    topics_analysis = cov["topics_analysis"]
    
    core_topics = [t for t in topics_analysis if t["is_core"]]
    secondary_topics = [t for t in topics_analysis if not t["is_core"]]
    
    def _pct(topics, key):
        if not topics: return 100
        return round(100 * sum(1 for t in topics if t[key]) / len(topics))

    # calculate strict metrics
    core_strict_pct = _pct(core_topics, "is_covered_strict")
    secondary_strict_pct = _pct(secondary_topics, "is_covered_strict")
    total_strict_pct = _pct(topics_analysis, "is_covered_strict")
    
    # calculate legacy metrics
    total_legacy_pct = _pct(topics_analysis, "legacy_covered")

    use_legacy = LEGACY_COVERAGE_METRIC
    
    if use_legacy:
        # legacy evaluation ignores core vs secondary for verdict, just uses MIN_COVERAGE
        verdict = "ADEQUATE" if (len(topics_analysis) and total_legacy_pct >= MIN_COVERAGE and not cov.get("sequencing")) else "INADEQUATE"
    else:
        verdict = "ADEQUATE" if (core_strict_pct >= CORE_MIN_COVERAGE and secondary_strict_pct >= SECONDARY_MIN_COVERAGE and not cov.get("sequencing")) else "INADEQUATE"

    missing = [t["topic"] for t in topics_analysis if not t["legacy_covered" if use_legacy else "is_covered_strict"]]

    return {
        "course": uid, "title": title, "mastery": mastery,
        "grounding": grounding,
        "topics_checked": len(topics_analysis),
        "coverage_pct": total_legacy_pct if use_legacy else total_strict_pct,
        "coverage_legacy_pct": total_legacy_pct,
        "coverage_strict_pct": total_strict_pct,
        "core_strict_pct": core_strict_pct,
        "secondary_strict_pct": secondary_strict_pct,
        "missing": missing,
        "sequencing": cov.get("sequencing") or [],
        "verdict": verdict,
    }


# --- instrument self-validation ----------------------------------------------

_GOOD_OUTLINE_REF = """Week 1-2: Potential outcomes, counterfactuals, SUTVA.
Week 3-4: Randomised experiments, ATE/ATT estimands.
Week 5-6: Confounding, backdoor criterion, DAGs.
Week 7-8: Propensity scores, matching, weighting.
Week 9-10: Instrumental variables, LATE, weak instruments.
Week 11-12: Difference-in-differences, regression discontinuity.
Week 13: Sensitivity analysis."""


def self_test(model=None):
    """Must notice a gutted outline and not cry wolf on a complete one."""
    full = ("MODULE: Potential Outcomes\n  Counterfactuals: SUTVA; ATE; ATT\n"
            "MODULE: Confounding\n  DAGs: backdoor criterion; colliders\n"
            "MODULE: Propensity Scores\n  Matching: weighting; trimming\n"
            "MODULE: Instrumental Variables\n  LATE: weak instruments\n"
            "MODULE: Quasi-experiments\n  DiD: regression discontinuity\n"
            "MODULE: Robustness\n  Sensitivity analysis: bounds\n")
    gutted = "MODULE: Introduction\n  What is causality: correlation; examples\n"

    topics = core_topics(_GOOD_OUTLINE_REF, "causal inference", 4, model=model)
    print(f"  derived {len(topics)} core topics from the reference")
    if not topics:
        print("\n  INSTRUMENT UNRELIABLE — could not derive topics.")
        return False

    results = {}
    for name, outline in (("complete outline", full), ("gutted outline", gutted)):
        cov = coverage(topics, outline, model=model)
        if not cov:
            print(f"  {name:20} -> judge unavailable")
            return False
        r = _summarise(cov)
        results[name] = r["verdict"]
        print(f"  {name:20} -> {r['verdict']}  (coverage {r['coverage_pct']}%)")
        if r["missing"]:
            print(f"      missing: {', '.join(r['missing'][:4])}")

    ok = (results.get("gutted outline") == "INADEQUATE"
          and results.get("complete outline") == "ADEQUATE")
    print("\n  " + ("Instrument is usable." if ok else
                    "INSTRUMENT UNRELIABLE — do not trust its verdicts."))
    return ok


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course")
    p.add_argument("--reference", help="path to a real syllabus (text)")
    p.add_argument("--no-reference", action="store_true",
                   help="judge from model knowledge (clearly weaker)")
    p.add_argument("--model")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--out")
    args = p.parse_args()

    if args.self_test:
        print("Syllabus-checker self-test\n")
        return 0 if self_test(args.model) else 1

    if not args.course:
        print("--course required (or --self-test)")
        return 2
    ref = None
    if args.reference:
        with open(args.reference) as f:
            ref = f.read()
    elif not args.no_reference:
        print("Provide --reference <real syllabus> for a meaningful check, or "
              "pass --no-reference to accept the weaker model-knowledge mode.")
        return 2

    r = check(args.course, ref, model=args.model)
    if r.get("error"):
        print(r["error"])
        return 1

    print(f"Course : {r['title']} (mastery {r['mastery']})")
    print(f"Grounding: {r['grounding']}")
    print(f"Coverage (Strict): {r['coverage_strict_pct']}% (Core: {r['core_strict_pct']}%, Secondary: {r['secondary_strict_pct']}%)")
    print(f"Coverage (Legacy): {r['coverage_legacy_pct']}%")
    print(f"Topics checked: {r['topics_checked']}")
    if r["missing"]:
        print("Missing core topics:")
        for t in r["missing"][:12]:
            print(f"  - {t}")
    if r["sequencing"]:
        print("Sequencing problems:")
        for t in r["sequencing"][:8]:
            print(f"  - {t}")
    print(f"\nVERDICT: {r['verdict']}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(r, f, indent=2)
    return 0 if r["verdict"] == "ADEQUATE" else 1


if __name__ == "__main__":
    sys.exit(main())
