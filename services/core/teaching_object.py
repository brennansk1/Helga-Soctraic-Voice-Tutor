"""The teaching object — a concept as structure rather than prose.

WHY
---
The only consumer of a concept file is a model, at question-generation and
grading time; no human ever reads it. So prose is the wrong PRIMARY
representation, and the research on this pipeline said so directly: store the
material as addressable structure and let the prose be the fallback.

Three concrete costs of prose-first, all of them already paid here:

  * the FSM regex-extracts `## Socratic Hooks` and `## Edge Cases` out of the
    file at session time, so the file is *already* being used as a
    section-addressed store queried by a parser — just a fragile one
  * redundancy detection needs claims, and had to re-derive them from Markdown
  * a verifier needs claims paired with the passage that supports them, which
    prose cannot express at all

WHAT THIS IS NOT
----------------
Not a migration of where content lives. The Markdown stays exactly where it is
and stays canonical; this is a parsed view stored alongside it. Moving the store
is a separate change with its own risk, and the value here — addressability —
does not require it.

DETERMINISTIC, NO MODEL
-----------------------
Every field is parsed from the section template the generator already fills.
Nothing here calls an LLM, so building a teaching object cannot fail a build,
cannot drift between runs, and costs nothing.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

_BLOOM_BANDS = (("1-2", (1, 2)), ("3-4", (3, 4)), ("5-6", (5, 6)))


def _section(md, header):
    m = re.search(rf"##\s*{re.escape(header)}\s*\n(.*?)(?=\n##\s|\Z)",
                  md or "", re.DOTALL)
    return m.group(1).strip() if m else ""


# A bullet marker, WITHOUT eating the ** of bold.
#
# `[-*•]` matches the first asterisk of `**Correction**`, leaving `*Correction**`
# and silently dropping every correction. That exact bug has now been found
# twice in this codebase — once in the ledger's claim extractor and once here —
# so the pattern is written once and shared.
_BULLET = re.compile(r"^\s*(?:[-•]|\*(?!\*))\s*")


def _debullet(line):
    return _BULLET.sub("", line or "").strip()


def _bullets(text):
    out = []
    for line in (text or "").splitlines():
        line = _debullet(line)
        if len(line) > 3:
            out.append(line)
    return out


def worked_steps(md):
    """The worked example as ordered steps.

    The template demands ONE example carried through to a result, with explicit
    steps — "naming a field where the idea applies is NOT a worked example and
    will be rejected". So the steps are recoverable, and having them as a list
    is what lets a tutor walk a learner through one rather than quoting a
    paragraph at them.
    """
    body = _section(md, "Real-World Examples")
    if not body:
        return []
    steps = re.findall(r"(Step\s*\d+[:.].*?)(?=Step\s*\d+[:.]|\Z)", body,
                       re.DOTALL | re.IGNORECASE)
    if steps:
        return [" ".join(s.split())[:300] for s in steps]
    # No explicit numbering: fall back to sentences, which is weaker but keeps
    # the field populated rather than silently empty.
    return [s.strip()[:300] for s in re.split(r"(?<=[.!?])\s+", body)
            if len(s.strip()) > 20][:6]


def misconceptions(md):
    """Belief/correction pairs — already the right shape for question writing."""
    body = _section(md, "Misconceptions")
    out, belief = [], None
    for line in body.splitlines():
        line = _debullet(line)
        b = re.match(r"\*\*Belief\*\*:?\s*(.+)", line, re.I)
        c = re.match(r"\*\*Correction\*\*:?\s*(.+)", line, re.I)
        if b:
            belief = b.group(1).strip()
        elif c and belief:
            out.append({"belief": belief[:300], "correction": c.group(1).strip()[:300]})
            belief = None
    return out


def question_seeds(md):
    """Socratic hooks, keyed by the Bloom band they serve.

    Stored per band rather than as a flat list because Bloom is the difficulty
    controller and moves WITHIN a session — two grades >=3 advance a level, a
    grade <=1 drops one — so the tutor needs to reach for the right band, not
    the next item in a list.
    """
    body = _section(md, "Socratic Hooks")
    seeds = {}
    for line in body.splitlines():
        line = _debullet(line)
        m = re.match(r"Bloom\s*(\d)\s*[-–]\s*(\d)\s*:?\s*(.+)", line, re.I)
        if m and len(m.group(3)) > 10:
            seeds[f"{m.group(1)}-{m.group(2)}"] = m.group(3).strip()[:300]
    return seeds


def mastery_threshold(md):
    """The one sentence describing what a grade-3 answer must show."""
    body = _section(md, "Mastery Criteria")
    m = re.search(r"Grade\s*3\s*requires:?\s*(.+)", body, re.I)
    return m.group(1).strip()[:300] if m else ""


def build(md, concept_uid=None, title="", bloom_level=None, prerequisites=None):
    """Parse a hydrated concept into its teaching object.

    Never raises: a malformed file yields a sparse object, which is strictly
    better than failing a build over a parse.
    """
    try:
        from services.core.taught_ledger import extract_claims
    except ImportError:
        try:
            from taught_ledger import extract_claims
        except ImportError:
            extract_claims = lambda _md: []  # noqa: E731

    try:
        obj = {
            "concept_uid": concept_uid,
            "title": title,
            "bloom_level": bloom_level,
            "claims": extract_claims(md),
            "worked_steps": worked_steps(md),
            "misconceptions": misconceptions(md),
            "question_seeds": question_seeds(md),
            "mastery_threshold": mastery_threshold(md),
            "prerequisites": list(prerequisites or []),
            "edge_cases": _bullets(_section(md, "Edge Cases & Limitations")),
            # The prose is DEMOTED, not discarded: it is the LECTURE payload
            # when a learner is lost, and a human-auditable artifact.
            "prose_fallback": _section(md, "Core Explanation")[:2000],
        }
    except Exception as e:
        logger.debug(f"[TEACHING_OBJECT] parse failed for {title!r}: {e}")
        return {"concept_uid": concept_uid, "title": title, "claims": [],
                "worked_steps": [], "misconceptions": [], "question_seeds": {},
                "mastery_threshold": "", "prerequisites": [], "edge_cases": [],
                "prose_fallback": ""}
    return obj


def completeness(obj):
    """Which fields a concept actually filled.

    A hollow concept is structurally complete and substantively empty, so
    "passed the section template" says nothing. This counts what is THERE.
    """
    if not obj:
        return {"score": 0.0, "missing": ["everything"]}
    fields = {
        "claims": bool(obj.get("claims")),
        "worked_steps": bool(obj.get("worked_steps")),
        "misconceptions": bool(obj.get("misconceptions")),
        "question_seeds": len(obj.get("question_seeds") or {}) >= 2,
        "mastery_threshold": bool(obj.get("mastery_threshold")),
        "edge_cases": bool(obj.get("edge_cases")),
    }
    present = sum(1 for v in fields.values() if v)
    return {"score": round(present / len(fields), 2),
            "present": present, "of": len(fields),
            "missing": sorted(k for k, v in fields.items() if not v)}


def to_json(obj):
    return json.dumps(obj, ensure_ascii=False)
