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

# Fraction of the current course completed before the next elective is offered.
ELECTIVE_PROMPT_AT = 0.70
# ...and the point at which the recommended option is pre-selected so an
# unanswered prompt cannot stall the learner's own pipeline.
ELECTIVE_AUTOSELECT_AT = 0.90
# Never have more than this many unbuilt-but-committed courses in flight.
MAX_LOOKAHEAD = 1
# A session this recent counts as active; builds wait.
SESSION_IDLE_SECONDS = 300

PRIORITY_INTERACTIVE = "interactive"
PRIORITY_BACKGROUND = "background"


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
        "wait", "prompt_elective", "autoselect_elective", "resume_build",
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
        if progress >= ELECTIVE_AUTOSELECT_AT:
            return _r("autoselect_elective", PRIORITY_INTERACTIVE,
                      f"{int(progress * 100)}% through and still unchosen — "
                      f"pre-selecting the recommended option so the build has "
                      f"room; the learner can still change it")
        if progress >= ELECTIVE_PROMPT_AT:
            return _r("prompt_elective", PRIORITY_INTERACTIVE,
                      f"{int(progress * 100)}% through — ask now so there is "
                      f"study time left to build inside")
        return _r("wait", PRIORITY_BACKGROUND,
                  f"{int(progress * 100)}% through — too early to ask")

    # 4. Chosen and unbuilt. Respect the lookahead cap.
    if int(s.get("builds_in_flight") or 0) >= MAX_LOOKAHEAD:
        return _r("wait", PRIORITY_BACKGROUND,
                  f"{s.get('builds_in_flight')} build(s) already in flight "
                  f"(lookahead cap {MAX_LOOKAHEAD})")

    return _r("start_build", PRIORITY_BACKGROUND,
              "next course is chosen, unbuilt, and the learner is idle")


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
