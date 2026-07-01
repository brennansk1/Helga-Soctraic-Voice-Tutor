"""Catalog authoring & review (B16.3, B26.1–B26.3 — design spec 04 §3–§6).

The catalog is authored offline from a standards BRIEF (never a student topic
string), reviewed by a human, versioned, and frozen. This module is the admin
job runner + the review state machine; it reuses the existing
SkeletonBuilder → SyllabusAuditor → ContentHydrator pipeline unchanged and is
NEVER invoked from a student request path.

CLI:
    python -m services.core.catalog_admin standards load [--prune]
    python -m services.core.catalog_admin build data/catalog/briefs/<brief>.yaml
"""

import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from services.common.prompts import GRADE_BAND_PROFILES  # noqa: E402

VALID_ACTIONS = ("submit_for_review", "approve", "publish",
                 "request_changes", "revise", "retire")


# ------------------------------------------------------------------- briefs

def load_brief(path):
    """Parse a YAML/JSON standards brief (spec 04 §3.1)."""
    with open(path, "r") as f:
        raw = f.read()
    if path.endswith(".json"):
        brief = json.loads(raw)
    else:
        import yaml
        brief = yaml.safe_load(raw)
    for field in ("brief_id", "title", "subject", "grade_band", "standard_codes"):
        if not brief.get(field):
            raise ValueError(f"brief missing required field {field!r}")
    if brief["grade_band"] not in GRADE_BAND_PROFILES:
        raise ValueError(f"brief grade_band {brief['grade_band']!r} not a band")
    return brief


def catalog_storage(storage):
    """A StorageManager view whose course files land in data/catalog/courses/
    (same SQLite, catalog CourseStore) — the builders write through
    `.courses`, so swapping the sub-store routes the whole pipeline."""
    import copy as _copy
    view = _copy.copy(storage)
    view.courses = storage.catalog_courses
    return view


# -------------------------------------------------------------------- build

def build_from_brief(storage, brief, skeleton_cls=None, auditor_cls=None,
                     hydrator_cls=None, status_callback=None, llm_json=None):
    """B16.3/B26.1: standards-driven batch build. Builder classes are
    injectable for tests; defaults are the real pipeline."""
    if skeleton_cls is None or hydrator_cls is None or auditor_cls is None:
        from services.core.course_builder import (SkeletonBuilder,
                                                  SyllabusAuditor,
                                                  ContentHydrator)
        skeleton_cls = skeleton_cls or SkeletonBuilder
        auditor_cls = auditor_cls or SyllabusAuditor
        hydrator_cls = hydrator_cls or ContentHydrator

    band = GRADE_BAND_PROFILES[brief["grade_band"]]
    cat_storage = catalog_storage(storage)

    # topic synthesis: title + a standards block so the LLM names real Utah
    # strand sub-areas (spec 04 §3.2.1)
    standards_texts = []
    for code in brief["standard_codes"]:
        row = storage.standards.get(code)
        if not row:
            raise ValueError(f"brief references unknown standard {code!r}")
        standards_texts.append(f"{code} ({row['strand']}): {row['text']}")
    topic = brief["title"]
    hints = "\n".join(f"- {h}" for h in brief.get("module_hints", []))
    standards_block = ("STANDARDS TO COVER:\n" + "\n".join(standards_texts)
                       + (f"\nSTRUCTURAL HINTS:\n{hints}" if hints else "")
                       + (f"\nNOTES: {brief['notes']}" if brief.get("notes") else ""))

    builder = skeleton_cls(
        storage=cat_storage,
        status_callback=status_callback,
        scope=int(brief.get("scope", 3)),
        mastery=int(brief.get("mastery", 3)),
        starting_from=int(brief.get("starting_from", 1)),
        teaching_style=brief.get("teaching_style", ""),
    )
    course_uid = builder.build(f"{topic}\n\n{standards_block}")
    if not course_uid:
        raise RuntimeError("skeleton build failed (see logs)")

    auditor_cls(storage=cat_storage, status_callback=status_callback).audit(course_uid)
    hydrator_cls(storage=cat_storage, status_callback=status_callback,
                 mastery=int(brief.get("mastery", 3))).hydrate(course_uid)

    course = cat_storage.courses.get_course(course_uid)
    # band bounds override sliders for catalog courses (spec 04 §3.2.2/§3.5)
    course["bloom_floor"] = band["bloom_floor"]
    course["bloom_ceiling"] = band["bloom_ceiling"]
    course["catalog"] = {
        "is_catalog": True,
        "subject": brief["subject"],
        "grade_band": brief["grade_band"],
        "grade_numeric": brief.get("grade_numeric"),
        "standard_codes": sorted(brief["standard_codes"]),
        "enrichment_included": bool(brief.get("enrichment")),
        "catalog_status": "draft",
        "version": 1,
        "visibility": "private",
        "brief_id": brief["brief_id"],
    }
    cat_storage.courses.update_course(course_uid, course)

    tag_concept_standards(storage, course_uid, brief, llm_json=llm_json)
    logger.info(f"Catalog build complete: {course_uid} (draft)")
    return course_uid


