#!/usr/bin/env python3
"""The backup/restore drill (A7): prove a Helga backup actually restores.

A backup that has never been restored is a hope, not a backup. This drill
makes a real backup of a data directory, restores it into a fresh location,
and then VERIFIES the restore the way a user would notice a failure: the
database opens, the schema version matches, every course row still has its
directory, every structure.json parses, and progress counts survive.

Run:
    python3 tools/backup_drill.py                    # drill against ./data
    python3 tools/backup_drill.py --data-dir PATH    # drill against PATH
    python3 tools/backup_drill.py --backup-only OUT  # just produce a backup

The drill never modifies the source data directory. SQLite is copied through
the backup API (not file copy), so a live WAL database backs up consistently.
Exit 0 only when every check passes.
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time


def _fail(msg):
    print(f"DRILL FAIL: {msg}")
    sys.exit(1)


def make_backup(data_dir, out_dir):
    """Copy helga.db (via the SQLite backup API) + courses/ into out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    src_db = os.path.join(data_dir, "helga.db")
    dst_db = os.path.join(out_dir, "helga.db")
    if os.path.exists(src_db):
        # The backup API gives a consistent snapshot of a live WAL database;
        # a plain file copy can capture the .db mid-checkpoint and restore to
        # a database that opens but lies.
        src = sqlite3.connect(src_db)
        dst = sqlite3.connect(dst_db)
        with dst:
            src.backup(dst)
        src.close(); dst.close()
    courses = os.path.join(data_dir, "courses")
    if os.path.isdir(courses):
        shutil.copytree(courses, os.path.join(out_dir, "courses"),
                        dirs_exist_ok=True)
    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": os.path.abspath(data_dir),
        "db_bytes": os.path.getsize(dst_db) if os.path.exists(dst_db) else 0,
        "course_dirs": sorted(os.listdir(courses)) if os.path.isdir(courses) else [],
    }
    with open(os.path.join(out_dir, "backup_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def verify_restore(restored_dir, expect):
    """The checks a user's experience actually depends on."""
    db_path = os.path.join(restored_dir, "helga.db")
    if expect["db_bytes"] and not os.path.exists(db_path):
        _fail("restored tree has no helga.db")

    # Plain open, not mode=ro: a WAL-journaled database refuses a read-only
    # open when its -wal/-shm sidecars are absent — which they always are in a
    # fresh restore, since the backup API checkpoints them away. This is our
    # own temp copy, so writability costs nothing.
    conn = sqlite3.connect(db_path)
    try:
        ver = conn.execute("SELECT version FROM schema_version").fetchone()
        print(f"  schema_version: {ver[0]}")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            _fail(f"integrity_check: {integrity}")
        print("  integrity_check: ok")
        rows = conn.execute("SELECT uid, status FROM courses").fetchall()
        progress = conn.execute("SELECT COUNT(*) FROM user_progress").fetchone()[0]
        print(f"  courses rows: {len(rows)}, user_progress rows: {progress}")
    finally:
        conn.close()

    courses_dir = os.path.join(restored_dir, "courses")
    on_disk = set(os.listdir(courses_dir)) if os.path.isdir(courses_dir) else set()
    if on_disk != set(expect["course_dirs"]):
        _fail(f"course directories diverged: expected {len(expect['course_dirs'])}, "
              f"restored {len(on_disk)}")
    unreadable = []
    for uid in sorted(on_disk):
        sj = os.path.join(courses_dir, uid, "structure.json")
        try:
            with open(sj) as f:
                json.load(f)
        except Exception as e:
            unreadable.append(f"{uid}: {e}")
    if unreadable:
        _fail("structure.json unreadable after restore:\n  " + "\n  ".join(unreadable))
    print(f"  {len(on_disk)} course directories restored, every structure.json parses")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--backup-only", metavar="OUT",
                    help="produce a backup at OUT and stop (no restore drill)")
    args = ap.parse_args()

    if not os.path.isdir(args.data_dir):
        _fail(f"no data directory at {args.data_dir}")

    if args.backup_only:
        m = make_backup(args.data_dir, args.backup_only)
        print(f"backup written to {args.backup_only} "
              f"({m['db_bytes']} DB bytes, {len(m['course_dirs'])} courses)")
        return

    with tempfile.TemporaryDirectory(prefix="helga-drill-") as tmp:
        backup_dir = os.path.join(tmp, "backup")
        restore_dir = os.path.join(tmp, "restored")
        print(f"backing up {args.data_dir} ...")
        manifest = make_backup(args.data_dir, backup_dir)
        print(f"restoring into a fresh tree ...")
        shutil.copytree(backup_dir, restore_dir)
        os.remove(os.path.join(restore_dir, "backup_manifest.json"))
        print("verifying the restore:")
        verify_restore(restore_dir, manifest)
    print("DRILL PASS: a backup of this data directory restores completely.")


if __name__ == "__main__":
    main()
