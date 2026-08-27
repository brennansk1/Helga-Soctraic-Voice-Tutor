"""Queue policy over FSRS: which items, in what order, and how many today.

FSRS answers "when should this item come back". Everything that decides whether
a review habit survives a year and a twelve-course programme sits on top of that
answer, not inside it:

  * FSRS hands back an exact due date per item. Left alone across many courses
    those dates clump, and month three of a degree lands a 200-item Tuesday.
    A day that big does not get done; it gets abandoned, and the whole schedule
    with it. Intervals are therefore nudged within a tolerance the algorithm is
    indifferent to, onto the lightest nearby day.
  * When more is due than the learner can do, WHICH items get dropped decides
    what rots. Most-decayed first, and foundations before the things built on
    them: re-drilling NULLIF while three-valued logic is lapsed is wasted.
  * An item failed over and over is not a scheduling problem. It is a teaching
    problem, and it should leave the card queue for a Socratic repair instead
    of cycling forever.
  * New material must not be introduced faster than the reviews it creates can
    be sustained. This is the classic way a spaced-repetition habit dies.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence

# An item that keeps being forgotten stops being scheduled and is escalated.
LEECH_LAPSES = 4
# Past this interval an item is effectively known; it leaves the daily view.
RETIRED_DAYS = 365
# How far FSRS's chosen date may be moved to flatten a spike. Small enough that
# predicted retrievability barely moves, large enough to break up clumps.
FUZZ_MIN_DAYS = 2
FUZZ_FRACTION = 0.05
FUZZ_CAP_DAYS = 21
# Reviews are the obligation; new items are the choice. When the backlog is
# heavy, stop adding to it.
NEW_ITEMS_PER_DAY = 12
BACKLOG_PAUSES_NEW = 2.0   # multiples of the daily cap


@dataclass(frozen=True)
class Due:
    """What the scheduler needs to know about one item. Deliberately not the
    storage row: this module stays pure so its policy can be tested."""
    uid: str
    concept_uid: str
    course_uid: str
    kind: str
    due_date: str            # ISO
    interval_days: float
    stability: Optional[float]
    lapses: int
    repetitions: int
    depth: int = 0           # prerequisite depth; 0 = foundational

    @property
    def is_new(self) -> bool:
        return not self.repetitions


def _iso(d: date) -> str:
    return d.isoformat()


def _parse(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat((value or "")[:10])
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------- load spreading

def fuzz_window(interval_days: float) -> int:
    """How many days either side of the ideal date are acceptable."""
    if interval_days < FUZZ_MIN_DAYS * 2:
        return 0
    return int(min(FUZZ_CAP_DAYS, max(1, round(interval_days * FUZZ_FRACTION))))


def balance_due_date(ideal: date, interval_days: float,
                     load: Dict[str, int], item_uid: str = "") -> date:
    """Move a due date onto the least-loaded day inside its tolerance window.

    Ties break on a hash of the item id rather than arbitrarily, so two items
    scheduled in the same breath do not both land on the same "first quietest"
    day and rebuild the spike they were spread to avoid.
    """
    window = fuzz_window(interval_days)
    if not window:
        return ideal
    jitter = int(hashlib.sha1(item_uid.encode()).hexdigest(), 16) if item_uid else 0
    best, best_key = ideal, None
    for offset in range(-window, window + 1):
        day = ideal + timedelta(days=offset)
        if day <= date.today():
            continue                      # never schedule into the past
        key = (load.get(_iso(day), 0), abs(offset), (jitter + offset) % 97)
        if best_key is None or key < best_key:
            best, best_key = day, key
    return best


# ---------------------------------------------------------------- prioritising

def overdue_ratio(item: Due, today: date) -> float:
    """How far past its half-life an item has drifted, relative to its own
    interval — so a 2-day item a week late outranks a 200-day item a week late."""
    due = _parse(item.due_date, today)
    late = (today - due).days
    if late <= 0:
        return 0.0
    return late / max(1.0, float(item.interval_days or 1))


def priority(item: Due, today: date) -> float:
    """Higher is more urgent. Only the ORDER matters, not the magnitude."""
    score = 1.0 + overdue_ratio(item, today) * 3.0
    # Foundations first: reviewing a dependent while its prerequisite is lapsed
    # spends the learner's attention on the wrong concept.
    score *= 1.0 + max(0.0, (6 - min(item.depth, 6)) * 0.08)
    # Fragile memories decay soonest, so they are the ones worth catching.
    if item.stability:
        score *= 1.0 + min(1.0, 6.0 / max(1.0, float(item.stability)))
    else:
        score *= 1.5
    # A history of forgetting is a standing signal, up to a point.
    score *= 1.0 + min(item.lapses, LEECH_LAPSES) * 0.05
    return score


def is_leech(item: Due) -> bool:
    return item.lapses >= LEECH_LAPSES


def is_retired(item: Due) -> bool:
    return (item.interval_days or 0) >= RETIRED_DAYS


# ---------------------------------------------------------------- the mix

# Ratios by the concept's Bloom target. Every tier appears at every level: the
# ratio shifts, the lane never closes. Mixed factual + higher-order practice
# beats either pure form on higher-order tests (Agarwal), and factual practice
# alone transfers no better than no practice at all.
MIX_BY_BLOOM = {
    1: {"recall": 0.55, "discriminate": 0.25, "apply": 0.15, "socratic": 0.05},
    2: {"recall": 0.45, "discriminate": 0.25, "apply": 0.22, "socratic": 0.08},
    3: {"recall": 0.32, "discriminate": 0.24, "apply": 0.34, "socratic": 0.10},
    4: {"recall": 0.25, "discriminate": 0.22, "apply": 0.38, "socratic": 0.15},
    5: {"recall": 0.20, "discriminate": 0.20, "apply": 0.40, "socratic": 0.20},
    6: {"recall": 0.18, "discriminate": 0.18, "apply": 0.42, "socratic": 0.22},
}


def target_mix(bloom) -> Dict[str, float]:
    """Junk in gives the level-2 mix, never an exception: this is called while
    rendering a queue, and a malformed Bloom tag in one concept file must not
    take the whole review session down."""
    try:
        level = int(bloom or 2)
    except (TypeError, ValueError):
        level = 2
    return MIX_BY_BLOOM.get(max(1, min(6, level)), MIX_BY_BLOOM[2])


def select_new(fresh: Sequence[Due], allowance: int,
               bloom_of: Optional[Dict[str, int]] = None) -> List[Due]:
    """Choose today's new items so the diet is mixed, not just cheap.

    Taking new items in bank order hands out a queue of pure recall, because
    that is the order they extract in — and a pure-factual queue is precisely
    the arrangement the evidence rules out: practising facts and then testing
    understanding performs no better than not practising at all. Draws are
    therefore apportioned across kinds by the target mix, weighted by the Bloom
    level of the concepts actually on offer.
    """
    if allowance <= 0 or not fresh:
        return []

    buckets: Dict[str, List[Due]] = {}
    for it in fresh:
        buckets.setdefault(it.kind, []).append(it)

    blooms = [(bloom_of or {}).get(it.concept_uid, 2) for it in fresh] or [2]
    average = round(sum(blooms) / len(blooms))
    weights = target_mix(average)

    # Largest-remainder apportionment, so small shares still get a seat instead
    # of rounding away to nothing.
    quotas: Dict[str, float] = {k: weights.get(k, 0.0) * allowance for k in buckets}
    picked: Dict[str, int] = {k: min(len(buckets[k]), int(q)) for k, q in quotas.items()}
    while sum(picked.values()) < allowance:
        candidates = [k for k in buckets if picked[k] < len(buckets[k])]
        if not candidates:
            break
        k = max(candidates, key=lambda k: (quotas[k] - picked[k], weights.get(k, 0)))
        picked[k] += 1

    out: List[Due] = []
    for kind, n in picked.items():
        out.extend(buckets[kind][:n])
    # Interleave so a session does not open with six true/false in a row.
    order = sorted(buckets, key=lambda k: -picked.get(k, 0))
    woven, pools = [], {k: [i for i in out if i.kind == k] for k in order}
    while any(pools[k] for k in order):
        for k in order:
            if pools[k]:
                woven.append(pools[k].pop(0))
    return woven[:allowance]


def interleave(items: Sequence[Due]) -> List[Due]:
    """Round-robin across courses, then concepts.

    Blocking one course at a time is the comfortable order and the less
    effective one; interleaving is a desirable difficulty that improves
    retention and discrimination between similar material. It also stops one
    large course from monopolising a capped day.
    """
    buckets: Dict[str, List[Due]] = {}
    for it in items:
        buckets.setdefault(it.course_uid or "", []).append(it)
    order = sorted(buckets, key=lambda c: -len(buckets[c]))
    out: List[Due] = []
    while any(buckets[c] for c in order):
        for course in order:
            if buckets[course]:
                out.append(buckets[course].pop(0))
    return out


# ---------------------------------------------------------------- the queue

def build_queue(items: Iterable[Due], *, today: Optional[date] = None,
                daily_cap: int = 60,
                new_per_day: int = NEW_ITEMS_PER_DAY,
                bloom_of: Optional[Dict[str, int]] = None) -> Dict:
    """Today's queue, plus what was held back and why.

    Nothing is dropped silently: a capped day that does not say it was capped
    reads as "you are done", which is the one thing it must never imply.
    """
    today = today or date.today()
    cap = max(1, int(daily_cap or 1))

    due, leeches, retired, upcoming = [], [], [], []
    for it in items:
        if is_leech(it):
            leeches.append(it)
            continue
        if _parse(it.due_date, today) > today:
            upcoming.append(it)
            continue
        if is_retired(it) and it.repetitions:
            retired.append(it)
            continue
        due.append(it)

    reviews = [i for i in due if not i.is_new]
    fresh = [i for i in due if i.is_new]

    # Reviews are an obligation already incurred; new items are optional. A
    # backlog means the last thing to do is take on more.
    backlog = len(reviews) > cap * BACKLOG_PAUSES_NEW
    new_allowance = 0 if backlog else max(0, int(new_per_day))

    # Order by urgency, interleave across courses, and ONLY THEN cap. Capping
    # first let the largest course fill the day on its own and left
    # interleaving nothing to balance — a fifty-item course crowded a six-item
    # one out entirely. Round-robin preserves priority order within each
    # course, so the cap still drops the least urgent work.
    reviews.sort(key=lambda i: priority(i, today), reverse=True)
    chosen_reviews = interleave(reviews)[:cap]
    room = max(0, cap - len(chosen_reviews))
    chosen_new = select_new(fresh, min(room, new_allowance), bloom_of)

    queue = chosen_reviews + chosen_new
    return {
        "queue": queue,
        "counts": {
            "due_total": len(due),
            "reviews_due": len(reviews),
            "new_available": len(fresh),
            "in_queue": len(queue),
            "held_back": max(0, len(reviews) - len(chosen_reviews)),
            "leeches": len(leeches),
            "retired": len(retired),
            "upcoming": len(upcoming),
        },
        "leeches": leeches,
        "capped": len(reviews) > cap,
        "new_paused_for_backlog": backlog,
    }


def forecast(items: Iterable[Due], days: int = 30,
             today: Optional[date] = None) -> List[Dict]:
    """Due counts per day ahead — what the load balancer is flattening."""
    today = today or date.today()
    counts: Dict[str, int] = {}
    for it in items:
        if is_leech(it):
            continue
        d = max(_parse(it.due_date, today), today)
        if (d - today).days <= days:
            counts[_iso(d)] = counts.get(_iso(d), 0) + 1
    return [{"date": _iso(today + timedelta(days=n)),
             "count": counts.get(_iso(today + timedelta(days=n)), 0)}
            for n in range(days + 1)]
