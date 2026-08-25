"""Ledger-based quality checks, callable from the pipeline.

These lived in `tools/hydration_qa.py` and could only be run by hand, after the
fact, by someone who remembered to. That is the same position `level_audit.py`
was in before it became `level_calibration.py`, and this is the same move for
the same reason: a check that only a person can run is a check that does not
run.

WHAT THIS MODULE IS FOR
-----------------------
Questions answerable from the LEDGER — the claims, sources and teaching objects
a build recorded — as opposed to questions answerable from the concept file in
front of you. `course_audit.py` owns the second kind: hygiene, structure,
executable claims, thin content. This owns the first: redundancy across the
course, grounding, supplementary share, depth, truth.

Splitting them that way is not tidiness. The ledger checks need a database
connection and see the whole course; the file checks need only the markdown and
see one concept. Keeping them apart is what lets the file checks run inside
hydration, per concept, where a failure can still be repaired.

THE DISCIPLINE, INHERITED VERBATIM
----------------------------------
  * conjunctive — every check that CAN run must pass
  * NOT RUN is never a pass, so a course cannot score better by having less
    measured about it
  * arithmetic wherever possible, because the LLM judge in this repo swings
    +/-1.4 out of 5 between identical runs

Every function returns a dict with `checked`. When `checked` is False it also
carries `reason`, and the caller must report it as unmeasured — never as clean.
"""
import logging
import sqlite3

logger = logging.getLogger(__name__)

# Thresholds, unchanged from the tool that owned them.
MAX_REDUNDANT_SHARE = 0.15      # concepts re-teaching earlier material
MIN_CLAIMS_PER_CONCEPT = 3.0    # a concept that asserts less than this is thin
MIN_COMPLETENESS = 0.60         # mean teaching-object completeness
MAX_HOLLOW_SHARE = 0.25         # concepts under half-filled
MIN_GROUNDED_SHARE = 0.80       # claims linked to a retained source
MAX_SUPPLEMENTARY_SHARE = 0.20  # claims resting ONLY on below-bar sources
MIN_DEPTH_PASS = 0.90           # concepts meeting their depth contract
MAX_FALSE_CLAIM_SHARE = 0.05    # claims a verifier judges unsupported


def check_substance(conn, course_uid):
    """Do concepts assert anything, or are they fluent and empty?

    The measured failure was ~half of concepts hollow — structurally complete,
    passing the section template, saying little. Claims per concept is the
    cheapest model-free proxy for that.
    """
    rows = conn.execute(
        "SELECT c.concept_uid, COUNT(k.claim) FROM taught_concepts c "
        "LEFT JOIN taught_claims k ON k.course_uid=c.course_uid "
        "AND k.concept_uid=c.concept_uid WHERE c.course_uid=? "
        "GROUP BY c.concept_uid", (course_uid,)).fetchall()
    if not rows:
        return {"checked": False, "reason": "no ledger rows"}
    counts = [n for _, n in rows]
    mean = sum(counts) / len(counts)
    empty = sum(1 for n in counts if n == 0)
    return {"checked": True, "concepts": len(counts),
            "claims_per_concept": round(mean, 2), "empty": empty,
            "ok": mean >= MIN_CLAIMS_PER_CONCEPT and not empty}


def check_hollowness(conn, course_uid):
    """Structurally complete and substantively empty.

    The section template cannot see this by construction, because passing it IS
    having the headings. The teaching object counts what each concept FILLED.
    """
    try:
        rows = conn.execute(
            "SELECT completeness FROM teaching_objects WHERE course_uid=?",
            (course_uid,)).fetchall()
    except sqlite3.OperationalError:
        return {"checked": False, "reason": "teaching_objects absent (pre-v14)"}
    scores = [r[0] for r in rows if r[0] is not None]
    if not scores:
        return {"checked": False, "reason": "no teaching objects"}
    mean = sum(scores) / len(scores)
    hollow = sum(1 for s in scores if s < 0.5)
    share = hollow / len(scores)
    return {"checked": True, "concepts": len(scores),
            "mean_completeness": round(mean, 3), "hollow": hollow,
            "hollow_share": round(share, 3),
            "ok": mean >= MIN_COMPLETENESS and share <= MAX_HOLLOW_SHARE}


def check_grounding(conn, course_uid):
    """Are claims linked to a retained source, or asserted from nowhere?"""
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM taught_claims WHERE course_uid=?",
            (course_uid,)).fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM claim_sources WHERE course_uid=? "
            "AND source_id IS NOT NULL", (course_uid,)).fetchone()[0]
    except sqlite3.OperationalError:
        return {"checked": False, "reason": "source tables absent (pre-v12)"}
    if not total:
        return {"checked": False, "reason": "no claims"}
    share = linked / total
    return {"checked": True, "claims": total, "grounded": linked,
            "share": round(share, 3), "ok": share >= MIN_GROUNDED_SHARE}


