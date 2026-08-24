"""When the material looks thin, LOOK HARDER BEFORE SHRINKING THE COURSE.

THE GAP THIS FILLS
------------------
`scope_fit.assess_scope` is a single arithmetic snapshot: chapters found
against concepts requested. It is good arithmetic and it fires at the wrong
moment — once, on whatever the first research sweep happened to return. A
subject whose syllabus is simply harder to find looks identical to a subject
that has none, and the pipeline responded to both the same way: shrink the
course, or warn the learner it is over-stretched.

Meanwhile `research_loop` — an iterative search with a measured exit — ran
ONLY for subjects with no structural sources at all. The case that most needed
a second look, a real subject with thin first-pass evidence, got a single sweep
and a verdict.

So this is the missing middle: a thin verdict ESCALATES the search, and the
verdict is re-taken after each escalation. The course shrinks only once looking
harder has stopped helping.

WHY IT MUST BE BOUNDED, WHICH IS NOT OBVIOUS
--------------------------------------------
The intuition is "keep searching until you have enough". The retrieval
literature is clear that this is wrong: answer quality rises with retrieved
material and then FALLS, because each additional sweep brings back
semantically plausible non-answers — hard negatives — and the reader degrades
in their presence. Retrieving a hundred passages can be worse than retrieving
ten.

So every escalation here is bounded three ways:

  CEILING     a fixed maximum number of tiers, because more is not better
  SATURATION  two consecutive tiers that add nothing new end it — the
              diminishing-returns plateau, and the same rule `research_loop`
              already uses for its own exit
  SUFFICIENCY the moment the arithmetic says "ok", stop. Continuing past that
              spends budget to make the course worse.

TWO WAYS TO STOP, AND THEY MUST NOT BE CONFUSED
-----------------------------------------------
The adaptive-retrieval work frames this as a two-stage termination: enough
evidence to proceed, versus a question that cannot be answered reliably. Both
end the loop and they mean opposite things to a learner:

    SUFFICIENT      we looked harder and found it — build what was asked
    GENUINELY THIN  we looked as hard as is useful and it is not there —
                    say so plainly and offer the shape that fits

The second is the honest answer this project keeps insisting on, and it is only
honest AFTER the escalation. Saying "not enough material" on one sweep is the
absent-vs-zero error: it reports our search effort as a property of the subject.

DEGRADED BRIEFS NEVER ESCALATE AND NEVER WARN
---------------------------------------------
`assess_scope` already refuses to judge a degraded brief, and this inherits
that: if lookups failed or were throttled, thin evidence means WE COULD NOT
LOOK. Escalating would hammer a service that is already rate-limiting us, and
warning would tell the learner their subject is too small when the truth is
that Wikimedia was busy.
"""
import logging
import time

logger = logging.getLogger(__name__)

try:
    from services.core.scope_fit import assess_scope
except ImportError:  # container (flat)
    from scope_fit import assess_scope

#: How many escalations at most — and this is TWO, not three, because of an
#: off-by-one that matters.
#:
#: The iterative-retrieval literature measures ITERATIONS, and reports the
#: optimum at two or three with a FOURTH iteration consistently degrading
#: answer quality across complex datasets — additional cycles introduce noisy
#: or tangentially related material faster than they add signal.
#:
#: The initial `curriculum_brief` sweep IS iteration one. So three escalations
#: would run iteration four: exactly the one shown to make things worse. Two
#: escalations put the ladder at three total iterations, the top of the
#: measured optimum.
#:
#: The same work notes that on simple cases quality degrades with EVERY extra
#: iteration, because the first retrieval was already sufficient. That is why
#: nothing escalates unless the arithmetic first says the material is thin.
MAX_TIERS = 2

#: Consecutive tiers that may add nothing before we call the subject dry. Two
#: rather than one, for the reason `research_loop` gives: one empty round is
#: often a bad query, two is the subject.
#:
#: Systematic-review practice uses the same shape of rule — stop when the last
#: N records yield nothing new — but with N in the tens, and the difference is
#: the UNIT. There a barren step is one screened record; here it is an entire
#: search sweep across a widened set of terms. Two barren sweeps is a far
#: stronger signal than two barren records, and with MAX_TIERS at two it is
#: also the whole ladder — so in practice this fires as "the escalation found
#: nothing" rather than as an early exit.
DRY_TIERS = 2

#: Wall-clock ceiling. Course creation already runs for tens of minutes and a
#: learner is watching a progress bar; an unbounded search is not a feature.
DEFAULT_BUDGET_S = 180.0

#: What each tier widens. Ordered cheapest-and-most-precise first, because the
#: early tiers are the ones most likely to find the real syllabus, and the late
#: ones are the ones most likely to bring back hard negatives.
TIERS = (
    {
        "name": "adjacent",
        "why": "the same subject under the names a syllabus would use",
        "widen": "synonyms and the formal name of the field",
    },
    {
        "name": "parent",
        "why": "the broader field this sits inside",
        "widen": "the parent discipline, whose syllabus usually covers it",
    },
    {
        "name": "applied",
        "why": "where the subject is taught as part of something else",
        "widen": "courses that teach this as a component",
    },
)


def _fingerprint(brief):
    """What counts as 'new material' between tiers.

    Chapters and sources, not bytes: two sweeps that return the same syllabus
    formatted differently have added nothing, and a byte comparison would call
    that progress.
    """
    if not isinstance(brief, dict):
        return (0, 0)
    return (int(brief.get("chapter_count") or 0),
            int(brief.get("structural_sources") or 0))


