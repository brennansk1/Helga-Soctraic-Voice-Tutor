"""Queue policy: the part that decides whether a review habit survives a year."""
from datetime import date, timedelta

import pytest

from services.common.review_scheduler import (
    Due, LEECH_LAPSES, balance_due_date, build_queue, forecast, fuzz_window,
    interleave, is_leech, is_retired, overdue_ratio, priority, select_new, target_mix,
)

TODAY = date(2026, 8, 26)


def mk(uid, *, days_late=0, interval=10, stability=10.0, lapses=0, reps=3,
       course="course_a", concept=None, depth=0, kind="recall"):
    return Due(uid=uid, concept_uid=concept or f"con_{uid}", course_uid=course,
               kind=kind, due_date=(TODAY - timedelta(days=days_late)).isoformat(),
               interval_days=interval, stability=stability, lapses=lapses,
               repetitions=reps, depth=depth)


# ---- spreading load ------------------------------------------------------

def test_fuzz_window_scales_with_interval_and_is_zero_when_short():
    assert fuzz_window(1) == 0
    assert fuzz_window(3) == 0
    assert fuzz_window(100) == 5
    assert fuzz_window(10_000) == 21, "the window must stay bounded"


def test_balancing_moves_a_due_date_off_the_busiest_day():
    ideal = TODAY + timedelta(days=100)
    load = {ideal.isoformat(): 90,
            (ideal + timedelta(days=1)).isoformat(): 0}
    got = balance_due_date(ideal, 100, load, "itm_x")
    assert got != ideal
    assert abs((got - ideal).days) <= fuzz_window(100)


def test_balancing_never_schedules_into_the_past():
    ideal = TODAY + timedelta(days=1)
    got = balance_due_date(ideal, 400, {}, "itm_y")
    assert got > date.today()


def test_balancing_spreads_a_clump_instead_of_rebuilding_it():
    """Twelve items scheduled in the same breath must not all pick the same
    quietest day — that just moves the spike."""
    ideal = TODAY + timedelta(days=200)
    load = {}
    days = []
    for n in range(12):
        d = balance_due_date(ideal, 200, load, f"itm_{n}")
        load[d.isoformat()] = load.get(d.isoformat(), 0) + 1
        days.append(d)
    assert len(set(days)) >= 5, f"clumped onto {len(set(days))} days"
    assert max(load.values()) <= 4


# ---- ordering ------------------------------------------------------------

def test_overdue_is_relative_to_the_items_own_interval():
    short = mk("s", days_late=7, interval=2)
    long_ = mk("l", days_late=7, interval=200)
    assert overdue_ratio(short, TODAY) > overdue_ratio(long_, TODAY)


def test_foundations_outrank_what_is_built_on_them():
    base = mk("base", days_late=1, depth=0)
    deep = mk("deep", days_late=1, depth=6)
    assert priority(base, TODAY) > priority(deep, TODAY)


def test_fragile_memories_outrank_stable_ones():
    assert priority(mk("weak", days_late=1, stability=1.0), TODAY) > \
           priority(mk("firm", days_late=1, stability=300.0), TODAY)


def test_nothing_overdue_still_orders_deterministically():
    a, b = mk("a", days_late=0), mk("b", days_late=0)
    assert priority(a, TODAY) == pytest.approx(priority(b, TODAY))


# ---- leeches and retirement ---------------------------------------------

def test_a_leech_leaves_the_card_queue_for_repair():
    leech = mk("stuck", lapses=LEECH_LAPSES)
    assert is_leech(leech)
    out = build_queue([leech, mk("ok")], today=TODAY)
    assert leech not in out["queue"], "a leech kept cycling in the daily queue"
    assert out["leeches"] == [leech], "a leech must be surfaced, not just dropped"


def test_known_items_retire_from_the_daily_view():
    old = mk("known", interval=400, days_late=1)
    assert is_retired(old)
    out = build_queue([old], today=TODAY)
    assert out["queue"] == []
    assert out["counts"]["retired"] == 1


def test_a_new_item_never_counts_as_retired():
    """interval_days can be seeded high before the first review; a never-seen
    item must still be introduced rather than silently retired."""
    fresh = Due("n", "c", "k", "recall", TODAY.isoformat(), 400, None, 0, 0)
    out = build_queue([fresh], today=TODAY)
    assert out["counts"]["retired"] == 0
    assert len(out["queue"]) == 1


# ---- capping and honesty -------------------------------------------------

def test_a_capped_day_says_it_was_capped():
    items = [mk(f"i{n}", days_late=1) for n in range(100)]
    out = build_queue(items, today=TODAY, daily_cap=20)
    assert len(out["queue"]) == 20
    assert out["capped"] is True
    assert out["counts"]["held_back"] == 80, \
        "silently truncating reads as 'you are done'"


def test_new_items_pause_when_the_backlog_is_deep():
    reviews = [mk(f"r{n}", days_late=2) for n in range(200)]
    fresh = [Due(f"n{n}", f"c{n}", "course_a", "recall", TODAY.isoformat(),
                 0, None, 0, 0) for n in range(30)]
    out = build_queue(reviews + fresh, today=TODAY, daily_cap=20)
    assert out["new_paused_for_backlog"] is True
    assert all(i.repetitions for i in out["queue"]), \
        "new material was added on top of a backlog"


def test_new_items_flow_when_there_is_room():
    fresh = [Due(f"n{n}", f"c{n}", "course_a", "recall", TODAY.isoformat(),
                 0, None, 0, 0) for n in range(30)]
    out = build_queue(fresh, today=TODAY, daily_cap=60, new_per_day=12)
    assert len(out["queue"]) == 12
    assert out["new_paused_for_backlog"] is False


