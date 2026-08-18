"""Is there enough subject here to fill the shape the learner asked for?

THE FAILURE THIS PREVENTS
-------------------------
A **master's degree in D&D lore**. Nothing about it is un-teachable — it is
ordinary conceptual material — but the subject cannot sustain forty courses. The
material runs out long before the structure does.

Left unchecked the model does not refuse. It emits forty plausible course titles
and pads them, which is this project's documented hollow-concept failure
multiplied by forty.

WHY THIS IS ARITHMETIC AND NOT A JUDGEMENT
------------------------------------------
The obvious implementation is to ask the model "is there enough material?", and
that is the one thing that cannot work: the same optimism that generates forty
padded courses will answer yes. The research brief already counts the evidence,
so the trigger is a comparison of two numbers. The model's role is to EXPLAIN a
verdict the arithmetic reached, never to reach it.

THE RULE THAT MATTERS MOST
--------------------------
**A degraded brief can never produce this warning.** If lookups failed or were
throttled, thin evidence means *we could not look* — not *the subject is thin*.
Telling a learner their subject is too small when Wikimedia was rate-limiting
would be the absent-vs-zero error delivered straight to a user, which is the
worst place this project has ever put it.

SOURCELESS IS NOT OVER-STRETCHED
--------------------------------
D&D DMing has little academic literature and is still a real, deep practice. The
comparison is evidence volume RELATIVE TO REQUESTED SCOPE, so a 6-course
certificate in it passes cleanly while a 40-course master's does not.
"""

import logging

logger = logging.getLogger(__name__)

# Concepts one real chapter of structural evidence can honestly support. A
# chapter is a chapter's worth of material; asking it to carry a whole course is
# how padding starts. Deliberately generous — this check should fire on the
# indefensible, not on the ambitious.
CONCEPTS_PER_CHAPTER = 6

# Below this ratio of available-to-requested material, warn.
WARN_RATIO = 0.5


def assess_scope(brief, requested_concepts, requested_courses=1):
    """Compare evidence volume against the requested course/program size.

    Returns a verdict dict. `verdict` is one of:
      "ok"            — enough evidence for what was asked
      "stretched"     — noticeably thin; offer a smaller shape
      "unsupported"   — very little evidence for a large ask
      "unknown"       — the brief was degraded, or there was no brief at all;
                        NO claim is made about the subject either way
    """
    out = {
        "requested_concepts": requested_concepts,
        "requested_courses": requested_courses,
        "verdict": "unknown",
        "reason": "",
    }
    if not isinstance(brief, dict) or not brief:
        out["reason"] = "no research brief — scope not assessed"
        return out

    if brief.get("degraded"):
        out["reason"] = ("research was degraded (failed or throttled lookups) — "
                         "thin evidence here means we could not look, not that "
                         "the subject is thin")
        logger.info("[SCOPE] brief degraded — suppressing any over-stretch verdict")
        return out

    chapters = int(brief.get("chapter_count") or 0)
    sources = int(brief.get("structural_sources") or 0)
    out["chapter_count"] = chapters
    out["structural_sources"] = sources

    if sources == 0:
        # No structural evidence at all. That is NOT the same as over-stretched:
        # plenty of real practices have no open syllabus. It is only alarming in
        # combination with a large ask, and the caller labels the course
        # sourceless regardless.
        out["verdict"] = "unsupported" if requested_courses > 3 else "unknown"
        out["reason"] = (
            f"no published syllabus found, and {requested_courses} courses were "
            f"requested" if requested_courses > 3 else
            "no published syllabus found; at this size that is not by itself a "
            "problem")
        return out

    supportable = chapters * CONCEPTS_PER_CHAPTER
    ratio = supportable / requested_concepts if requested_concepts else 1.0
    out["supportable_concepts"] = supportable
    out["ratio"] = round(ratio, 2)

    if ratio >= WARN_RATIO:
        out["verdict"] = "ok"
        out["reason"] = (f"{chapters} chapters of real syllabus support roughly "
                         f"{supportable} concepts against {requested_concepts} "
                         f"requested")
        return out

    out["verdict"] = "unsupported" if ratio < 0.25 else "stretched"
    out["reason"] = (
        f"{chapters} chapters across {sources} source(s) support roughly "
        f"{supportable} concepts, but {requested_concepts} were requested "
        f"({int(ratio * 100)}% of what the evidence carries)")
    out["suggested_concepts"] = max(CONCEPTS_PER_CHAPTER, supportable)
    if requested_courses > 1:
        per_course = max(1, requested_concepts // max(1, requested_courses))
        out["suggested_courses"] = max(1, supportable // max(1, per_course))
    return out


def describe(assessment):
    """One learner-facing sentence, or "" when there is nothing to say.

    Names the specific shortfall and the concrete alternative. A warning that can
    only be accepted or cancelled trains people to accept; one that offers the
    right-sized option is actionable.
    """
    v = (assessment or {}).get("verdict")
    if v not in ("stretched", "unsupported"):
        return ""
    a = assessment
    smaller = a.get("suggested_courses")
    if smaller and a.get("requested_courses", 1) > 1:
        return (f"There may not be enough published material on this subject to "
                f"fill {a['requested_courses']} courses. The evidence found "
                f"supports roughly {smaller}. You can build the smaller version, "
                f"broaden the subject, or continue as asked.")
    return (f"There may not be enough published material on this subject to fill "
            f"a course of {a['requested_concepts']} concepts — the evidence found "
            f"supports roughly {a.get('suggested_concepts', '?')}. You can build "
            f"the smaller version, broaden the subject, or continue as asked.")
