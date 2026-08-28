"""Stage 5 end to end: build_for_course, and what happens when content changes.

The entry point the build actually calls had NO test. Everything beneath it —
sync_items' idempotence, history preservation, retirement — is covered in
test_item_store.py, but nothing exercised the function that reads a course's
markdown off disk and drives that machinery, which is what runs at the end of
every build and what "re-extraction when content later changes" means.

Verified by hand against the live SQL course first (1,317 items over 95
concepts; adding one Key Fact gave written=1/updated=1316/retired=0, removing
it again gave written=0/retired=1 with the row kept as `retired`, and a seeded
stability/repetitions/lapses survived both passes). This makes that repeatable
without touching real data.
"""
import os

import pytest

from services.common.item_bank import build_for_course
from services.common.storage import StorageManager


CONCEPT = """# Window Frames

## Key Facts
- A window frame defaults to RANGE, not ROWS, when ORDER BY is present.
- `LAG` reads the partition's sort order rather than the frame boundary.

## Misconceptions
- **Belief**: `RANK()` skips no numbers after a tie.
  **Correction**: it skips as many as the tie consumed.

## Edge Cases & Limitations
- **Empty partition:** aggregate window functions return NULL, not zero.

## Socratic Hooks
- Bloom 1-2: What frame does `OVER (ORDER BY x)` imply?
- Bloom 5-6: Evaluate RANGE against ROWS for a moving average.
"""

EXTRA_FACT = "- A probe fact: frames are evaluated per partition.\n"


@pytest.fixture()
def course(tmp_path):
    """A one-concept course on disk, shaped the way build_for_course reads it."""
    sm = StorageManager(data_dir=str(tmp_path))
    uid, cuid = "course_stage5", "con_stage5a"
    sm.courses.create_course({
        "uid": uid, "title": "Stage 5", "modules": [
            {"uid": "mod_1", "title": "M", "units": [
                {"uid": "unit_1", "title": "U", "lessons": [
                    {"uid": "less_1", "title": "L", "concepts": [
                        {"uid": cuid, "title": "Window Frames"}]}]}]}]})
    content = tmp_path / "courses" / uid / "content"
    content.mkdir(parents=True, exist_ok=True)
    md = content / f"{cuid}.md"
    md.write_text(CONCEPT, encoding="utf-8")
    return sm, uid, cuid, md, str(tmp_path)


def test_stage5_extracts_a_bank_from_disk(course):
    sm, uid, cuid, _md, root = course
    r = build_for_course(uid, sm, data_root=root)
    assert r["concepts"] == 1
    assert r["items"] > 0 and r["written"] == r["items"]
    assert r["retired"] == 0
    kinds = sm.flashcards.kinds_in_scope(course_uid=uid)
    assert "recall" in kinds, "a bank with no recall items is not a bank"
    assert len(kinds) > 1, f"only one tier came out: {kinds}"


def test_re_extraction_is_idempotent(course):
    sm, uid, cuid, _md, root = course
    first = build_for_course(uid, sm, data_root=root)
    second = build_for_course(uid, sm, data_root=root)
    assert second["written"] == 0, "re-running Stage 5 duplicated items"
    assert second["retired"] == 0
    assert second["items"] == first["items"]


def test_added_content_adds_items_and_keeps_review_history(course):
    sm, uid, cuid, md, root = course
    build_for_course(uid, sm, data_root=root)

    # A card the learner has actually studied.
    victim = sm.flashcards.get_items(course_uid=uid)[0]["uid"]
    sm.flashcards.update_card(victim, stability=42.0, repetitions=7, lapses=3)

    md.write_text(CONCEPT.replace("## Misconceptions",
                                  EXTRA_FACT + "\n## Misconceptions"),
                  encoding="utf-8")
    r = build_for_course(uid, sm, data_root=root)

    assert r["written"] >= 1, "the new fact produced no item"
    assert r["retired"] == 0, "adding content must retire nothing"

    kept = [i for i in sm.flashcards.get_items(course_uid=uid)
            if i["uid"] == victim]
    assert kept, "a studied item vanished across re-extraction"
    assert kept[0]["stability"] == 42.0 and kept[0]["repetitions"] == 7 \
        and kept[0]["lapses"] == 3, "review history was reset by re-extraction"


def test_removed_content_retires_rather_than_deletes(course):
    sm, uid, cuid, md, root = course
    md.write_text(CONCEPT.replace("## Misconceptions",
                                  EXTRA_FACT + "\n## Misconceptions"),
                  encoding="utf-8")
    build_for_course(uid, sm, data_root=root)

    md.write_text(CONCEPT, encoding="utf-8")          # take the fact back out
    r = build_for_course(uid, sm, data_root=root)

    assert r["retired"] >= 1, "stale content left its items schedulable"
    rows = sm.flashcards.get_items(course_uid=uid, include_retired=True)
    retired = [i for i in rows if (i.get("status") or "") == "retired"]
    assert retired, "the item was deleted outright; history has to survive"


def test_a_course_with_no_readable_content_reports_none(course, tmp_path):
    """The ITEMS:NONE path — a real case, not a hypothetical: a course whose
    concepts lack the sections extraction reads yields nothing."""
    sm, uid, cuid, md, root = course
    md.write_text("# Title only\n\nProse with no sections.\n", encoding="utf-8")
    seen = []
    r = build_for_course(uid, sm, data_root=root, status_cb=seen.append)
    assert r["items"] == 0
    assert any(str(m).startswith("ITEMS:") for m in seen), \
        "Stage 5 said nothing at all about an empty bank"
