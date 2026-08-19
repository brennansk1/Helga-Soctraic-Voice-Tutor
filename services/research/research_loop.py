"""Iterative research: the model decides what to look for, code decides when to stop.

THE DIVISION OF LABOUR
----------------------
A model driving its own research loop is worth having — it knows what a topic
*means*, which is exactly what query formulation needs. Measured: resolving
"Learning to be a Dungeon Master", blind search returned *Dungeon Crawler Carl*
(a novel), category filtering still returned the novel, and a model-suggested
candidate returned **Gamemaster**.

But the loop must not exit on the model's own judgement. `scope_fit` exists
because asking a model "is there enough material?" invites the same optimism that
produces forty padded courses, and the judges in this repo swing ±10 points on
identical input. **Self-assessed sufficiency is the weakest available stopping
rule**, and a nondeterministic one: the same course built twice would get
different research depth, invisibly.

So:

    the model PROPOSES   — the checklist, and the queries for uncovered items
    the code DISPOSES    — whether an item is covered, and whether to continue

THE EXIT CONDITION
------------------
Measured coverage of a checklist, never satisfaction. The loop stops when every
item is sourced, or the budget is spent, or two rounds add nothing new.

A REAL SYLLABUS REPLACES THE CHECKLIST
--------------------------------------
Where a published syllabus exists it becomes the checklist outright — it is not
merged with the model's list and not supplemented by it. A model-invented
checklist is a fallback for subjects that have no published standard, and a
course built against one is labelled as such, because it did not meet a
published standard and the record should say so.
"""

import logging

logger = logging.getLogger(__name__)

MAX_ROUNDS = 4
# Rounds that may add no new sources before the loop concludes the subject is
# dry. Two rather than one: a single empty round is often a bad query, while two
# is the subject.
DRY_ROUNDS = 2


def run_research_loop(checklist, search_fn, propose_queries_fn=None,
                      max_rounds=MAX_ROUNDS, is_covered_fn=None):
    """Search until the checklist is covered, the budget is spent, or it goes dry.

    `checklist`           items that must be sourced (from a real syllabus where
                          one exists; model-invented only as a fallback)
    `search_fn(query)`    -> list of source dicts, or [] . MUST return [] for a
                          DEGRADED lookup as well as an empty one; the caller
                          distinguishes them via `degraded_fn` below.
    `propose_queries_fn`  (items, found) -> queries. This is the model's job.
    `is_covered_fn`       (item, sources) -> bool. Deterministic. Defaults to
                          keyword containment, which cannot drift.

    Returns a dict recording what was covered, what was not, and why it stopped.
    """
    items = [c for c in (checklist or []) if isinstance(c, str) and c.strip()]
    if not items:
        return {"ran": False, "reason": "empty checklist"}

    covered_by = {}
    sources = []
    rounds = 0
    dry = 0
    is_covered = is_covered_fn or _default_is_covered

    while rounds < max_rounds:
        outstanding = [i for i in items if i not in covered_by]
        if not outstanding:
            break

        rounds += 1
        queries = (propose_queries_fn(outstanding, sources)
                   if propose_queries_fn else list(outstanding))
        queries = [q for q in (queries or []) if isinstance(q, str) and q.strip()]
        if not queries:
            logger.info("[LOOP] no queries proposed — stopping")
            break

        before = len(sources)
        for q in queries:
            try:
                found = search_fn(q) or []
            except Exception as e:
                logger.warning(f"[LOOP] search failed for {q!r}: {e}")
                continue
            sources.extend(found)

        # NO PROGRESS IS A STOPPING CONDITION, not a reason to try harder.
        # Without it a loop grinds through its whole budget on a dry subject,
        # and — worse — on a silently failing service, which is what the
        # 4096-token ceiling looked like from the outside.
        if len(sources) == before:
            dry += 1
            logger.info(f"[LOOP] round {rounds} added no sources "
                        f"({dry}/{DRY_ROUNDS} dry)")
            if dry >= DRY_ROUNDS:
                break
            continue
        dry = 0

        for item in outstanding:
            if is_covered(item, sources):
                covered_by[item] = rounds

    outstanding = [i for i in items if i not in covered_by]
    return {
        "ran": True,
        "rounds": rounds,
        "items": len(items),
        "covered": len(covered_by),
        "outstanding": outstanding,
        "coverage_pct": round(len(covered_by) / max(1, len(items)) * 100),
        "sources": len(sources),
        "stopped_because": ("covered" if not outstanding else
                            "dry" if dry >= DRY_ROUNDS else
                            "budget" if rounds >= max_rounds else "no queries"),
        # The exit is a measurement, and saying so on the record keeps anyone
        # later from reading it as the model's opinion.
        "exit_rule": "measured coverage of the checklist (no model judgement)",
    }


def topic_name(item):
    """The topic, without its explanation.

    Models answer "list the topics" with "Topic: a sentence explaining it", and
    a 20-word item can never be matched against source titles — measured, the
    loop found 248 sources and scored 0/12 because every item was a sentence.
    The part before the colon is the topic; the rest is prose about it.
    """
    head = (item or "").split(":", 1)[0].strip()
    # A dash is the other common separator, but only when it is separating
    # rather than hyphenating a word ("Gram-Schmidt" must survive).
    for sep in (" — ", " – ", " - "):
        if sep in head:
            head = head.split(sep, 1)[0].strip()
    return head or (item or "").strip()


def _default_is_covered(item, sources):
    """Deterministic containment. No judge, so it cannot drift or hallucinate.

    Weak on purpose: it answers "did we find something about this", which is the
    question the loop needs, and leaves "is it any good" to the depth contract
    and fact-check, which run over the content afterwards.
    """
    # Match on the topic, not on its explanation, and cap the words considered —
    # requiring half of a long phrase is a test nothing can pass.
    words = [w for w in topic_name(item).lower().split() if len(w) > 3][:6]
    if not words:
        return False
    blob = " ".join(
        f"{s.get('title', '')} {s.get('snippet', '')}".lower()
        for s in sources if isinstance(s, dict))
    hits = sum(1 for w in words if w in blob)
    return hits >= max(1, len(words) // 2)


def checklist_from(brief, model_fallback=None):
    """The checklist, and whether it came from a published syllabus.

    A real syllabus REPLACES the model's list rather than merging with it.
    Merging would let invented items dilute a published standard, and then
    "covered the checklist" would no longer mean "covered the syllabus".
    """
    chapters = []
    for outline in ((brief or {}).get("syllabi") or []):
        chapters.extend(c for c in (outline.get("chapters") or [])
                        if isinstance(c, str) and c.strip())
    if chapters:
        return {"items": chapters, "source": "published syllabus",
                "authoritative": True}
    return {"items": list(model_fallback or []), "source": "model-proposed",
            "authoritative": False,
            "note": ("no published syllabus for this subject — coverage is "
                     "against an invented checklist and does not demonstrate "
                     "parity with any published course")}
