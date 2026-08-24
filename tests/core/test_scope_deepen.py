"""Look harder before shrinking the course — and know when to stop looking.

`assess_scope` was a single snapshot taken on whatever the first sweep
returned, so a subject whose syllabus is merely hard to find was
indistinguishable from one that has none. Meanwhile the iterative search ran
only for subjects with NO sources at all — the case that most needed a second
look got a single sweep and a verdict.
"""
import pytest

from services.core.scope_deepen import (
    deepen_scope, describe_deepening, TIERS, DRY_TIERS, MAX_TIERS,
)


def _brief(chapters, sources=2, degraded=False):
    return {"chapter_count": chapters, "structural_sources": sources,
            "degraded": degraded}


def _clock():
    """Deterministic monotonic clock — no sleeping in tests."""
    t = {"n": 0.0}

    def now():
        t["n"] += 1.0
        return t["n"]
    return now


# --- when NOT to escalate ----------------------------------------------------

def test_sufficient_material_is_not_escalated():
    """Continuing past sufficiency spends budget to make the course worse."""
    calls = []
    a = deepen_scope(_brief(40), 60, lambda t, b: calls.append(t) or b,
                     now=_clock())
    assert a["verdict"] == "ok"
    assert a["deepening"]["stopped"] == "not_needed"
    assert calls == [], "escalated a subject that was already sufficient"


def test_a_degraded_brief_never_escalates():
    """Thin evidence from a degraded brief means WE COULD NOT LOOK.

    Escalating would hammer a service already refusing us, and any verdict
    would report our search effort as a property of the subject.
    """
    calls = []
    a = deepen_scope(_brief(1, degraded=True), 200,
                     lambda t, b: calls.append(t) or b, now=_clock())
    assert a["deepening"]["stopped"] == "degraded"
    assert calls == []
    assert a["verdict"] == "unknown"


def test_a_tier_that_returns_a_degraded_brief_stops_the_ladder():
    def widen(tier, brief):
        return _brief(99, degraded=True)

    a = deepen_scope(_brief(2), 200, widen, now=_clock())
    assert a["deepening"]["stopped"] == "degraded"


# --- when it works -----------------------------------------------------------

def test_escalation_that_finds_material_builds_at_full_size():
    """The whole point: thin first sweep, real subject, look again."""
    def widen(tier, brief):
        return _brief(40)          # the syllabus was findable after all

    a = deepen_scope(_brief(2), 60, widen, now=_clock())
    assert a["verdict"] == "ok"
    assert a["deepening"]["stopped"] == "sufficient"
    assert a["deepening"]["gained_chapters"] == 38


def test_it_stops_the_moment_it_is_sufficient():
    """Not "run all tiers" — stop when the arithmetic clears."""
    seen = []

    def widen(tier, brief):
        seen.append(tier["name"])
        return _brief(40)

    deepen_scope(_brief(2), 60, widen, now=_clock())
    assert len(seen) == 1, f"kept searching after sufficiency: {seen}"


# --- when to give up ---------------------------------------------------------

def test_two_barren_tiers_end_it():
    """Diminishing returns. One empty round is often a bad query; two is the
    subject."""
    seen = []

    def widen(tier, brief):
        seen.append(tier["name"])
        return brief               # nothing new, ever

    a = deepen_scope(_brief(2), 200, widen, now=_clock())
    assert a["deepening"]["stopped"] == "saturated"
    assert len(seen) == DRY_TIERS


def test_the_ladder_has_a_ceiling():
    """More retrieval is not monotonically better: answer quality rises and
    then FALLS as hard negatives accumulate."""
    seen = []

    def widen(tier, brief):
        seen.append(tier["name"])
        # Gains every tier, but never enough — the ceiling must still bite.
        return _brief(brief["chapter_count"] + 1)

    a = deepen_scope(_brief(2), 10_000, widen, now=_clock())
    assert len(seen) <= MAX_TIERS
    assert a["deepening"]["stopped"] == "exhausted"


def test_the_time_budget_bites():
    def slow_clock():
        t = {"n": 0.0}

        def now():
            t["n"] += 100.0
            return t["n"]
        return now

    seen = []

    def widen(tier, brief):
        seen.append(tier["name"])
        return _brief(brief["chapter_count"] + 1)

    a = deepen_scope(_brief(2), 10_000, widen, budget_s=150.0, now=slow_clock())
    assert a["deepening"]["stopped"] == "budget"
    assert len(seen) < MAX_TIERS


