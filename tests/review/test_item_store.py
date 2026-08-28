"""Item bank persistence: syncing extracted items must never cost a learner
their recall history, and re-extraction must be idempotent."""
import pytest

from services.common.review_items import extract
from services.common.storage import StorageManager

CONCEPT = """# Test Concept

## Metadata
- **Bloom Target**: 3 (Apply)

## Mastery Criteria
Grade 3 requires: naming the two phases.

## Key Facts
- `FOO(a)` is shorthand for `BAR WHEN a THEN NULL END` in every dialect.
- **Ordering**: the engine evaluates the left operand before the right one.

## Misconceptions
- **Belief**: `FOO(a)` returns TRUE when a is empty.
  **Correction**: It returns NULL, so wrap it in `IS NULL`.

## Edge Cases & Limitations
- **Type Mismatch:** implicit casting is attempted between operands.

## Socratic Hooks
- Bloom 1-2: What does `SELECT FOO(1);` return?
- Bloom 5-6: Evaluate FOO against a sentinel-value approach.
"""


@pytest.fixture()
def sm(tmp_path):
    return StorageManager(data_dir=str(tmp_path))


def test_sync_writes_then_is_idempotent(sm):
    items = extract(CONCEPT, "con_a", "course_a")
    first = sm.flashcards.sync_items(items)
    assert first["written"] == len(items) and first["retired"] == 0

    second = sm.flashcards.sync_items(items)
    assert second["written"] == 0, "re-extraction created duplicate items"
    assert second["updated"] == len(items)
    assert second["retired"] == 0
    assert len(sm.flashcards.get_items()) == len(items)


def test_sync_preserves_recall_history(sm):
    """The learner's memory state belongs to the learner; extraction may only
    refresh the wording."""
    items = extract(CONCEPT, "con_a", "course_a")
    sm.flashcards.sync_items(items)
    target = items[0].uid
    sm.flashcards.update_card(target, stability=42.0, difficulty=7.5,
                              repetitions=9, lapses=2,
                              next_review_date="2027-01-01")

    sm.flashcards.sync_items(extract(CONCEPT, "con_a", "course_a"))

    row = [i for i in sm.flashcards.get_items() if i["uid"] == target][0]
    assert row["stability"] == 42.0
    assert row["repetitions"] == 9
    assert row["lapses"] == 2
    assert row["next_review_date"] == "2027-01-01"


def test_edited_content_retires_the_stale_item_rather_than_deleting_it(sm):
    sm.flashcards.sync_items(extract(CONCEPT, "con_a", "course_a"))
    edited = CONCEPT.replace("before the right one", "after the right one")

    result = sm.flashcards.sync_items(extract(edited, "con_a", "course_a"))
    assert result["retired"] >= 1
    assert result["written"] >= 1

    live = {i["uid"] for i in sm.flashcards.get_items()}
    everything = {i["uid"] for i in sm.flashcards.get_items(include_retired=True)}
    assert everything - live, "the stale item was deleted, taking its history"


def test_items_carry_their_kind_bloom_and_payload(sm):
    sm.flashcards.sync_items(extract(CONCEPT, "con_a", "course_a"))
    rows = sm.flashcards.get_items()
    assert {r["kind"] for r in rows} >= {"recall", "discriminate", "apply", "socratic"}
    disc = [r for r in rows if r["kind"] == "discriminate"]
    assert all(isinstance(r["payload"], dict) for r in disc)
    assert {r["payload"].get("truth") for r in disc} == {True, False}
    soc = [r for r in rows if r["kind"] == "socratic"][0]
    assert "Grade 3 requires" in soc["payload"]["rubric"]


def test_day_load_counts_scheduled_days(sm):
    sm.flashcards.sync_items(extract(CONCEPT, "con_a", "course_a"))
    uids = [i["uid"] for i in sm.flashcards.get_items()][:3]
    for u in uids:
        sm.flashcards.update_card(u, next_review_date="2027-03-01")
    assert sm.flashcards.day_load().get("2027-03-01") == 3


def test_grading_spreads_due_dates_instead_of_clumping(sm):
    """Twenty items graded identically must not all land on the same day."""
    from services.core.fsrs_engine import FSRSEngine
    items = extract(CONCEPT, "con_a", "course_a")
    sm.flashcards.sync_items(items)
    engine = FSRSEngine()

    # give them all a long, identical history so the fuzz window is wide
    for it in items:
        sm.flashcards.update_card(it.uid, stability=120.0, difficulty=5.0,
                                  repetitions=6, interval_days=120)
    days = set()
    for it in items:
        days.add(sm.flashcards.grade_card_fsrs(it.uid, 3, engine)["next_review_date"])
    assert len(days) > 1, f"every graded item landed on one day: {days}"


def test_retired_items_are_excluded_from_the_queue_source(sm):
    sm.flashcards.sync_items(extract(CONCEPT, "con_a", "course_a"))
    uid = sm.flashcards.get_items()[0]["uid"]
    sm.flashcards.update_card(uid, status="retired")
    assert uid not in {i["uid"] for i in sm.flashcards.get_items()}
    assert uid in {i["uid"] for i in sm.flashcards.get_items(include_retired=True)}


# ---------------------------------------------------------------------------
# A RECALL-ONLY BANK MUST ANNOUNCE ITSELF.
#
# Items are extracted, not generated, so a course whose concepts lack the
# sections the tutor reads yields only the prose fallback — recall. Measured on
# "Reading a Query Plan": 26 items, every one recall, and scoping review to it
# served twelve of them as an ordinary session. Factual-only retrieval practice
# is the one case the evidence says does not transfer, and nothing on screen
# distinguished it from a full session.
# ---------------------------------------------------------------------------

def _item(uid, course_uid, kind, bloom=1):
    return {"uid": uid, "course_uid": course_uid, "concept_uid": "c_" + uid,
            "front": "q " + uid, "back": "a " + uid, "kind": kind,
            "bloom": bloom}


def test_kinds_in_scope_reports_only_what_is_there(sm):
    from services.common.review_items import RECALL, APPLY
    sm.flashcards.sync_items([_item("a", "course_thin", RECALL),
                              _item("b", "course_thin", RECALL)])
    assert sm.flashcards.kinds_in_scope(course_uid="course_thin") == {RECALL}

    sm.flashcards.sync_items([_item("c", "course_full", RECALL),
                              _item("d", "course_full", APPLY, bloom=3)])
    assert sm.flashcards.kinds_in_scope(course_uid="course_full") == {RECALL,
                                                                     APPLY}


def test_kinds_in_scope_is_scoped_to_the_course(sm):
    """The point is per-course: a healthy library must not mask one course
    whose bank is recall alone."""
    from services.common.review_items import RECALL, APPLY
    sm.flashcards.sync_items([_item("a", "course_thin", RECALL),
                              _item("d", "course_full", APPLY, bloom=3)])
    assert sm.flashcards.kinds_in_scope(course_uid="course_thin") == {RECALL}
    assert sm.flashcards.kinds_in_scope() == {RECALL, APPLY}


def test_an_empty_scope_reports_nothing_rather_than_raising(sm):
    assert sm.flashcards.kinds_in_scope(course_uid="course_absent") == set()
