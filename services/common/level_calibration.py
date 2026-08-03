"""Does the content read at the level it CLAIMS? (quality gate criterion 2)

The depth contract checks that rigor MARKERS are present; the fact-checker
checks that claims are TRUE. Neither asks the question a learner cares about:
is this actually pitched at the level it was sold as?

Method: strip everything that states the level outright (the Metadata block
names the Bloom target and depth; Mastery Criteria restates it), then ask a
judge what level the writing sits at. Comparing that against the claimed
mastery gives a calibration gap.

This is the library form of `tools/level_audit.py` so hydration can record a
verdict at generation time instead of it being an after-the-fact manual step.

IMPORTANT — the material is deliberately delivered as SHORT micro-lessons
("the Duolingo of a college course"). The judge is told so explicitly: brevity
must not be read as shallowness. It is still told that sophisticated
terminology is not sophistication.
"""

import logging
import os
import re
import statistics

logger = logging.getLogger(__name__)

# A gap wider than this means the course is not at the level it claims.
MAX_GAP = float(os.getenv("HELGA_MAX_LEVEL_GAP", "1.0"))

_SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "integer", "minimum": 1, "maximum": 5},
        "reason": {"type": "string"},
    },
    "required": ["level"],
}

_JUDGE = """You classify the academic level of teaching material. Judge the
SUBSTANCE, not the formatting — headings, bullets and citation lists tell you
nothing about level.

IMPORTANT: this material is deliberately delivered in SHORT, self-contained
micro-lessons, like a language-learning app's take on a university subject. A
lesson is MEANT to be small. Do NOT lower the level for brevity, or for lacking
lecture-style exposition. Judge how sophisticated the thinking is and what a
reader must already know.

1 = popular science / general audience
2 = high school
3 = undergraduate
4 = advanced undergraduate or masters
5 = graduate / research

Material that only DESCRIBES a topic — no derivation, no non-trivial worked
example, no engagement with when the method fails — is level 3 at most, however
advanced its vocabulary. Sophisticated terminology is not sophistication.

Return STRICT JSON: {"level": n, "reason": "<one sentence>"}"""


def _strip_level_hints(body):
    """Remove blocks that state the level, so the judge cannot read it off."""
    for sec in ("Metadata", "Mastery Criteria", "Sources"):
        body = re.sub(rf"^## {sec}\n.*?(?=\n## |\Z)", "", body,
                      flags=re.DOTALL | re.MULTILINE)
    return re.sub(r"(?i)bloom[^\n]*", "", body).strip()


def _judge_level(text):
    from services.common.fact_check import _post, _field
    d = _post([{"role": "system", "content": _JUDGE},
               {"role": "user", "content": text[:6000]}],
              schema=_SCHEMA, max_tokens=300)
    if not d:
        return None
    # Alias-tolerant: the /v1 shim does not enforce schema key names.
    raw = _field(d, "level", "rating", "score", "grade")
    try:
        lvl = int(raw)
    except (TypeError, ValueError):
        return None
    return lvl if 1 <= lvl <= 5 else None


def calibrate(bodies, claimed_mastery, sample=6):
    """Judge a sample of concept bodies against the claimed mastery level.

    `bodies` is an iterable of markdown strings. Returns a verdict dict, or
    None if the judge was unavailable — an unusable judge must not be reported
    as a pass.
    """
    bodies = [b for b in bodies if b and b.strip()]
    if not bodies or not claimed_mastery:
        return None
    # Spread the sample through the course rather than only reading the intro,
    # which is systematically easier than later material.
    step = max(1, len(bodies) // max(1, sample))
    picked = bodies[::step][:sample]

    levels = []
    for b in picked:
        lvl = _judge_level(_strip_level_hints(b))
        if lvl:
            levels.append(lvl)
    if not levels:
        return None

    judged = round(statistics.mean(levels), 2)
    gap = round(judged - float(claimed_mastery), 2)
    return {
        "claimed": claimed_mastery,
        "judged": judged,
        "sd": round(statistics.pstdev(levels), 2) if len(levels) > 1 else 0.0,
        "n": len(levels),
        "gap": gap,
        # Too far BELOW is the reported problem (a course sold as college-level
        # that isn't). Too far above is also a failure: it is inaccessible to
        # the learner it was built for.
        "calibrated": abs(gap) <= MAX_GAP,
        "levels": levels,
    }