def test_items_due_later_are_not_in_todays_queue():
    later = Due("l", "c", "k", "recall",
                (TODAY + timedelta(days=3)).isoformat(), 10, 10.0, 0, 2)
    out = build_queue([later], today=TODAY)
    assert out["queue"] == [] and out["counts"]["upcoming"] == 1


# ---- interleaving --------------------------------------------------------

def test_courses_interleave_rather_than_block():
    items = ([mk(f"a{n}", course="course_a") for n in range(4)] +
             [mk(f"b{n}", course="course_b") for n in range(4)])
    order = [i.course_uid for i in interleave(items)]
    assert order[0] != order[1], f"one course blocked the queue: {order}"


def test_one_big_course_cannot_monopolise_a_capped_day():
    big = [mk(f"big{n}", course="course_big", days_late=1) for n in range(50)]
    small = [mk(f"sm{n}", course="course_small", days_late=1) for n in range(6)]
    out = build_queue(big + small, today=TODAY, daily_cap=20)
    courses = {i.course_uid for i in out["queue"]}
    assert "course_small" in courses, "the smaller course was crowded out"


# ---- the mix -------------------------------------------------------------

def test_every_tier_stays_open_at_every_bloom_level():
    """The ratio shifts with Bloom; the lane never closes. Pure-factual and
    pure-higher-order queues both underperform the mix."""
    for bloom in range(1, 7):
        mix = target_mix(bloom)
        assert abs(sum(mix.values()) - 1.0) < 1e-6
        assert all(v > 0 for v in mix.values()), f"bloom {bloom} closed a lane"


def test_higher_bloom_shifts_weight_to_higher_order():
    low, high = target_mix(1), target_mix(6)
    assert high["apply"] + high["socratic"] > low["apply"] + low["socratic"]
    assert high["recall"] < low["recall"]


def test_target_mix_survives_junk_input():
    for bad in (None, 0, 9, "x"):
        try:
            mix = target_mix(bad)
        except (TypeError, ValueError):
            pytest.fail(f"target_mix({bad!r}) raised")
        assert abs(sum(mix.values()) - 1.0) < 1e-6


# ---- forecast ------------------------------------------------------------

def test_forecast_covers_every_day_and_excludes_leeches():
    items = [mk("a", days_late=0), mk("b", days_late=-5),
             mk("leech", lapses=LEECH_LAPSES)]
    f = forecast(items, days=10, today=TODAY)
    assert len(f) == 11
    assert sum(p["count"] for p in f) == 2
    assert f[0]["date"] == TODAY.isoformat()


def test_forecast_puts_overdue_items_on_today_not_in_the_past():
    f = forecast([mk("late", days_late=30)], days=5, today=TODAY)
    assert f[0]["count"] == 1


def test_empty_input_is_an_empty_day_not_a_crash():
    out = build_queue([], today=TODAY)
    assert out["queue"] == [] and out["counts"]["due_total"] == 0
    assert out["capped"] is False


# ---- the diet of a new session ------------------------------------------

def _fresh(kind, n, bloom_concept="con_x"):
    return [Due(f"{kind}{i}", bloom_concept, "course_a", kind,
                TODAY.isoformat(), 0, None, 0, 0) for i in range(n)]


def test_new_items_are_a_mixed_diet_not_whatever_extracted_first():
    """Items extract in bank order — every Key Fact first. Taking them in that
    order hands out a pure-recall session, which transfers no better than no
    practice at all."""
    fresh = (_fresh("recall", 40) + _fresh("discriminate", 20) +
             _fresh("apply", 25) + _fresh("socratic", 8))
    out = build_queue(fresh, today=TODAY, daily_cap=60, new_per_day=12)
    kinds = {i.kind for i in out["queue"]}
    assert len(kinds) >= 3, f"the session was almost all one kind: {kinds}"
    recall = sum(1 for i in out["queue"] if i.kind == "recall")
    assert recall < len(out["queue"]), "the whole session was recall"


def test_the_mix_follows_the_bloom_of_the_concepts_on_offer():
    fresh = (_fresh("recall", 40) + _fresh("discriminate", 20) +
             _fresh("apply", 40) + _fresh("socratic", 20))
    low = build_queue(fresh, today=TODAY, new_per_day=20,
                      bloom_of={"con_x": 1})["queue"]
    high = build_queue(fresh, today=TODAY, new_per_day=20,
                       bloom_of={"con_x": 6})["queue"]
    higher = lambda q: sum(1 for i in q if i.kind in ("apply", "socratic"))
    assert higher(high) > higher(low)


def test_selection_never_exceeds_the_allowance_or_invents_items():
    fresh = _fresh("recall", 3) + _fresh("apply", 2)
    picked = select_new(fresh, 20, None)
    assert len(picked) == 5, "asked for more than exists and got duplicates"
    assert len({i.uid for i in picked}) == 5


def test_a_single_kind_still_fills_the_allowance():
    picked = select_new(_fresh("recall", 30), 10, None)
    assert len(picked) == 10 and all(i.kind == "recall" for i in picked)


def test_no_new_items_requested_returns_nothing():
    assert select_new(_fresh("recall", 5), 0, None) == []
    assert select_new([], 10, None) == []


def test_a_rare_kind_still_gets_a_seat():
    """Largest-remainder apportionment: a small share must not round to zero."""
    fresh = _fresh("recall", 100) + _fresh("socratic", 5)
    picked = select_new(fresh, 12, {"con_x": 4})
    assert any(i.kind == "socratic" for i in picked)