def test_a_thin_subject_still_ends_thin_but_only_after_looking():
    """The honest answer, and it is only honest AFTER the escalation."""
    a = deepen_scope(_brief(2), 200, lambda t, b: b, now=_clock())
    assert a["verdict"] in ("stretched", "unsupported")
    assert a["deepening"]["tiers_run"], "declared a subject thin without looking"


# --- robustness --------------------------------------------------------------

def test_a_failing_widen_does_not_break_the_build():
    def boom(tier, brief):
        raise RuntimeError("search backend down")

    a = deepen_scope(_brief(2), 200, boom, now=_clock())
    assert a["verdict"] in ("stretched", "unsupported")
    assert a["deepening"]["stopped"] in ("saturated", "exhausted")


def test_widen_returning_nothing_is_treated_as_a_barren_tier():
    a = deepen_scope(_brief(2), 200, lambda t, b: None, now=_clock())
    assert a["deepening"]["stopped"] == "saturated"


def test_status_callback_names_each_tier():
    """The learner should see WHY it is still searching, not a frozen bar."""
    events = []
    deepen_scope(_brief(2), 200, lambda t, b: b,
                 status_callback=events.append, now=_clock())
    assert events, "no progress reported during deepening"
    assert all(e.startswith("SCOPE:DEEPEN:") for e in events)
    assert any(TIERS[0]["name"] in e for e in events)


def test_a_broken_status_callback_cannot_stop_the_search():
    def bad(_):
        raise RuntimeError("ui gone")

    a = deepen_scope(_brief(2), 200, lambda t, b: b, status_callback=bad,
                     now=_clock())
    assert a["deepening"]["tiers_run"]


# --- what the learner is told ------------------------------------------------

def test_the_message_distinguishes_found_from_absent():
    found = deepen_scope(_brief(2), 60, lambda t, b: _brief(40), now=_clock())
    dry = deepen_scope(_brief(2), 200, lambda t, b: b, now=_clock())
    assert "found" in describe_deepening(found)
    assert "added nothing new" in describe_deepening(dry)
    assert describe_deepening(found) != describe_deepening(dry)


def test_degraded_is_described_as_unknown_not_small():
    msg = describe_deepening(
        deepen_scope(_brief(1, degraded=True), 200, lambda t, b: b,
                     now=_clock()))
    assert "unknown rather than small" in msg
    assert "not a judgement about the subject" in msg


def test_nothing_is_said_when_nothing_happened():
    a = deepen_scope(_brief(40), 60, lambda t, b: b, now=_clock())
    assert describe_deepening(a) == ""


# --- the ceiling, and the off-by-one that set it -----------------------------

def test_the_ladder_never_reaches_the_degrading_iteration():
    """The initial sweep IS iteration one.

    The iterative-retrieval literature puts the optimum at two or three
    iterations and reports a FOURTH consistently degrading answer quality.
    Three escalations would BE iteration four. This pins the arithmetic so a
    later "let's search a bit harder" cannot quietly reintroduce it.
    """
    assert MAX_TIERS == 2, (
        "MAX_TIERS counts ESCALATIONS, not iterations: the initial "
        "curriculum_brief sweep is iteration 1, so 2 escalations = 3 total, "
        "the top of the measured optimum. 3 would run the degrading fourth.")


def test_only_the_two_most_precise_tiers_run():
    """Ordered cheapest-and-most-precise first, so the ceiling drops the tier
    most likely to return plausible non-answers."""
    ran = []

    def widen(tier, brief):
        ran.append(tier["name"])
        return _brief(brief["chapter_count"] + 1)

    deepen_scope(_brief(2), 10_000, widen, now=_clock())
    assert ran == ["adjacent", "parent"], ran
    assert "applied" not in ran, (
        "the widest tier ran; it is defined but must sit outside the default "
        "ceiling")


def test_a_caller_can_still_reach_the_third_tier_deliberately():
    """Defined-but-not-default is different from deleted. A caller that knows
    it wants the widest sweep can ask for it."""
    ran = []

    def widen(tier, brief):
        ran.append(tier["name"])
        return _brief(brief["chapter_count"] + 1)

    deepen_scope(_brief(2), 10_000, widen, max_tiers=3, now=_clock())
    assert "applied" in ran
