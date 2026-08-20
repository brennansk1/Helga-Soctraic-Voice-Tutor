#!/usr/bin/env python3
"""reconcile_courses.py — make the `courses` table and data/courses/ agree.

THE DIVERGENCE THIS SETTLES
---------------------------
A course lives in two stores at once: a row in the `courses` table of
helga.db, and a directory under data/courses/{uid}/ holding structure.json.
Nothing has ever checked that the two sets match. Measured against the live
data directory on 2026-08-19:

    courses table   19 rows
    data/courses/    3 directories

Sixteen rows describe courses that cannot be opened. The course list is built
from SQLite (CourseStore.list_courses reads the table, not the disk), so every
one of those sixteen is offered to the user and every one of them fails when
clicked. This is the failure mode CLAUDE.md names AUTO-10 — one store written
without the other — seen from its second, worse side: the row survived and the
content did not.

The two directions are NOT symmetrical, and this tool treats them differently:

    directory but no row   RECOVERABLE. structure.json is the content. The row
                           is derived metadata and can be rebuilt from it.
                           --fix re-registers these.

    row but no directory   UNRECOVERABLE. The row is a title and a status; the
                           course itself is gone and no amount of repair brings
                           it back. --fix removes these, because a row that
                           cannot open is worse than no row — but only for
                           statuses that were never finished (see below).

USAGE
-----
    python3 tools/reconcile_courses.py                    # dry run, ./data
    python3 tools/reconcile_courses.py /path/to/data      # dry run, elsewhere
    python3 tools/reconcile_courses.py --fix              # reconcile
    python3 tools/reconcile_courses.py --fix --include-ready

Exit code 0 when the stores agree (or after a successful --fix), 1 when a dry
run found divergence, 2 on error. Safe to run against a live database: the dry
run opens SQLite read-only so it *cannot* write, and --fix uses the same WAL +
busy-timeout settings as services/common/storage.py.
"""

import argparse
import json
import os
import sqlite3
import sys

# A row whose directory is gone is dead weight in every status except this one.
# `ready` means the course was fully built and hydrated at some point, so a
# missing directory there is not a leftover skeleton — it is data loss, and
# possibly a mounting or path mistake rather than a real absence. Deleting
# those rows on a mis-pointed --data-dir would destroy the only remaining
# record of what the user had. So `ready` is excluded from --fix by default and
# requires an explicit second flag.
PROTECTED_STATUSES = {"ready"}

# Kept in sync with CourseStore.create_course. Only columns that actually exist
# in the target database are used — older databases predate the catalog fields
# and re-registration must not fail on a schema that is merely older.
REGISTER_COLUMNS = (
    "uid", "title", "overview", "status", "teaching_style", "created_at",
    "subject", "grade_band", "grade_numeric", "is_catalog", "catalog_status",
    "version", "visibility", "reviewed_by", "published_at",
    "enrichment_included",
)