def tag_concept_standards(storage, course_uid, brief, llm_json=None):
    """Spec 04 §3.4: hint-map regex tags (deterministic, tag_source=human),
    then optional LLM-assisted draft tags (flagged, must be confirmed before
    publish — never publish an LLM-only tag)."""
    course = storage.catalog_courses.get_course(course_uid)
    concepts = storage.catalog_courses.get_flat_concepts(course_uid)
    hint_map = brief.get("concept_standard_map") or {}   # regex → {code, coverage}

    mappings, concept_uids = [], []
    tagged = {}
    for con in concepts:
        concept_uids.append(con["uid"])
        title = con.get("title", "")
        for pattern, spec in hint_map.items():
            if re.search(pattern, title, re.IGNORECASE):
                tagged.setdefault(con["uid"], []).append(
                    {"standard_code": spec["standard_code"],
                     "coverage": spec.get("coverage", "full"),
                     "tag_source": "human"})

    if llm_json:
        candidates = ", ".join(brief["standard_codes"])
        for con in concepts:
            if con["uid"] in tagged:
                continue
            verdict = llm_json(
                "You map K-12 course concepts to Utah standards codes. "
                "Output ONLY JSON.",
                (f"CONCEPT: {con.get('title', '')}\n"
                 f"OBJECTIVES: {con.get('learning_objectives', [])}\n"
                 f"CANDIDATE CODES: {candidates}\n"
                 'Which code does this concept cover, at what coverage? '
                 '{"standard_code": "...", "coverage": "full|partial|enrichment"}'),
                {"type": "object",
                 "properties": {"standard_code": {"type": "string"},
                                "coverage": {"type": "string",
                                             "enum": ["full", "partial", "enrichment"]}},
                 "required": ["standard_code", "coverage"]})
            if verdict and verdict.get("standard_code") in brief["standard_codes"]:
                tagged[con["uid"]] = [{**verdict, "tag_source": "llm"}]

    def _apply(node):
        for mod in node.get("modules", []):
            for unit in mod.get("units", []):
                for lesson in unit.get("lessons", []):
                    for con in lesson.get("concepts", []):
                        if con["uid"] in tagged:
                            con["concept_standards"] = tagged[con["uid"]]
    _apply(course)
    storage.catalog_courses.update_course(course_uid, course)

    for uid, tags in tagged.items():
        for t in tags:
            mappings.append({"concept_uid": uid,
                             "standard_code": t["standard_code"],
                             "coverage": t["coverage"]})
    storage.standards.sync_concept_standards(concept_uids, mappings)
    return tagged


# ---------------------------------------------------------- review workflow

def _iter_concepts(course):
    for mod in (course or {}).get("modules", []):
        for unit in mod.get("units", []):
            for lesson in unit.get("lessons", []):
                for con in lesson.get("concepts", []):
                    yield con


def transition_catalog_course(storage, course_uid, action, actor=None, note=None):
    """B26.2 state machine: draft → reviewed → published → retired, with the
    spec 04 §4.1 guards enforced server-side. Returns the new status.
    Raises ValueError on any guard failure."""
    course = storage.catalog_courses.get_course(course_uid)
    if not course or not (course.get("catalog") or {}).get("is_catalog"):
        raise ValueError("not a catalog course")
    cat = course["catalog"]
    status = cat.get("catalog_status", "draft")

    if action == "submit_for_review":
        if status != "draft":
            raise ValueError(f"submit_for_review requires draft (is {status})")
        concepts = list(_iter_concepts(course))
        if not concepts:
            raise ValueError("course has no concepts")
        untagged = [c["uid"] for c in concepts
                    if not c.get("concept_standards")
                    and not any(t.get("coverage") == "enrichment"
                                for t in c.get("concept_standards", []))]
        if untagged:
            raise ValueError(f"{len(untagged)} concept(s) missing standard tags: "
                             f"{untagged[:5]}")
        if course.get("status") not in ("ready", "available"):
            raise ValueError(f"course not fully hydrated (status={course.get('status')})")
        cat["catalog_status"] = "reviewed"

    elif action == "approve":
        if status != "reviewed":
            raise ValueError(f"approve requires reviewed (is {status})")
        if not actor:
            raise ValueError("reviewer identity required")
        cat["reviewed_by"] = actor

    elif action == "publish":
        if status != "reviewed":
            raise ValueError(f"publish requires reviewed (is {status})")
        if not cat.get("reviewed_by"):
            raise ValueError("approve (reviewer sign-off) required before publish")
        # every brief standard covered by >=1 full/partial concept; no
        # unconfirmed LLM-only tags (spec 04 §3.4 / §4.1)
        covered = set()
        for con in _iter_concepts(course):
            for t in con.get("concept_standards", []):
                if t.get("tag_source") == "llm":
                    raise ValueError(f"unconfirmed LLM tag on {con['uid']} — "
                                     "reviewer must confirm before publish")
                if t.get("coverage") in ("full", "partial"):
                    covered.add(t["standard_code"])
        missing = [c for c in cat.get("standard_codes", []) if c not in covered]
        if missing:
            raise ValueError(f"standards not covered by any concept: {missing}")
        version = int(cat.get("version") or 1)
        cat["catalog_status"] = "published"
        cat["visibility"] = "catalog"
        cat["published_at"] = datetime.utcnow().isoformat()
        _snapshot_version(storage, course_uid, course, version, actor, note)

    elif action == "request_changes":
        if status != "reviewed":
            raise ValueError(f"request_changes requires reviewed (is {status})")
        if not note:
            raise ValueError("reviewer note required")
        cat["catalog_status"] = "draft"
        cat["review_note"] = note

    elif action == "revise":
        # spec 04 §5.3 step 1: published is immutable — begin a new version's
        # working copy by dropping back to draft (same uid; version bumps on
        # the eventual republish)
        if status != "published":
            raise ValueError(f"revise requires published (is {status})")
        cat["catalog_status"] = "draft"
        cat["visibility"] = "private"
        cat.pop("reviewed_by", None)   # new version needs fresh sign-off

    elif action == "retire":
        if status != "published":
            raise ValueError(f"retire requires published (is {status})")
        cat["catalog_status"] = "retired"
        cat["visibility"] = "private"

    else:
        raise ValueError(f"unknown action {action!r}")

    storage.catalog_courses.update_course(course_uid, course)
    try:
        storage.audit.record(f"catalog_{action}", actor_id=actor,
                             actor_role="admin",
                             detail={"course_uid": course_uid, "note": note})
    except Exception:
        pass
    return cat["catalog_status"]