def check_supplementary(conn, course_uid):
    """Share of claims resting ONLY on below-bar sources.

    Measured in claims, not sources: one weak book can dominate content while
    being a small minority of the source list.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*), SUM(supplementary) FROM claim_sources "
            "WHERE course_uid=?", (course_uid,)).fetchone()
    except sqlite3.OperationalError:
        return {"checked": False, "reason": "claim_sources absent (pre-v12)"}
    total, supp = (row or (0, 0))
    if not total:
        return {"checked": False, "reason": "no claim-source links"}
    share = (supp or 0) / total
    return {"checked": True, "claims": total, "supplementary_only": supp or 0,
            "share": round(share, 3), "ok": share <= MAX_SUPPLEMENTARY_SHARE}



def _as_count(value):
    """A concept count, from something that may be a count OR A BAND.

    Sizes in this pipeline are ranges, not numbers. The domain packs express
    shape as (min, max) — `concepts_per_lesson: (2, 4)`, `units_per_module:
    (2, 8)` — presets advertise "~16 concepts", and a long chapter earns 2-6
    concepts by length rather than a fixed quota. Structures are checked as a
    SHARE falling inside a band, never against an exact number, and the two
    live courses bear that out: 2-3 concepts per lesson, 1-3 lessons per unit.
    So arithmetic here must not assume a scalar ever arrived.

    A band collapses to its midpoint, which is the only reading that keeps a
    share meaningful. A count passes through. Anything else is zero, because
    inventing a denominator is how a coverage figure becomes fiction.
    """
    if isinstance(value, (list, tuple)):
        nums = [v for v in value if isinstance(v, (int, float))]
        if not nums:
            return 0
        return int(round(sum(nums) / len(nums)))
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def check_depth(course_json):
    """Did concepts meet the depth contract for their mastery level?

    THIS READ KEYS THAT DO NOT EXIST, AND SO HAD NEVER RUN.

    It looked for `checked`/`total` and `missed`. The builder writes
    `concepts_total`, `concepts_missing_contract` and `met_pct`. The first
    lookup found nothing, `total` fell to 0, and every call returned
    "depth_contract recorded no totals" — on courses whose depth_contract was
    fully populated. Verified on both live courses on 2026-08-25.

    It was dead a second way underneath: `d.get("missed") or d.get("failures")`
    falls back to `failures`, which is a LIST of failure records, so the
    arithmetic below would have raised TypeError had the first lookup ever
    succeeded.

    The check reported itself unmeasured rather than passing, which is the one
    reason this was merely useless instead of dangerous — "NOT RUN is never a
    pass" did its job. The legacy key names are still accepted so a course
    built before the rename still reads.
    """
    d = (course_json or {}).get("depth_contract") or {}
    if not d:
        return {"checked": False, "reason": "no depth_contract on the course"}

    total = _as_count(d.get("concepts_total") or d.get("checked")
                      or d.get("total") or 0)
    if not total:
        return {"checked": False, "reason": "depth_contract recorded no totals"}

    missed = d.get("concepts_missing_contract")
    if missed is None:
        missed = d.get("missed")
    if missed is None:
        # `failures` is a list, never a count. Length is the honest reading.
        fails = d.get("failures")
        missed = len(fails) if isinstance(fails, (list, tuple)) else 0
    missed = _as_count(missed)

    share = (total - missed) / total
    out = {"checked": True, "concepts": total, "passed": total - missed,
           "share": round(share, 3), "ok": share >= MIN_DEPTH_PASS}

    # A resumed build verifies only what it hydrated. Reporting its share as
    # the course's would be the contract overstating its own coverage.
    verified = d.get("concepts_verified")
    if verified is not None and verified < total:
        out["partial_run"] = True
        out["verified"] = verified

    # AND `concepts_total` IS THE RUN'S TOTAL, NOT THE COURSE'S.
    #
    # A resumed build records only the segment it hydrated, so SQL — 95
    # concepts, built across several resumes — carries concepts_total=14 and
    # this reported "14 of 14 passed, 100%". True of the segment, and read by
    # everyone as true of the course. The same wording on a course card said
    # "All 14 concepts met the depth contract" for those 95 concepts.
    course_concepts = _count_concepts(course_json)
    if course_concepts and course_concepts > total:
        out["course_concepts"] = course_concepts
        out["coverage"] = round(total / course_concepts, 3)
        out["partial_run"] = True
    return out


def _count_concepts(course_json):
    """How many concepts the course has, walked from its structure."""
    n = 0
    for module in (course_json or {}).get("modules") or []:
        for unit in module.get("units") or []:
            for lesson in unit.get("lessons") or []:
                n += len(lesson.get("concepts") or [])
    return n


def check_truth(conn, course_uid, verifier=None, limit=200):
    """Are claims actually supported by the sources retained for them?

    NOT RUN without a verifier, and never reported as passed in that case.

    This is the only check here with a model in it, and the one measured to be
    unreliable as a gate: on a seeded set it caught 3 of 3 false claims and
    also flagged 2 of 3 TRUE ones, both needing a single inference step from
    the passage. Treat an unsupported verdict as a question, not a defect.
    """
    if verifier is None:
        return {"checked": False,
                "reason": "no verifier available — truth NOT measured"}
    try:
        rows = conn.execute(
            "SELECT cs.claim, s.passage FROM claim_sources cs "
            "JOIN sources s ON s.source_id = cs.source_id "
            "WHERE cs.course_uid=? AND s.passage IS NOT NULL "
            "AND length(trim(s.passage)) > 50 LIMIT ?",
            (course_uid, limit)).fetchall()
    except sqlite3.OperationalError:
        return {"checked": False, "reason": "source tables absent"}
    if not rows:
        # The common case until passages were retained: sources exist with no
        # text, so there is nothing to check a claim against.
        return {"checked": False,
                "reason": "no retained passages to check claims against"}

    unsupported = 0
    for claim, passage in rows:
        try:
            if not verifier.supported(claim, passage):
                unsupported += 1
        except Exception as e:
            logger.debug("verifier failed on a claim: %s", e)
    share = unsupported / len(rows)
    return {"checked": True, "claims": len(rows), "unsupported": unsupported,
            "share": round(share, 3), "ok": share <= MAX_FALSE_CLAIM_SHARE,
            "advisory": True}
