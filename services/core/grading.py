"""Grading — anchored, pre-committed, and split from its consequences.

THE PROBLEM, MEASURED
---------------------
This project measured its own judge swinging **±1.4 out of 5 on identical
input**. That is not an anomaly; it is normal small-judge behaviour. Expect
Krippendorff's α around **0.4-0.6** untreated — Qwen-3 reached 0.563 on MT-Bench
against an accepted-good threshold of 0.80, and our grader is the same family
quantised harder at IQ3_S.

A single unanchored 1-5 scalar then drives three decisions at once: Bloom
movement, the FSRS review rating, and mode selection. Three decisions riding on
a coin flip.

FOUR FIXES, CHEAPEST FIRST
--------------------------
1. **Keep five levels, add named anchors.** The scale is not the problem. A
   5-point rubric measured best across judges — highest exact agreement,
   highest bucketed agreement, lowest normalised variance — degrading
   monotonically at 6+ points. What produces the swing is holistic grading with
   no concrete descriptors.

2. **Grade against the concept's stored grade-3 threshold**, written at
   hydration and already sitting in `teaching_objects`. Committing the criterion
   *before* seeing the answer turns the judgement into a comparison against a
   fixed target rather than a fresh impression. The cheapest reliability gain
   available, and it needs no new data.

3. **Split the downstream uses.** Mode selection tolerates ±1 noise; FSRS
   integrates over many reviews; **Bloom promotion does not** — a spurious
   two-in-a-row ≥3 pushes a learner past their level. Hence `clean_margin`.

4. **Return a misconception id, not just a score.** A classification against a
   fixed small set is more stable than a number, and it is more actionable —
   the belief/correction pairs are already stored and currently unused.

THE TRAP
--------
A grader that always returns 3 shows excellent test-retest agreement and carries
zero information. **Stability alone is not the metric**; `score_entropy` exists
so a low-entropy grader cannot be mistaken for a reliable one.
"""

import logging
import math
import re
from collections import Counter

logger = logging.getLogger(__name__)

# Named, behaviourally-anchored levels. Deliberately about observable features
# of an answer, not abstract quality words like "good" or "excellent", which are
# what an unanchored scale leaves the model to invent per turn.
ANCHORS = {
    5: "Meets the threshold and goes beyond it: correct, and connects the idea "
       "to something else or identifies where it breaks down.",
    4: "Meets the threshold cleanly, with correct reasoning and no significant "
       "gap.",
    3: "Meets the stated threshold. This is the pass mark.",
    2: "Partially correct: the right general direction with a material gap, "
       "omission, or one clear error.",
    1: "Incorrect, or correct only by restating the question without reasoning.",
}

# A grade that only just reaches the pass mark must not promote a learner.
CLEAN_MARGIN_GRADE = 4


def rubric_block(threshold, bloom_level=None):
    """The rubric handed to the grader, anchored on the concept's own threshold.

    `threshold` is the per-concept grade-3 criterion written at hydration. If a
    concept has none, the rubric still works but is weaker — the anchors carry
    it, and that degradation is visible rather than silent.
    """
    lines = ["### GRADING RUBRIC — score 1 to 5"]
    if threshold:
        lines += [f"THE PASS MARK (3) FOR THIS CONCEPT IS: {threshold}",
                  "Grade by comparing the answer against that stated threshold, "
                  "not against a general impression of quality."]
    else:
        lines.append("No concept-specific threshold was recorded; grade against "
                     "the anchors alone.")
    if bloom_level:
        lines.append(f"The learner is working at Bloom level {bloom_level}; "
                     f"judge the answer at that level, not a higher one.")
    for score in (5, 4, 3, 2, 1):
        lines.append(f"  {score} — {ANCHORS[score]}")
    lines.append("Return the score and, if the answer shows a listed "
                 "misconception, that misconception's id.")
    return "\n".join(lines)


def misconception_block(pairs, limit=6):
    """Known misconceptions for this concept, as a fixed set to classify against.

    Identifying WHICH misconception an answer exhibits is more stable than
    scoring it — a classification against a small closed set rather than a point
    on a continuum — and it is far more actionable for the next teaching move.
    """
    if not pairs:
        return ""
    lines = ["### KNOWN MISCONCEPTIONS (classify against these, or answer none)"]
    for i, p in enumerate(pairs[:limit], 1):
        belief = (p.get("belief") or "").strip()
        if belief:
            lines.append(f"  m{i}: {belief[:160]}")
    return "\n".join(lines) if len(lines) > 1 else ""


GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "integer"},
        "misconception": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["grade"],
}


def parse_grade(raw):
    """Pull a grade out of whatever came back. Returns (grade, misconception).

    Returns `(None, ...)` when nothing usable was produced, so the caller can
    mark the exchange ungraded rather than inventing a number — a fabricated
    grade would enter FSRS as a real assessment.
    """
    if isinstance(raw, dict):
        g = raw.get("grade")
        m = raw.get("misconception") or None
        try:
            g = int(g)
        except (TypeError, ValueError):
            g = None
        if m in ("none", "None", ""):
            m = None
        return (g if g and 1 <= g <= 5 else None), m
    if isinstance(raw, (int, float)):
        g = int(raw)
        return (g if 1 <= g <= 5 else None), None
    if isinstance(raw, str):
        m = re.search(r"\b([1-5])\b", raw)
        return (int(m.group(1)) if m else None), None
    return None, None


def is_clean_margin(grade):
    """Did the answer clear the bar, or scrape it?

    The hysteresis that keeps grader noise out of Bloom promotion. A 3 is a
    pass and must not promote: with a judge whose α is ~0.5, a 3 is as likely to
    have been a 2.
    """
    return grade is not None and grade >= CLEAN_MARGIN_GRADE


def to_fsrs_rating(grade):
    """1-5 grade to an FSRS rating (1 Again, 2 Hard, 3 Good, 4 Easy).

    FSRS integrates over many reviews and tolerates the noise; the mapping is
    deliberately coarse so a one-point grader wobble rarely crosses a boundary.
    """
    if grade is None:
        return None
    if grade <= 1:
        return 1
    if grade == 2:
        return 2
    if grade <= 4:
        return 3
    return 4


# --- instrumentation: is the grader actually working? ------------------------

def score_entropy(grades):
    """Shannon entropy of a grade distribution, in bits.

    THE INSTRUMENT THAT CATCHES A BROKEN GRADER LOOKING HEALTHY. A grader that
    always returns 3 has perfect test-retest agreement and zero information.
    Stability must always be read alongside this.

    log2(5) ≈ 2.32 is the maximum for a five-point scale; anything below ~0.8
    means the grader is barely discriminating.
    """
    vals = [g for g in (grades or []) if g is not None]
    if not vals:
        return 0.0
    n = len(vals)
    counts = Counter(vals)
    # abs() so a single-value distribution reports 0.0 rather than -0.0,
    # which reads like a bug in a health report.
    return abs(round(-sum((c / n) * math.log2(c / n) for c in counts.values()), 3))


def agreement(runs):
    """Exact and within-one agreement across repeated grades of one answer.

    `runs` is a list of grades for the SAME answer. Krippendorff's α is the
    right chance-corrected metric and needs a proper implementation; this is the
    cheap test-retest signal to run first, and it is enough to see whether the
    grader is anywhere near usable.
    """
    vals = [g for g in (runs or []) if g is not None]
    if len(vals) < 2:
        return {"n": len(vals), "exact": None, "within_one": None}
    mode = Counter(vals).most_common(1)[0][0]
    exact = sum(1 for v in vals if v == mode) / len(vals)
    within = sum(1 for v in vals if abs(v - mode) <= 1) / len(vals)
    return {"n": len(vals), "mode": mode, "exact": round(exact, 3),
            "within_one": round(within, 3), "spread": max(vals) - min(vals)}


def grader_health(repeated_runs):
    """Verdict over several answers, each graded several times.

    `repeated_runs` is [[g,g,g], [g,g,g], ...] — one inner list per answer.
    Reports both stability AND entropy, because either alone is misleading.
    """
    if not repeated_runs:
        return {"ran": False}
    per = [agreement(r) for r in repeated_runs]
    exact = [p["exact"] for p in per if p["exact"] is not None]
    within = [p["within_one"] for p in per if p["within_one"] is not None]
    flat = [g for r in repeated_runs for g in r if g is not None]
    ent = score_entropy(flat)
    mean_exact = round(sum(exact) / len(exact), 3) if exact else 0.0
    return {
        "ran": True, "answers": len(repeated_runs),
        "mean_exact_agreement": mean_exact,
        "mean_within_one": round(sum(within) / len(within), 3) if within else 0.0,
        "score_entropy": ent,
        # Both must hold. A grader that is stable because it always says the
        # same thing is not a working grader.
        "usable": mean_exact >= 0.60 and ent >= 0.80,
        "note": ("stable but low-entropy — the grader is barely discriminating"
                 if mean_exact >= 0.60 and ent < 0.80 else None),
    }