def default_data_dir() -> str:
    """The repo's own ./data, resolved from this file rather than the cwd.

    Tools in this directory are run from anywhere; resolving against cwd would
    silently point --fix at a data directory that happens to sit next to the
    shell, which for a tool that deletes rows is not an acceptable default.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "data")


def open_db(db_path: str, writable: bool) -> sqlite3.Connection:
    """Open helga.db the way the rest of the codebase does, plus read-only
    enforcement for the dry run.

    The dry run opens with mode=ro so that a bug in this tool cannot damage a
    live database — the guarantee is enforced by SQLite, not by our own care.
    busy_timeout matches storage.py's 30s: the web-ui, core, and rag services
    all hold connections to this file and a reconcile run must wait for them
    rather than failing with 'database is locked'.
    """
    if writable:
        conn = sqlite3.connect(db_path, timeout=30)
        # WAL is already the journal mode every service sets; asserting it here
        # keeps a fix run from downgrading a live database if it were ever
        # opened before the services had touched it.
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        uri = "file:" + os.path.abspath(db_path).replace("?", "%3f") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def scan_rows(conn: sqlite3.Connection) -> dict:
    """uid -> row dict for every course registered in SQLite."""
    rows = {}
    for row in conn.execute("SELECT * FROM courses"):
        rows[row["uid"]] = dict(row)
    return rows


def scan_dirs(courses_dir: str) -> tuple:
    """Scan data/courses/ into (readable, unreadable).

    `readable` maps uid -> parsed structure.json. `unreadable` maps uid ->
    reason. The split matters for --fix: only a directory whose structure.json
    actually parses can be re-registered, because the row's title and status
    are read out of it. A directory with a truncated or missing structure.json
    is reported and then left strictly alone — it may be a build in flight.
    """
    readable, unreadable = {}, {}
    if not os.path.isdir(courses_dir):
        return readable, unreadable

    for name in sorted(os.listdir(courses_dir)):
        course_dir = os.path.join(courses_dir, name)
        if not os.path.isdir(course_dir) or name.startswith("."):
            continue
        structure_path = os.path.join(course_dir, "structure.json")
        if not os.path.exists(structure_path):
            unreadable[name] = "no structure.json"
            continue
        try:
            with open(structure_path, "r") as f:
                structure = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            unreadable[name] = f"unreadable structure.json: {e}"
            continue
        if not isinstance(structure, dict):
            unreadable[name] = "structure.json is not an object"
            continue
        readable[name] = structure
    return readable, unreadable


def diff(rows: dict, readable: dict) -> tuple:
    """(rows with no directory, directories with no row), both uid-sorted."""
    orphan_rows = sorted(set(rows) - set(readable))
    orphan_dirs = sorted(set(readable) - set(rows))
    return orphan_rows, orphan_dirs


def register_dir(conn: sqlite3.Connection, uid: str, structure: dict) -> None:
    """Insert the missing `courses` row for a directory that has content.

    Values come from structure.json, which is authoritative here by definition:
    it is the store that survived. Only columns present in this database are
    written, so an older schema re-registers with the fields it has.
    """
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(courses)")}
    cat = structure.get("catalog") or {}
    values = {
        "uid": uid,
        "title": structure.get("title", ""),
        "overview": structure.get("overview", ""),
        # A directory we had to rediscover is by definition of unknown
        # provenance, so its status is taken from the file rather than assumed.
        "status": structure.get("status", "unknown"),
        "teaching_style": structure.get("teaching_style", ""),
        "created_at": structure.get("created_at"),
        "subject": cat.get("subject"),
        "grade_band": cat.get("grade_band"),
        "grade_numeric": cat.get("grade_numeric"),
        "is_catalog": 1 if cat.get("is_catalog") else 0,
        "catalog_status": cat.get("catalog_status", "draft"),
        "version": cat.get("version", 1),
        "visibility": cat.get("visibility", "private"),
        "reviewed_by": cat.get("reviewed_by"),
        "published_at": cat.get("published_at"),
        "enrichment_included": 1 if cat.get("enrichment_included") else 0,
    }
    cols = [c for c in REGISTER_COLUMNS if c in existing]
    # created_at has a DEFAULT datetime('now'); omit it rather than writing a
    # NULL over the default when structure.json never recorded one.
    if "created_at" in cols and values["created_at"] is None:
        cols.remove("created_at")
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO courses ({', '.join(cols)}) VALUES ({placeholders})",
        [values[c] for c in cols],
    )


def remove_row(conn: sqlite3.Connection, uid: str) -> int:
    """Delete a dead course row and everything keyed to it.

    Mirrors CourseStore.delete_course's cascade list. Leaving progress,
    flashcards, and FTS entries behind for a course that no longer exists is
    how aggregate queries end up counting content nobody can reach. Nothing on
    disk is touched — this tool never deletes files, only rows.
    """
    cascade = [
        ("courses", "uid"),
        ("user_progress", "course_uid"),
        ("flashcards", "course_uid"),
        ("scheduled_reviews", "course_uid"),
        ("activity_log", "course_uid"),
        ("concept_fts", "course_uid"),
        ("concept_vec", "course_uid"),
        ("hydration_provenance", "course_uid"),
    ]
    total = 0
    for table, col in cascade:
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE {col}=?", (uid,))
            total += cur.rowcount if cur.rowcount > 0 else 0
        except sqlite3.OperationalError:
            # Table or column absent in an older schema — nothing to cascade.
            pass
    return total


def reconcile(data_dir: str, apply_fix: bool = False,
              include_ready: bool = False, out=sys.stdout) -> dict:
    """Report divergence, and repair it when apply_fix is set.

    Returns a summary dict so tests and callers can assert on the outcome
    without parsing the printed report.
    """
    db_path = os.path.join(data_dir, "helga.db")
    courses_dir = os.path.join(data_dir, "courses")

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"no helga.db under {data_dir}")

    conn = open_db(db_path, writable=apply_fix)
    try:
        rows = scan_rows(conn)
        readable, unreadable = scan_dirs(courses_dir)
        orphan_rows, orphan_dirs = diff(rows, readable)

        print(f"data dir : {data_dir}", file=out)
        print(f"rows     : {len(rows)} in `courses`", file=out)
        print(f"dirs     : {len(readable)} readable"
              f"{f', {len(unreadable)} unreadable' if unreadable else ''}"
              f" under courses/", file=out)
        print("", file=out)

        registered, removed, protected = [], [], []

        print(f"[A] rows with no directory: {len(orphan_rows)}", file=out)
        for uid in orphan_rows:
            row = rows[uid]
            status = (row.get("status") or "unknown")
            title = (row.get("title") or "")[:50]
            guarded = status in PROTECTED_STATUSES and not include_ready
            mark = "PROTECTED" if guarded else ("remove" if apply_fix else "dead")
            print(f"    {uid}  [{status:<10}] {title}   -> {mark}", file=out)
            if not apply_fix:
                continue
            if guarded:
                protected.append(uid)
                continue
            if status in PROTECTED_STATUSES:
                # Deleting the last record of a finished course is the one
                # destructive thing this tool can do. It never happens quietly.
                print(f"    !! REMOVING A `ready` COURSE ROW: {uid} "
                      f"title={row.get('title')!r} created_at="
                      f"{row.get('created_at')!r} — its structure.json is "
                      f"absent from {courses_dir}. The course content is NOT "
                      f"recoverable from this row.", file=out)
            n = remove_row(conn, uid)
            removed.append(uid)
            print(f"       removed {n} row(s) across course tables", file=out)

        print("", file=out)
        print(f"[B] directories with no row: {len(orphan_dirs)}", file=out)
        for uid in orphan_dirs:
            structure = readable[uid]
            title = (structure.get("title") or "")[:50]
            status = structure.get("status", "unknown")
            action = "re-register" if apply_fix else "unregistered"
            print(f"    {uid}  [{status:<10}] {title}   -> {action}", file=out)
            if apply_fix:
                register_dir(conn, uid, structure)
                registered.append(uid)

        if unreadable:
            print("", file=out)
            print(f"[C] directories skipped (never touched): {len(unreadable)}",
                  file=out)
            for uid, reason in sorted(unreadable.items()):
                print(f"    {uid}  {reason}", file=out)

        if apply_fix:
            conn.commit()

        print("", file=out)
        if apply_fix:
            print(f"FIXED: re-registered {len(registered)}, "
                  f"removed {len(removed)}, "
                  f"left {len(protected)} protected `ready` row(s) alone.",
                  file=out)
            if protected:
                print("       Re-run with --include-ready to remove those too, "
                      "after confirming the data directory is the right one.",
                      file=out)
        elif orphan_rows or orphan_dirs:
            print(f"DIVERGENCE: {len(orphan_rows)} row(s) cannot open, "
                  f"{len(orphan_dirs)} directory(ies) invisible. "
                  f"Re-run with --fix to reconcile.", file=out)
        else:
            print("OK: every row has a directory and every directory has a row.",
                  file=out)

        return {
            "rows": len(rows),
            "dirs": len(readable),
            "orphan_rows": orphan_rows,
            "orphan_dirs": orphan_dirs,
            "unreadable": unreadable,
            "registered": registered,
            "removed": removed,
            "protected": protected,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile the `courses` SQLite table with data/courses/.")
    parser.add_argument("data_dir", nargs="?", default=default_data_dir(),
                        help="data directory (default: the repo's ./data)")
    parser.add_argument("--fix", action="store_true",
                        help="apply the reconciliation (default is a dry run)")
    parser.add_argument("--include-ready", action="store_true",
                        help="also remove orphaned rows whose status is "
                             "`ready` (destructive; each one is announced)")
    args = parser.parse_args()

    if args.include_ready and not args.fix:
        print("--include-ready has no effect without --fix", file=sys.stderr)

    try:
        summary = reconcile(args.data_dir, apply_fix=args.fix,
                            include_ready=args.include_ready)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except sqlite3.Error as e:
        print(f"error: sqlite: {e}", file=sys.stderr)
        return 2

    if args.fix:
        return 0
    return 1 if (summary["orphan_rows"] or summary["orphan_dirs"]) else 0


if __name__ == "__main__":
    sys.exit(main())
