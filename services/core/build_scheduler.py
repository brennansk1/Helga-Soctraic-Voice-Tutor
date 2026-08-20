"""When should the next course in a programme be built?

THE CONSTRAINT THAT SHAPES EVERYTHING
-------------------------------------
This box serves one model at a time (`OLLAMA_MAX_LOADED_MODELS=1`, 24 GB). A
course build is hundreds of LLM calls; a tutoring turn is one. If a build runs
while someone is mid-session, their turn queues behind it and the learner
experiences the university building itself as latency inside their own lesson.

Measured on this hardware: **90 s per concept to build**, and **~18 s per
tutoring turn**. A single build stage can therefore stall a lesson for longer
than the lesson's own turn takes. That is the failure this module exists to
prevent, and it is the default unless designed against.

So the question is never just *when*, it is *when, at what priority, and yielding
to whom*.

WHY LOOKAHEAD IS 1
------------------
A course takes hours to build (~135 concepts x 90 s ≈ 3.4 h) and weeks to study.
The window is enormous, so there is no reason to run ahead: building two courses
early spends hours on a choice the learner has not made, and a learner who
abandons a programme after two courses has then wasted at most one build rather
than thirty-eight.

WHY THE ELECTIVE PROMPT COMES EARLY
-----------------------------------
A course cannot be built until it is chosen. Asking at 100% leaves no room to
build; asking at ~70% means the choice is made while there is still study time
left to build inside. The registration mechanic and the scheduler meet here, and
asking early is better on both counts.

DEGRADED IS NOT DONE
--------------------
A build that finished while research was unavailable is flagged rather than
silently accepted, or a throttling storm becomes permanent curriculum.
"""

import logging

logger = logging.getLogger(__name__)

# How far into the CURRENT course before asking which course comes next.
#
# A programme is a fixed list of courses and the learner picks the ORDER, one
# course at a time. So this prompt is not "choose an elective" — it is "you
# are nearly through this one; what next?", asked early enough that the answer
# has time to build before they arrive at it.
NEXT_COURSE_PROMPT_AT = 0.70
# ...and the point at which the recommended option is pre-selected so an
# unanswered prompt cannot stall the learner's own pipeline.
NEXT_COURSE_AUTOSELECT_AT = 0.90
# Never have more than this many unbuilt-but-committed courses in flight.
MAX_LOOKAHEAD = 1
# A session this recent counts as active; builds wait.
SESSION_IDLE_SECONDS = 300

PRIORITY_INTERACTIVE = "interactive"
PRIORITY_BACKGROUND = "background"
# Below background: building a course the learner has NOT yet picked, on the
# chance they pick it. Several courses are usually available at once (anything
# whose prerequisites are met), and a learner who decides late would otherwise
# wait ~3.6 h for their choice to build.
PRIORITY_SPECULATIVE = "speculative"

# How many un-picked courses may be pre-built. Each is a full course build, so
# speculatively building everything currently available multiplies the cost of
# the whole programme — with several courses unlocked at once that is many
# extra builds and tens of hours. One is the compromise: the likeliest next
# course gets a head start, the rest wait for the learner to choose.
MAX_SPECULATIVE = 1


def decide(state):
    """What, if anything, should the build system do right now?

    `state` is a plain dict so this is testable without a database:

        progress            0.0-1.0 through the current course
        seconds_since_turn  since the learner's last tutoring turn (None = never)
        next_course_chosen  has the learner picked the next course?
        next_course_built   is it already built?
        builds_in_flight    courses currently building
        build_paused        was a build interrupted and left resumable?

    Returns {action, priority, reason}. `action` is one of:
        "wait", "prompt_next_course", "autoselect_next_course", "resume_build",
        "start_build"
    """
    s = state or {}
    progress = float(s.get("progress") or 0.0)
    idle = s.get("seconds_since_turn")
    active = idle is not None and idle < SESSION_IDLE_SECONDS

    # 1. A live session outranks everything. This is the whole point.
    if active:
        return _r("wait", PRIORITY_INTERACTIVE,
                  f"learner active {int(idle)}s ago — builds yield to sessions")

    # 2. Resume before starting. An interrupted 3-hour build that restarts on
    #    every interruption never finishes for an active learner.
    if s.get("build_paused"):
        return _r("resume_build", PRIORITY_BACKGROUND,
                  "resuming an interrupted build rather than restarting it")

    if s.get("next_course_built"):
        return _r("wait", PRIORITY_BACKGROUND, "next course is already built")

    # 3. The choice gates the build, so chase the choice first.
    if not s.get("next_course_chosen"):
        if progress >= NEXT_COURSE_AUTOSELECT_AT:
            return _r("autoselect_next_course", PRIORITY_INTERACTIVE,
                      f"{int(progress * 100)}% through and still unchosen — "
                      f"pre-selecting the recommended next course so the build has "
                      f"room; the learner can still change it")
        if progress >= NEXT_COURSE_PROMPT_AT:
            return _r("prompt_next_course", PRIORITY_INTERACTIVE,
                      f"{int(progress * 100)}% through — ask which course comes next "
                      f"while there is still study time to build it in")
        return _r("wait", PRIORITY_BACKGROUND,
                  f"{int(progress * 100)}% through — too early to ask")

    # 4. Chosen and unbuilt. Respect the lookahead cap.
    if int(s.get("builds_in_flight") or 0) >= MAX_LOOKAHEAD:
        return _r("wait", PRIORITY_BACKGROUND,
                  f"{s.get('builds_in_flight')} build(s) already in flight "
                  f"(lookahead cap {MAX_LOOKAHEAD})")

    return _r("start_build", PRIORITY_BACKGROUND,
              "next course is chosen, unbuilt, and the learner is idle")