def _snapshot_version(storage, course_uid, course, version, actor, note):
    """B26.3: immutable per-publish snapshot + append-only changelog so a
    pinned enrollment can always read the exact content it started on."""
    course_dir = os.path.join(storage.catalog_dir, course_uid)
    versions_dir = os.path.join(course_dir, "versions")
    os.makedirs(versions_dir, exist_ok=True)
    with open(os.path.join(versions_dir, f"v{version}.json"), "w") as f:
        json.dump(course, f, indent=2)
    changelog = os.path.join(course_dir, "CHANGELOG.md")
    stamp = datetime.utcnow().isoformat()
    entry = (f"## v{version} — {stamp} — {actor or 'system'}\n"
             f"- {note or 'published'}\n\n")
    with open(changelog, "a") as f:
        f.write(entry)


def republish(storage, course_uid, actor=None, note=None):
    """B26.3 §5.3: a republish is a NEW VERSION of the same uid. Bumps
    version, snapshots, republishes. In-progress enrollments stay pinned to
    their course_version; new enrollments get the new one."""
    course = storage.catalog_courses.get_course(course_uid)
    cat = (course or {}).get("catalog") or {}
    if cat.get("catalog_status") != "reviewed":
        raise ValueError("republish requires the edited copy back in reviewed state")
    cat["version"] = int(cat.get("version") or 1) + 1
    storage.catalog_courses.update_course(course_uid, course)
    return transition_catalog_course(storage, course_uid, "publish",
                                     actor=actor, note=note)


def get_version_snapshot(storage, course_uid, version):
    """Read a pinned enrollment's snapshot; falls back to the live structure
    (v1 legacy without snapshots)."""
    path = os.path.join(storage.catalog_dir, course_uid, "versions",
                        f"v{int(version)}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return storage.catalog_courses.get_course(course_uid)


# ----------------------------------------------------------------------- CLI

def main(argv=None):
    import argparse
    from services.common.storage import StorageManager

    parser = argparse.ArgumentParser(prog="catalog_admin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_std = sub.add_parser("standards")
    p_std.add_argument("action", choices=["load"])
    p_std.add_argument("--prune", action="store_true")
    p_std.add_argument("--path", default="data/standards")

    p_build = sub.add_parser("build")
    p_build.add_argument("brief", help="brief file or directory")

    args = parser.parse_args(argv)
    storage = StorageManager(os.getenv("DATA_ROOT", "/app/data"))

    if args.cmd == "standards":
        from services.common.standards_loader import load_standards_dir
        result = load_standards_dir(storage, args.path, prune=args.prune)
        print(json.dumps(result, indent=2))
        return 1 if result["errors"] else 0

    if args.cmd == "build":
        paths = ([os.path.join(args.brief, f) for f in sorted(os.listdir(args.brief))
                  if f.endswith((".yaml", ".yml", ".json"))]
                 if os.path.isdir(args.brief) else [args.brief])
        for path in paths:   # sequential — Mac Mini constraint (spec 04 §3.6)
            brief = load_brief(path)
            uid = build_from_brief(storage, brief,
                                   status_callback=lambda m: print(f"  {m}"))
            print(f"built {brief['brief_id']} → {uid} (draft)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
