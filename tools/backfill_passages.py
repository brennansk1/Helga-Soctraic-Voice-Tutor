#!/usr/bin/env python3
"""Refill sources.passage for a course built before passages were retained.

`_citation()` dropped the source text, so 529 of 529 rows were stored with an
empty passage and nothing downstream could verify a claim against anything.
That is fixed for new builds; this recovers it for courses already on disk.

It re-asks the research service rather than reading the cache: the cache is
keyed by a hash of the query, expires in 24h/7d, and cannot be mapped back to
a concept. Re-asking is slower and complete.

    python3 tools/backfill_passages.py <course_uid> [--limit N] [--dry-run]
"""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from services.core.course_audit import walk_concepts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RESEARCH = os.getenv("RESEARCH_URL", "http://localhost:5006")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("course_uid")
    p.add_argument("--limit", type=int, default=0,
                   help="stop after N concepts (0 = all)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    struct = json.load(open(os.path.join(
        DATA, "courses", a.course_uid, "structure.json"), encoding="utf-8"))
    course_title = struct.get("title") or ""
    mastery = struct.get("mastery") or struct.get("mastery_level") or 3

    db = sqlite3.connect(os.path.join(DATA, "helga.db"))
    need = {r[0] for r in db.execute(
        "SELECT DISTINCT concept_uid FROM sources WHERE course_uid=? AND "
        "(passage IS NULL OR length(trim(passage))<50)", (a.course_uid,))}
    print(f"{course_title}: {len(need)} concept(s) with sources but no passage")

    todo = []
    for concept, path in walk_concepts(struct):
        if concept.get("uid") in need:
            todo.append((concept, path))
    if a.limit:
        todo = todo[:a.limit]
    print(f"backfilling {len(todo)}\n")

    filled = missed = 0
    for i, (concept, path) in enumerate(todo, 1):
        uid, title = concept.get("uid"), concept.get("title") or ""
        t0 = time.time()
        try:
            r = requests.post(f"{RESEARCH}/api/research_concept", json={
                "title": title, "module_title": path[0] if path else "",
                "course_title": course_title, "mastery": mastery,
            }, timeout=120)
            data = r.json() if r.status_code == 200 else {}
        except Exception as e:
            print(f"  {i}/{len(todo)} {title[:38]:40} ERROR {str(e)[:40]}")
            missed += 1
            continue

        rows = (data.get("sources") or []) + (data.get("evidence_sources") or [])
        by_url = {(s.get("url") or ""): (s.get("passage") or "")
                  for s in rows if isinstance(s, dict)}
        wrote = added = 0
        if not a.dry_run:
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            for s_row in rows:
                url = (s_row.get("url") or "").strip()
                passage = (s_row.get("passage") or "").strip()
                if not url or len(passage) < 50:
                    continue
                cur = db.execute(
                    "UPDATE sources SET passage=? WHERE course_uid=? AND "
                    "concept_uid=? AND url=? AND "
                    "(passage IS NULL OR length(trim(passage))<50)",
                    (passage[:4000], a.course_uid, uid, url))
                if cur.rowcount:
                    wrote += cur.rowcount
                    continue
                # A URL THE ORIGINAL BUILD DID NOT CITE.
                #
                # Research is not deterministic — the web moves, the ranking
                # moves — so most of a re-fetch comes back with different URLs
                # than the build stored. Discarding those leaves the concept
                # with no passage at all and nothing to verify against, which
                # is the situation this tool exists to end.
                #
                # They are stored as EVIDENCE (cited=0): usable by a fact
                # check, never rendered to a learner as a citation for text
                # the model never saw.
                exists = db.execute(
                    "SELECT 1 FROM sources WHERE course_uid=? AND "
                    "concept_uid=? AND url=?",
                    (a.course_uid, uid, url)).fetchone()
                if exists:
                    continue
                db.execute(
                    "INSERT INTO sources (course_uid, concept_uid, title, url, "
                    "passage, source_type, domain_tier, degraded, "
                    "retrieved_at, cited) VALUES (?,?,?,?,?,?,?,0,?,0)",
                    (a.course_uid, uid, s_row.get("title"), url,
                     passage[:4000], s_row.get("type"),
                     s_row.get("domain_tier"), now))
                added += 1
            db.commit()
        filled += wrote
        print(f"  {i}/{len(todo)} {title[:38]:40} {len(rows):2} source(s), "
              f"{wrote} filled + {added} as evidence  ({time.time()-t0:.0f}s)")

    print(f"\nwrote {filled} passage(s); {missed} concept(s) failed")
    left = db.execute(
        "SELECT count(*) FROM sources WHERE course_uid=? AND "
        "(passage IS NULL OR length(trim(passage))<50)", (a.course_uid,)).fetchone()[0]
    have = db.execute(
        "SELECT count(*) FROM sources WHERE course_uid=? AND "
        "length(trim(coalesce(passage,'')))>=50", (a.course_uid,)).fetchone()[0]
    print(f"course now: {have} source(s) with a passage, {left} still without")
    return 0


if __name__ == "__main__":
    sys.exit(main())
