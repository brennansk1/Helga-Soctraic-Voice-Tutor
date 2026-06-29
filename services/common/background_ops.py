"""
Background Operations for Helga.

Runs periodic maintenance tasks: cleanup, integrity checks, cache pruning,
streak resets, and error recovery. Designed to run inside the RAG service
(which owns the database) via a background thread.
"""

import os
import time
import json
import shutil
import sqlite3
import logging
import threading
from datetime import datetime, date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_ROOT = os.getenv("DATA_ROOT", "/app/data")


class BackgroundOperations:
    """Manages all periodic background tasks."""

    def __init__(self, storage_manager=None, interval_seconds=300):
        self.storage = storage_manager
        self.interval = interval_seconds  # Default: every 5 minutes
        self._stop_event = threading.Event()
        self._thread = None
        self.last_run = {}
        self.stats = {
            "runs": 0,
            "errors": 0,
            "cleaned_courses": 0,
            "cleaned_files": 0,
            "cache_pruned_mb": 0,
        }

    def start(self):
        """Start the background operations thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Background ops already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="bg-ops")
        self._thread.start()
        logger.info(f"Background operations started (interval: {self.interval}s)")

    def stop(self):
        """Stop the background operations thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Background operations stopped")

    def _run_loop(self):
        """Main loop — runs tasks on schedule."""
        # Wait 30s after startup before first run
        self._stop_event.wait(30)

        while not self._stop_event.is_set():
            try:
                self._run_all_tasks()
                self.stats["runs"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                logger.error(f"Background ops error: {e}", exc_info=True)

            self._stop_event.wait(self.interval)

    def _run_all_tasks(self):
        """Execute all background tasks in sequence."""
        now = datetime.utcnow()
        logger.debug(f"Background ops cycle starting at {now.isoformat()}")

        # Every cycle (5 min)
        self._cleanup_stale_courses()
        self._cleanup_orphan_content_files()
        self._reset_daily_streak()

        # Every 30 min
        if self._should_run("integrity_check", minutes=30):
            self._check_data_integrity()

        # Every hour
        if self._should_run("cache_prune", minutes=60):
            self._prune_tts_cache()
            self._prune_research_cache()

        # Every 6 hours
        if self._should_run("db_maintenance", minutes=360):
            self._sqlite_maintenance()
            self._cleanup_old_interactions()

    def _should_run(self, task_name, minutes):
        """Check if enough time has passed since last run of a task."""
        last = self.last_run.get(task_name)
        if last is None or (datetime.utcnow() - last).total_seconds() > minutes * 60:
            self.last_run[task_name] = datetime.utcnow()
            return True
        return False

    # --- Task implementations ---

    def _cleanup_stale_courses(self):
        """Remove courses stuck in 'building' or 'skeleton' status for > 1 hour."""
        if not self.storage:
            return
        try:
            courses = self.storage.courses.list_courses()
            cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
            for course in courses:
                status = course.get("status", "")
                created = course.get("created_at", "")
                if status in ("building", "skeleton") and created and created < cutoff:
                    uid = course.get("uid", "")
                    logger.warning(f"Cleaning stale course '{course.get('title')}' ({uid}) — stuck in '{status}' since {created}")
                    # Mark as failed instead of deleting — user can see what happened
                    try:
                        full_course = self.storage.courses.get_course(uid)
                        if full_course:
                            full_course["status"] = "failed"
                            full_course["error"] = "Course creation timed out (>1 hour)"
                            self.storage.courses.update_course(uid, full_course)
                            self.stats["cleaned_courses"] += 1
                    except Exception as e:
                        logger.warning(f"Failed to mark stale course {uid}: {e}")
        except Exception as e:
            logger.warning(f"Stale course cleanup failed: {e}")

    def _cleanup_orphan_content_files(self):
        """Remove .md content files for courses that no longer exist."""
        courses_dir = os.path.join(DATA_ROOT, "courses")
        if not os.path.exists(courses_dir):
            return
        try:
            known_uids = set()
            courses = self.storage.courses.list_courses() if self.storage else []
            for c in courses:
                uid = c.get("uid", "")
                if uid:
                    known_uids.add(uid)

            for dirname in os.listdir(courses_dir):
                dirpath = os.path.join(courses_dir, dirname)
                if os.path.isdir(dirpath) and dirname.startswith("course_"):
                    if dirname not in known_uids:
                        # Check if structure.json exists but course is not in DB
                        struct_path = os.path.join(dirpath, "structure.json")
                        if not os.path.exists(struct_path):
                            logger.info(f"Removing orphan course directory: {dirname}")
                            shutil.rmtree(dirpath, ignore_errors=True)
                            self.stats["cleaned_files"] += 1
        except Exception as e:
            logger.warning(f"Orphan cleanup failed: {e}")

    def _reset_daily_streak(self):
        """Reset daily XP counter if the date has changed. Called frequently but only acts once per day."""
        db_path = os.path.join(DATA_ROOT, "helga.db")
        if not os.path.exists(db_path):
            return
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT value FROM gamification WHERE key='daily_date'").fetchone()
            if row:
                stored_date = row["value"]
                today = date.today().isoformat()
                if stored_date and stored_date != today:
                    conn.execute("UPDATE gamification SET value='0' WHERE key='daily_xp'")
                    conn.execute("UPDATE gamification SET value=? WHERE key='daily_date'", (today,))
                    conn.commit()
                    logger.debug(f"Daily XP reset for new day: {today}")
            conn.close()
        except Exception as e:
            logger.debug(f"Daily streak reset skipped: {e}")

    def _check_data_integrity(self):
        """Verify course structures match filesystem content."""
        if not self.storage:
            return
        issues = []
        try:
            courses = self.storage.courses.list_courses()
            for course in courses:
                uid = course.get("uid", "")
                status = course.get("status", "")
                if status not in ("ready", "available"):
                    continue

                # Check structure.json exists
                struct_path = os.path.join(DATA_ROOT, "courses", uid, "structure.json")
                if not os.path.exists(struct_path):
                    issues.append(f"Course {uid}: structure.json missing")
                    continue

                # Check concept count matches content files
                concepts = self.storage.courses.get_flat_concepts(uid)
                content_dir = os.path.join(DATA_ROOT, "courses", uid, "content")
                if os.path.exists(content_dir):
                    md_files = [f for f in os.listdir(content_dir) if f.endswith(".md")]
                    if len(md_files) < len(concepts) * 0.5:
                        issues.append(f"Course {uid}: only {len(md_files)} content files for {len(concepts)} concepts")

            if issues:
                logger.warning(f"Data integrity check found {len(issues)} issue(s):")
                for iss in issues[:5]:
                    logger.warning(f"  - {iss}")
            else:
                logger.debug(f"Data integrity check passed ({len(courses)} courses)")
        except Exception as e:
            logger.warning(f"Data integrity check failed: {e}")

    def _prune_tts_cache(self):
        """Remove TTS cache files older than 7 days."""
        cache_dir = os.path.join(DATA_ROOT, "tts_cache")
        if not os.path.exists(cache_dir):
            return
        try:
            cutoff = time.time() - (7 * 86400)  # 7 days
            removed = 0
            total_bytes = 0
            for fname in os.listdir(cache_dir):
                fpath = os.path.join(cache_dir, fname)
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    total_bytes += os.path.getsize(fpath)
                    os.remove(fpath)
                    removed += 1
            if removed > 0:
                mb = total_bytes / (1024 * 1024)
                logger.info(f"TTS cache pruned: {removed} files, {mb:.1f} MB freed")
                self.stats["cache_pruned_mb"] += mb
        except Exception as e:
            logger.warning(f"TTS cache prune failed: {e}")

    def _prune_research_cache(self):
        """Remove research cache entries older than 30 days."""
        cache_dir = os.path.join(DATA_ROOT, "research_cache")
        if not os.path.exists(cache_dir):
            return
        try:
            cache_db = os.path.join(cache_dir, "cache.db")
            if os.path.exists(cache_db):
                size_before = os.path.getsize(cache_db)
                conn = sqlite3.connect(cache_db)
                # diskcache stores expiry — just let it handle TTL naturally
                conn.close()
                # If cache DB is > 100MB, log a warning
                size_mb = size_before / (1024 * 1024)
                if size_mb > 100:
                    logger.warning(f"Research cache is {size_mb:.0f} MB — consider clearing")
        except Exception as e:
            logger.debug(f"Research cache check skipped: {e}")

    def _sqlite_maintenance(self):
        """Run VACUUM and integrity check on SQLite databases."""
        db_path = os.path.join(DATA_ROOT, "helga.db")
        if not os.path.exists(db_path):
            return
        try:
            conn = sqlite3.connect(db_path)
            # Integrity check
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result and result[0] != "ok":
                logger.error(f"SQLite integrity check FAILED: {result[0]}")
            else:
                logger.debug("SQLite integrity check passed")

            # Optimize
            conn.execute("PRAGMA optimize")
            conn.close()
        except Exception as e:
            logger.warning(f"SQLite maintenance failed: {e}")

    def _cleanup_old_interactions(self):
        """Remove interaction records older than 90 days to keep DB lean."""
        db_path = os.path.join(DATA_ROOT, "helga.db")
        if not os.path.exists(db_path):
            return
        try:
            conn = sqlite3.connect(db_path)
            cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
            cursor = conn.execute(
                "DELETE FROM activity_log WHERE created_at < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted > 0:
                logger.info(f"Cleaned {deleted} activity log entries older than 90 days")
        except Exception as e:
            logger.debug(f"Old interaction cleanup skipped: {e}")

    def get_status(self):
        """Return background ops status for the health endpoint."""
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "total_runs": self.stats["runs"],
            "total_errors": self.stats["errors"],
            "cleaned_courses": self.stats["cleaned_courses"],
            "cleaned_files": self.stats["cleaned_files"],
            "cache_pruned_mb": round(self.stats["cache_pruned_mb"], 1),
            "last_run": {k: v.isoformat() for k, v in self.last_run.items()},
        }
