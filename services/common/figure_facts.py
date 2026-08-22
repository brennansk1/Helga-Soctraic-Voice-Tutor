"""Hold the tutor to the figure it drew.

THE FAILURE THIS EXISTS FOR
---------------------------
Measured 2026-08-21, mathematics, `misconception_holder` on partial
derivatives. The tutor opened by emitting this aid:

    Peak    (0, 0)  z=10
    Point A (2, 0)  z=6
    Point B (0, 2)  z=6

On turn 4 it said, correctly, "the temperature drops because you're moving
away from the peak at the origin". On turn 6 it said:

    "You moved from x=0 toward x=2, getting CLOSER to the peak."

Moving from x=0 toward x=2 moves AWAY from a peak at x=0. The tutor
contradicted its own figure AND its own previous turn, and the judge scored the
dialogue 1/5 on accuracy — one of three catastrophic failures dragging the
domain mean from ~4.2 to 3.37, i.e. from passing the blocking gate to failing
it.

WHY THIS AND NOT SELF-CONSISTENCY SAMPLING
------------------------------------------
The factuality literature's standard answers — sample N responses and take the
most consistent, or draft/verify/finalise in multiple passes — all cost extra
LLM calls. On this machine a call costs 8-40s and turn latency is already the
acute defect, so paying 2-3x per turn to catch an error is the wrong trade.

It is also unnecessary here. The figure is STRUCTURED DATA the tutor itself
emitted. Nothing needs to be inferred or re-derived: the coordinates are in the
transcript as JSON. The tutor's problem is not that it lacks the facts, it is
that by turn 6 the figure is buried in prose several turns back and it is
re-reading rather than being told. This is the same diagnosis as A.2 (turn
state) and the same remedy: state it, do not make the model re-derive it.

WHAT IT REFUSES TO SAY
----------------------
Only aids carrying explicit labelled data produce a block. A `geometry` aid
with no coordinates, or a diagram whose points are unlabelled, renders nothing
rather than an empty scaffold — the discipline used by turn_state and
learner_history. An invented fact is worse than a missing one.
"""
import json
import re

#: Bounded because this rides in every tutor turn after a figure is drawn.
MAX_ITEMS = 8
MAX_CHARS = 420

_AID_BLOCK = re.compile(r"```aid\s*(\{.*?\})\s*```", re.S)

#: Keys under which aid kinds carry their labelled data points.
_DATA_KEYS = ("points", "nodes", "series", "data", "bars", "cells")


def _aids_in(text):
    """Every parseable aid object in one message."""
    out = []
    for m in _AID_BLOCK.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                out.append(obj)
        except (ValueError, TypeError):
            continue          # a malformed aid is not a fact
    return out


def _describe_point(p):
    """One labelled datum as a short clause, or None."""
    if not isinstance(p, dict):
        return None
    label = p.get("label") or p.get("name") or p.get("id")
    if not label:
        return None
    coords = [f"{k}={p[k]}" for k in ("x", "y", "z", "value")
              if k in p and isinstance(p[k], (int, float))]
    if not coords:
        return None
    return f"{label} at {', '.join(coords)}"


def facts_from(transcript):
    """The FACTS block for the tutor prompt, or "".

    `transcript` is the dialogue so far. Only the tutor's own aids are read —
    a figure the student described is not something the tutor committed to.
    Never raises: a bookkeeping failure must not cost a turn.
    """
    try:
        items, title = [], None
        for turn in (transcript or []):
            if turn.get("role") != "tutor":
                continue
            for aid in _aids_in(turn.get("text", "")):
                for key in _DATA_KEYS:
                    seq = aid.get(key)
                    if not isinstance(seq, list):
                        continue
                    for p in seq:
                        d = _describe_point(p)
                        if d and d not in items:
                            items.append(d)
                            title = title or aid.get("title")
        if not items:
            return ""
        shown = items[:MAX_ITEMS]
        head = (f'FACTS FROM THE FIGURE YOU DREW'
                f'{" (" + str(title) + ")" if title else ""} — '
                f'these are YOUR OWN stated values:')
        body = "; ".join(shown)
        tail = ("Every claim you make about this figure must agree with those "
                "values. If you say something moves closer to or further from "
                "a point, check the numbers above before you say it.")
        return f"{head}\n  {body}\n  {tail}"[:MAX_CHARS]
    except Exception:            # pragma: no cover - defensive
        return ""
