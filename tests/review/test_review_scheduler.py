"""Queue policy: the part that decides whether a review habit survives a year."""
from datetime import date, timedelta

import pytest

from services.common.review_scheduler import (
    Due, LEECH_LAPSES, balance_due_date, build_queue, forecast, fuzz_window,
    interleave, is_leech, is_retired, maturity, overdue_ratio, priority, select_new,
    target_mix, MATURITY_BANDS, RETIRED_DAYS,
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


def test_forecast_excludes_never_reviewed_items():
    """An item with no due date is not due — it is unstarted. Counting the
    whole bank as due today put 2,485 items on the first bar of a chart whose
    only job is to show that the days ahead are survivable."""
    fresh = [Due(f"n{n}", "c", "k", "recall", TODAY.isoformat(), 0, None, 0, 0)
             for n in range(500)]
    scheduled = [mk("s", days_late=0)]
    f = forecast(fresh + scheduled, days=10, today=TODAY)
    assert sum(p["count"] for p in f) == 1, "unstarted items were counted as due"


# ---- maturity bands ------------------------------------------------------

def test_maturity_bands_follow_the_interval():
    assert maturity(0, 0) == 'new'
    assert maturity(0, 500) == 'new', "never reviewed is never 'known'"
    assert maturity(1, 1) == 'learning'
    assert maturity(3, 6.9) == 'learning'
    assert maturity(3, 7) == 'young'
    assert maturity(5, 20.9) == 'young'
    assert maturity(5, 21) == 'mature'
    assert maturity(9, 364) == 'mature'
    assert maturity(9, 365) == 'retired'


def test_maturity_never_raises_on_junk():
    for reps, ivl in ((None, None), ('x', 'y'), (-1, -5), (2, None)):
        assert maturity(reps, ivl) in MATURITY_BANDS


def test_retirement_and_maturity_agree():
    """Both read the same threshold; if they drift, an item can be 'retired' in
    one view and still shown in the other."""
    at = Due('u', 'c', 'k', 'recall', TODAY.isoformat(), RETIRED_DAYS, 9.0, 0, 5)
    assert is_retired(at) and maturity(5, RETIRED_DAYS) == 'retired'


# ---- scale ---------------------------------------------------------------

def test_a_full_bank_across_many_courses_still_yields_one_finishable_day():
    """The shape this has to survive: thousands of items, eight courses, a
    backlog, leeches and retired material all at once."""
    import random
    rng = random.Random(7)
    items = []
    for i in range(2500):
        course = 'course_%d' % (i % 8)
        roll = rng.random()
        if roll < 0.5:
            items.append(Due(f'i{i}', f'con_{i%400}', course, 'recall',
                             TODAY.isoformat(), 0, None, 0, 0))
        elif roll < 0.62:
            items.append(mk(f'i{i}', days_late=rng.randint(1, 9), course=course,
                            concept=f'con_{i%400}', interval=rng.randint(3, 30)))
        elif roll < 0.66:
            items.append(mk(f'i{i}', lapses=6, course=course, concept=f'con_{i%400}'))
        elif roll < 0.9:
            items.append(Due(f'i{i}', f'con_{i%400}', course, 'apply',
                             (TODAY + timedelta(days=rng.randint(1, 60))).isoformat(),
                             30, 40.0, 0, 4))
        else:
            items.append(mk(f'i{i}', interval=400, course=course,
                            concept=f'con_{i%400}', days_late=1))

    out = build_queue(items, today=TODAY, daily_cap=60)
    q = out['queue']
    assert len(q) <= 60, 'the cap did not hold at scale'
    assert out['counts']['held_back'] > 0 and out['capped']
    assert not any(is_leech(i) for i in q), 'a leech reached the daily queue'
    assert not any(is_retired(i) and i.repetitions for i in q), 'retired work resurfaced'
    assert len({i.course_uid for i in q}) >= 4, 'one course monopolised the day'
    assert out['counts']['leeches'] > 0, 'leeches were dropped instead of surfaced'


def test_forecast_over_a_year_stays_bounded_and_ordered():
    items = [Due(f'i{n}', 'c', 'k', 'recall',
                 (TODAY + timedelta(days=n % 120)).isoformat(), 30, 40.0, 0, 3)
             for n in range(600)]
    f = forecast(items, days=120, today=TODAY)
    assert len(f) == 121
    assert [p['date'] for p in f] == sorted(p['date'] for p in f)
    assert sum(p['count'] for p in f) == 600


def test_a_spent_daily_allowance_introduces_nothing_more():
    """The new-item budget belongs to the DAY, not the request. Rebuilding the
    queue after finishing a session handed out another full batch, so the day
    could never be completed — a treadmill, and the surest way to stop someone
    reviewing at all. The caller spends the budget down and passes what is left."""
    fresh = [Due(f'n{n}', f'c{n}', 'course_a', 'recall', TODAY.isoformat(),
                 0, None, 0, 0) for n in range(50)]
    spent = build_queue(fresh, today=TODAY, daily_cap=60, new_per_day=0)
    assert spent['queue'] == [], 'new items kept coming after the day was done'
    assert spent['counts']['new_available'] == 50, \
        'the waiting items should still be counted, just not served'

    partial = build_queue(fresh, today=TODAY, daily_cap=60, new_per_day=4)
    assert len(partial['queue']) == 4


def test_retention_target_moves_every_interval():
    """The Settings control has to reach FSRS, not just be stored. It read a
    different key-value table from the one Settings writes, so it returned the
    0.9 default forever and looked exactly like a working setting."""
    from services.core.fsrs_engine import FSRSEngine
    intervals = {r: FSRSEngine(desired_retention=r).next_interval(60)
                 for r in (0.85, 0.90, 0.95)}
    assert intervals[0.85] > intervals[0.90] > intervals[0.95], intervals
    # Asking for more certainty must cost meaningfully more reviews, or the
    # control is decorative.
    assert intervals[0.85] >= intervals[0.95] * 2


# ---- weak prerequisites --------------------------------------------------

from services.common.review_scheduler import (  # noqa: E402
    concept_strength, is_weak, weakest_root,
)


def _item(concept, *, reps=5, lapses=0, stability=40.0):
    return Due(f"{concept}-{lapses}-{stability}", concept, "course_a", "recall",
               TODAY.isoformat(), 10, stability, lapses, reps)


def test_a_concept_that_keeps_slipping_reads_as_weak():
    weak = concept_strength([_item("c", lapses=3, stability=2.0),
                             _item("c", lapses=4, stability=1.5)])
    firm = concept_strength([_item("d", lapses=0, stability=90.0),
                             _item("d", lapses=0, stability=120.0)])
    assert is_weak(weak["c"])
    assert not is_weak(firm["d"])


def test_an_unseen_concept_is_not_weak():
    """Unstarted is not failing. Telling someone to revisit a concept they have
    never met would be nonsense."""
    unseen = concept_strength([Due("u", "c", "k", "recall", TODAY.isoformat(),
                                   0, None, 0, 0)])
    assert not is_weak(unseen["c"])
    assert not is_weak(None)
    assert not is_weak({})


def test_the_deepest_weak_ancestor_is_the_one_named():
    """NULLIF rests on three-valued logic, which rests on NULL semantics. If the
    bottom one is failing, sending the learner back to the middle re-teaches a
    symptom."""
    edges = {"nullif": ["threeval"], "threeval": ["nullsem"], "nullsem": []}
    strength = concept_strength(
        [_item("threeval", lapses=3, stability=2.0),
         _item("nullsem", lapses=5, stability=1.0)])
    assert weakest_root("nullif", edges.get, strength) == "nullsem"


def test_a_solid_foundation_yields_no_suggestion():
    edges = {"nullif": ["threeval"], "threeval": []}
    strength = concept_strength([_item("threeval", lapses=0, stability=120.0)])
    assert weakest_root("nullif", edges.get, strength) is None


def test_a_cycle_terminates():
    edges = {"a": ["b"], "b": ["a"]}
    assert weakest_root("a", edges.get, {}) is None


def test_a_prereq_lookup_that_raises_costs_only_the_suggestion():
    def boom(uid):
        raise RuntimeError("map unavailable")
    assert weakest_root("a", boom, {}) is None


def test_depth_is_bounded():
    """A long chain must not walk the whole course."""
    chain = {f"c{i}": [f"c{i+1}"] for i in range(20)}
    strength = concept_strength([_item("c19", lapses=9, stability=1.0)])
    assert weakest_root("c0", chain.get, strength, max_depth=3) is None