def deepen_scope(brief, requested_concepts, widen_fn, requested_courses=1,
                 budget_s=DEFAULT_BUDGET_S, max_tiers=MAX_TIERS,
                 status_callback=None, now=time.monotonic):
    """Escalate the search while the material looks thin. Never raises.

    `widen_fn(tier, brief) -> brief | None` performs one escalation and returns
    an updated brief. It owns the actual searching; this owns when to do it and
    when to stop.

    Returns the final assessment with a `deepening` record attached:

        {"tiers_run": [...], "stopped": "<why>", "gained_chapters": N,
         "elapsed_s": F}

    `stopped` is one of:
        "sufficient"   the arithmetic cleared after looking harder
        "saturated"    consecutive tiers added nothing — the subject is dry
        "exhausted"    every tier ran and it is still thin
        "budget"       out of time
        "degraded"     the brief could not be trusted; no escalation, no verdict
        "not_needed"   it was never thin
    """
    started = now()
    record = {"tiers_run": [], "stopped": "not_needed", "gained_chapters": 0,
              "elapsed_s": 0.0}

    assessment = assess_scope(brief, requested_concepts,
                              requested_courses=requested_courses)
    verdict = assessment.get("verdict")

    # Not thin, or not judgeable. Either way there is nothing to escalate.
    if verdict == "ok":
        assessment["deepening"] = record
        return assessment
    if verdict == "unknown" or (isinstance(brief, dict)
                                and brief.get("degraded")):
        # A degraded brief must not trigger a search that would hammer a
        # service already refusing us, and must not produce a verdict about
        # the SUBJECT from a fact about our LOOKUPS.
        record["stopped"] = "degraded"
        assessment["deepening"] = record
        logger.info("[DEEPEN] brief degraded or unjudgeable — not escalating")
        return assessment

    start_chapters = _fingerprint(brief)[0]
    seen = _fingerprint(brief)
    dry = 0

    for tier in TIERS[:max_tiers]:
        if now() - started > budget_s:
            record["stopped"] = "budget"
            break

        if status_callback:
            try:
                status_callback(
                    f"SCOPE:DEEPEN:{tier['name']}:{tier['why']}")
            except Exception:
                pass

        try:
            widened = widen_fn(tier, brief)
        except Exception as e:
            logger.warning(f"[DEEPEN] tier {tier['name']} failed: {e}")
            widened = None

        gained = 0
        if isinstance(widened, dict) and widened:
            if widened.get("degraded"):
                # The escalation itself came back degraded. Keep whatever we
                # already had and stop — see the module docstring.
                record["stopped"] = "degraded"
                break
            fp = _fingerprint(widened)
            gained = max(0, fp[0] - seen[0])
            if fp != seen:
                brief = widened
                seen = fp

        record["tiers_run"].append({"tier": tier["name"], "gained": gained})

        if gained <= 0:
            dry += 1
            if dry >= DRY_TIERS:
                record["stopped"] = "saturated"
                break
            continue
        dry = 0

        assessment = assess_scope(brief, requested_concepts,
                                  requested_courses=requested_courses)
        if assessment.get("verdict") == "ok":
            record["stopped"] = "sufficient"
            break
    else:
        record["stopped"] = "exhausted"

    if record["stopped"] == "not_needed":
        record["stopped"] = "exhausted"
    record["gained_chapters"] = max(0, _fingerprint(brief)[0] - start_chapters)
    record["elapsed_s"] = round(now() - started, 1)
    assessment["deepening"] = record
    assessment["brief"] = brief
    logger.info(f"[DEEPEN] {record['stopped']} after "
                f"{len(record['tiers_run'])} tier(s), "
                f"+{record['gained_chapters']} chapters in "
                f"{record['elapsed_s']}s")
    return assessment


def describe_deepening(assessment):
    """One honest sentence about what the extra searching did.

    Separate from `scope_fit.describe`, which says what the SHAPE should be.
    This says what the SEARCH did — and a learner who is told "not enough
    material" deserves to know whether anyone looked twice.
    """
    d = (assessment or {}).get("deepening") or {}
    stopped = d.get("stopped")
    gained = d.get("gained_chapters", 0)
    tiers = len(d.get("tiers_run") or [])

    if stopped in (None, "not_needed"):
        return ""
    if stopped == "degraded":
        return ("Some sources could not be reached, so the amount of material "
                "here is unknown rather than small — this is not a judgement "
                "about the subject.")
    if stopped == "sufficient":
        return (f"The first search looked thin, so it searched {tiers} more "
                f"way{'s' if tiers != 1 else ''} and found "
                f"{gained} more chapter{'s' if gained != 1 else ''} of "
                f"material. Building at the size you asked for.")
    if stopped == "saturated":
        return (f"Searched {tiers} further way{'s' if tiers != 1 else ''} and "
                f"the last two added nothing new, which usually means the "
                f"published material really is this size.")
    if stopped == "budget":
        return (f"Searched {tiers} further way{'s' if tiers != 1 else ''} "
                f"before the time budget ran out — there may be more material "
                f"that a longer search would find.")
    return (f"Searched every way it knows ({tiers} beyond the first) and found "
            f"{gained} more chapter{'s' if gained != 1 else ''}. What follows "
            f"is sized to the material that actually exists.")