def decide_speculative(state):
    """Should an UNCHOSEN option be pre-built? Only with capacity to spare.

    Real registration offers several courses per elective slot, and a learner who
    picks at the last moment waits for the build. Pre-building every option makes
    the choice instant and costs N times as much: 3 options across a bachelor's 9
    elective slots is 18 extra builds, roughly 65 hours.

    So this is strictly subordinate. It runs only when the committed pipeline has
    nothing to do, it never delays a chosen course, and a speculative build is
    abandoned the moment real work appears — a half-built option is worth less
    than a chosen course started on time.
    """
    s = state or {}
    primary = decide(s)

    # Anything the committed pipeline wants to do outranks speculation. That
    # includes waiting: if it is waiting because a learner is active, the machine
    # is not idle in the sense that matters.
    if primary["action"] != "wait" or primary["priority"] == PRIORITY_INTERACTIVE:
        return _r("wait", primary["priority"],
                  f"committed pipeline is busy ({primary['action']}) — "
                  f"speculation yields")

    options = [o for o in (s.get("open_options") or []) if not o.get("built")]
    if not options:
        return _r("wait", PRIORITY_SPECULATIVE, "no unbuilt options open")
    if int(s.get("speculative_in_flight") or 0) >= MAX_SPECULATIVE:
        return _r("wait", PRIORITY_SPECULATIVE,
                  f"already speculating on {s.get('speculative_in_flight')} "
                  f"(cap {MAX_SPECULATIVE})")

    # Prefer the option the learner is most likely to take. Without a signal,
    # the first offered — which is the recommended one.
    pick = max(options, key=lambda o: float(o.get("likelihood") or 0))
    return {"action": "start_build", "priority": PRIORITY_SPECULATIVE,
            "course": pick.get("title"),
            "reason": ("pipeline idle and this option is open — pre-building it "
                       "so a late choice does not mean a wait; abandoned if the "
                       "learner chooses otherwise or real work appears")}


def _r(action, priority, reason):
    return {"action": action, "priority": priority, "reason": reason}


def on_course_passed(state):
    """Course completion is the strongest trigger: an event, not an estimate.

    A pace projection guesses when the learner will finish; passing the gate is
    the fact itself. Both are used — the projection drives the elective PROMPT
    early, and this drives the BUILD.
    """
    s = dict(state or {})
    s["progress"] = 1.0
    decision = decide(s)
    if decision["action"] == "start_build":
        logger.info("[SCHEDULE] course passed — starting the next build")
    return decision


def describe_unbuilt(course_title, degraded=False, failures=0):
    """What a learner sees on reaching a course that is not ready.

    Never a blank page and never a raw error. A build that completed DEGRADED is
    surfaced too — silently accepting it would let a throttling storm become
    permanent curriculum.
    """
    if failures >= 3:
        return (f"“{course_title}” could not be prepared after several "
                f"attempts. This needs attention rather than another retry.")
    if degraded:
        return (f"“{course_title}” was prepared while its reference "
                f"sources were unavailable, so its coverage may be thinner than "
                f"usual. It can be rebuilt when they are reachable again.")
    return (f"“{course_title}” is still being prepared. It builds in "
            f"the background between your sessions, so it will be ready shortly "
            f"— nothing is lost by carrying on with review in the meantime.")
