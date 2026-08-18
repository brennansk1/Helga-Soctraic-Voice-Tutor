#!/usr/bin/env python3
"""session_clock.py — how long is a Socratic session on one concept?

WHY THIS NUMBER GATES A DESIGN DECISION
---------------------------------------
`docs/AI_UNIVERSITY_DESIGN.md` maps **one lesson to one class session** (50
minutes) and derives everything above it from that: 45 lessons per semester
course, 3 concepts per lesson, ~135 concepts per course. Every number in that
ladder is arithmetic except this one, and this one has never been measured.

The measurement decides which of two OPPOSITE problems we have:

  * a concept-session measures ~17 min  -> the ladder is right; only the concept
    COUNT needed raising, which is done
  * a concept-session measures ~5 min   -> concepts are too THIN, and the fix is
    depth per concept, not more of them

Guessing between two opposite fixes is worse than waiting for the number, which
is why the design forbids any hour-equivalence claim in the UI until this runs.

WHAT IT MEASURES, AND WHAT IT CANNOT
------------------------------------
Two clocks, and only one of them is portable:

  * **learner turns per concept** — how many exchanges before the concept
    completes. Hardware-independent, so it is the number that transfers to
    another machine and the one to design against.
  * **wall-clock** — dominated by local LLM latency on this box. Useful for
    build planning here, meaningless as a claim about how long a *human* takes.

Neither includes the human's reading and thinking time, which no offline harness
can observe. So this establishes a FLOOR on session length, never the true
figure. Reported as such.

A simulated learner is not a real one. Three personas are driven — a quick
learner, a median one, and one holding a misconception — because a single
scripted respondent measures the script rather than the tutor, and the design
target is the median learner rather than the fastest path.

USAGE
    python3 tools/session_clock.py --course <uid> --concepts 5 --personas 3
"""

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "../services/core")))

# Scripted learners. Deliberately NOT model-generated: a model playing a student
# converges on the tutor's own phrasing, which shortens sessions in a way that
# flatters the measurement.
PERSONAS = {
    "quick": [
        "I think it's because the two sides squared add to the third squared.",
        "So it only works for right triangles.",
        "Yes — because the angle has to be 90 degrees for the areas to match.",
        "Got it.",
    ],
    "median": [
        "I'm not sure. Something about triangles?",
        "Maybe a squared plus b squared?",
        "Oh, equals c squared. And c is the long side.",
        "Because it's opposite the right angle?",
        "I think I follow now.",
        "Yes.",
    ],
    "misconception": [
        "It works for any triangle, right?",
        "But I've used it on other triangles before.",
        "Hmm. So if the angle isn't 90 it just doesn't hold?",
        "I see — I was remembering the law of cosines.",
        "OK, so the theorem is the special case where the angle is right.",
        "That makes sense.",
    ],
}


# Once a persona's scripted replies are exhausted the session must keep going or
# the measurement is a floor rather than a count. The first valid run stopped at
# exactly the script length (median 6, max 6) with completed=0 -- it measured the
# script, not the tutor. These continuations carry no new information, so they
# cannot shorten a session artificially; they only stop it ending early.
_CONTINUATIONS = [
    "I think so.", "Can you say more?", "Right.", "I'm not sure.",
    "That makes sense.", "Okay.", "Go on.", "I see.",
]


def run_session(fsm, concept_uid, replies, max_turns=25):
    """Drive one concept to completion. Returns per-session measures."""
    t0 = time.time()
    turns, latencies = 0, []

    fsm.transition({"type": "NAVIGATE_TO_TOPIC",
                    "payload": {"topic_id": concept_uid}})
    start_state = getattr(fsm, "state", None)

    script = list(replies) + [_CONTINUATIONS[i % len(_CONTINUATIONS)]
                              for i in range(max_turns)]
    for reply in script[:max_turns]:
        if getattr(fsm, "state", None) != "SOCRATIC_LEARNING":
            break
        t1 = time.time()
        fsm.transition({"type": "TEXT_INPUT", "payload": {"text": reply}})
        latencies.append(round(time.time() - t1, 1))
        turns += 1

    return {
        "concept_uid": concept_uid,
        "turns": turns,
        "wall_seconds": round(time.time() - t0, 1),
        "turn_latencies": latencies,
        "median_turn_latency": (round(statistics.median(latencies), 1)
                                if latencies else None),
        "completed": getattr(fsm, "state", None) != "SOCRATIC_LEARNING",
        "hit_cap": turns >= max_turns,
        "scripted_turns": len(replies),
        "start_state": start_state,
    }


def summarise(sessions):
    """Median and SPREAD. A median with unstated spread is the same false
    precision this project has already been burned by in its judges."""
    turns = [s["turns"] for s in sessions if s["turns"]]
    walls = [s["wall_seconds"] for s in sessions if s["turns"]]
    if not turns:
        return {"error": "no sessions produced turns"}

    def stats(v):
        return {"median": round(statistics.median(v), 1),
                "min": min(v), "max": max(v), "n": len(v)}

    out = {"turns": stats(turns), "wall_seconds": stats(walls),
           "sessions": len(sessions),
           "completed": sum(1 for s in sessions if s["completed"])}
    med = out["turns"]["median"]
    out["implied_concepts_per_50min_lesson"] = None
    out["note"] = (
        "wall-clock is local LLM latency, not human session length; turns is the "
        "portable measure. Neither includes the learner's reading and thinking "
        "time, so both are FLOORS.")
    if med:
        out["reading"] = (
            f"median {med} learner turns per concept. Design assumes 3 concepts "
            f"per 50-minute lesson, i.e. ~17 min and roughly {med} exchanges "
            f"each — plausible if a turn plus reading is ~2-3 minutes of a "
            f"learner's time.")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", required=True, help="course uid")
    p.add_argument("--data-root", default=os.getenv("DATA_ROOT", "data"))
    p.add_argument("--concepts", type=int, default=5)
    p.add_argument("--personas", type=int, default=3)
    p.add_argument("--out")
    a = p.parse_args()

    os.environ.setdefault("DATA_ROOT", a.data_root)
    from services.common.storage import StorageManager
    try:
        from fsm_logic import MnemosyneFSM
    except ImportError:
        from services.core.fsm_logic import MnemosyneFSM

    storage = StorageManager(a.data_root)
    course = storage.courses.get_course(a.course)
    if not course:
        print(f"course {a.course} not found under {a.data_root}")
        return 1

    concept_uids = [c["uid"] for m in course.get("modules", [])
                    for u in m.get("units", [])
                    for l in u.get("lessons", [])
                    for c in l.get("concepts", []) if c.get("uid")]
    if not concept_uids:
        print("course has no concepts")
        return 1

    names = list(PERSONAS)[:max(1, a.personas)]
    sessions = []
    for i, cuid in enumerate(concept_uids[:a.concepts]):
        for name in names:
            fsm = MnemosyneFSM(student_id="stu_clock0", storage=storage)
            fsm.active_course_uid = a.course
            try:
                s = run_session(fsm, cuid, PERSONAS[name])
            except Exception as e:
                print(f"  session failed ({cuid}, {name}): {e}")
                continue
            s["persona"] = name
            sessions.append(s)
            print(f"  {cuid[:16]} {name:14s} turns={s['turns']:2d} "
                  f"wall={s['wall_seconds']:6.1f}s completed={s['completed']}")

    result = {"course": a.course, "sessions": sessions,
              "summary": summarise(sessions)}
    print("\n=== SUMMARY ===")
    print(json.dumps(result["summary"], indent=2))
    if a.out:
        json.dump(result, open(a.out, "w"), indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
