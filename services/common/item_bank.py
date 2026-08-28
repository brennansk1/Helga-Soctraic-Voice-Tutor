"""Build the review item bank for a course from its concept files.

Called at the end of a course build, and re-runnable at any time: item ids are
derived from source text, so syncing an unchanged course is a no-op and syncing
an edited one refreshes exactly the items whose wording moved.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Dict, List, Optional

from services.common.review_items import extract, prerequisite_titles

logger = logging.getLogger(__name__)


def _concept_order(structure: Dict) -> List[Dict]:
    """Concepts in teaching order, with the title index needed for depth."""
    out = []
    for module in (structure or {}).get("modules", []) or []:
        for unit in module.get("units", []) or []:
            for lesson in unit.get("lessons", []) or []:
                for concept in lesson.get("concepts", []) or []:
                    if concept.get("uid"):
                        out.append(concept)
    return out


def prerequisite_depth(concepts: List[Dict], content_for: Callable) -> tuple:
    """(depth, edges) — how far each concept sits from the foundations, and
    which concepts each one rests on.

    The edges used to be discarded once depth was computed. Depth can order a
    queue; only the edges can answer "is the thing underneath this also weak?"

    Depth is computed over titles because that is what the hydrator writes into
    '## Prerequisites'. Cycles and dangling names resolve to depth 0 rather than
    raising: a malformed prerequisite line must not cost the whole course its
    item bank.
    """
    by_title = {(c.get("title") or "").strip().lower(): c.get("uid")
                for c in concepts if c.get("uid")}
    prereqs: Dict[str, List[str]] = {}
    for c in concepts:
        uid = c["uid"]
        md = content_for(uid) or ""
        names = prerequisite_titles(md)
        prereqs[uid] = [by_title[n.strip().lower()] for n in names
                        if n.strip().lower() in by_title
                        and by_title[n.strip().lower()] != uid]

    edges = dict(prereqs)          # the caller wants these, not just the depth
    depth: Dict[str, int] = {}
    visiting = set()

    def resolve(uid: str) -> int:
        if uid in depth:
            return depth[uid]
        if uid in visiting:            # cycle: treat as foundational
            return 0
        visiting.add(uid)
        parents = prereqs.get(uid) or []
        depth[uid] = 0 if not parents else 1 + max(resolve(p) for p in parents)
        visiting.discard(uid)
        return depth[uid]

    for c in concepts:
        try:
            resolve(c["uid"])
        except RecursionError:
            depth[c["uid"]] = 0

    # Normalise to 0-6. The hydrator writes every earlier concept into the next
    # one's prerequisite list, so raw depth is really ordinal position and ran
    # to 88 on the SQL course — far past the point where queue priority still
    # distinguishes anything. A course-relative band keeps "how foundational is
    # this, for this course" meaningful whether the course has 4 concepts or 95.
    if depth:
        deepest = max(depth.values()) or 1
        depth = {uid: min(6, round(6 * d / deepest)) for uid, d in depth.items()}
    return depth, edges


def build_for_course(course_uid: str, storage, data_root: str = "data",
                     student_id: Optional[str] = None,
                     status_cb: Optional[Callable] = None) -> Dict:
    """Extract every item for a course and sync it into the store."""
    def note(msg: str):
        if status_cb:
            try:
                status_cb(msg)
            except Exception:
                pass

    # get_course returns the merged structure.json — modules/units/lessons/
    # concepts included — so there is no second read to keep in step with it.
    structure = storage.courses.get_course(course_uid) or {}
    concepts = _concept_order(structure)
    if not concepts:
        logger.info("item bank: %s has no concepts", course_uid)
        return {"items": 0, "concepts": 0, "written": 0, "updated": 0, "retired": 0}

    content_dir = os.path.join(data_root, "courses", course_uid, "content")

    def content_for(uid: str) -> str:
        path = os.path.join(content_dir, f"{uid}.md")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    depth, edges = prerequisite_depth(concepts, content_for)

    items = []
    for c in concepts:
        md = content_for(c["uid"])
        if not md:
            continue
        for item in extract(md, c["uid"], course_uid):
            item.payload["depth"] = depth.get(c["uid"], 0)
            items.append(item)

    if not items:
        note("ITEMS:NONE")
        return {"items": 0, "concepts": len(concepts),
                "written": 0, "updated": 0, "retired": 0}

    try:
        storage.courses.save_prereqs(course_uid, edges)
    except Exception as e:
        # A missing dependency map costs a better repair suggestion, not the
        # item bank.
        logger.warning("could not save prerequisite edges for %s: %s", course_uid, e)

    result = storage.flashcards.sync_items(items, student_id=student_id)
    # depth lives on the row so the queue can order by it without re-reading
    # every markdown file on every request
    for item in items:
        d = item.payload.get("depth", 0)
        if d:
            storage.flashcards.update_card(item.uid, student_id=student_id, depth=d)

    note(f"ITEMS:{len(items)}")
    logger.info("item bank for %s: %d items over %d concepts (%s)",
                course_uid, len(items), len(concepts), result)
    return {"items": len(items), "concepts": len(concepts), **result}
