import os
import shutil
import json
import sqlite3
import logging

logger = logging.getLogger(__name__)

# Courses with these statuses did not finish building.
#
# THEY ARE NOT AUTOMATICALLY GARBAGE. Hydration is resumable — it skips every
# concept that already has content — so a course stopped at 60 of 100 concepts
# is hours of model time that only needs finishing, and the courses list now
# offers a "Resume build" button to finish it.
#
# This module used to delete all four states outright, directory and SQLite row,
# every time the RAG service imported. Two things followed, both measured:
#
#   * The resume button was unreachable for most of them. The course was gone
#     before anyone could click it.
#   * An interrupted build was destroyed by the very mechanism meant to rescue
#     it: the stale-build reaper marks an abandoned build "failed" so it stops
#     showing as in progress, and the next RAG start then deleted it.
#
# On 2026-08-24 a 101-concept course sat at "skeleton" for five and a half
# hours while it hydrated. Starting the stack during that window would have
# deleted all of it, silently, with no prompt and no backup.
#
# So the status alone no longer decides. See `_has_recoverable_work`.
INCOMPLETE_STATUSES = {"failed", "hydration_failed", "building", "skeleton"}


def _has_recoverable_work(course_dir):
    """Has anything been hydrated here that resuming would keep?

    One written concept is the threshold, because that is exactly what
    hydration will skip on a resume: the cost of preserving a course that
    turns out to be useless is one stale directory the user can delete, and
    the cost of deleting one wrongly is hours of generation that cannot be
    recovered.
    """
    content_dir = os.path.join(course_dir, "content")
    try:
        return any(n.endswith(".md") for n in os.listdir(content_dir))
    except OSError:
        return False


def clean_failed_courses(data_dir: str = "/app/data"):
    """
    Scans the data/courses directory and removes any course folders that are
    incomplete (status != 'ready') or missing structure.json entirely.

    Also cleans up corresponding SQLite metadata entries to prevent orphaned rows.
    """
    courses_dir = os.path.join(data_dir, "courses")
    if not os.path.exists(courses_dir):
        logger.info(f"Courses directory {courses_dir} does not exist. Skipping cleanup.")
        return

    db_path = os.path.join(data_dir, "helga.db")
    removed_count = 0
    preserved_count = 0

    for name in os.listdir(courses_dir):
        course_dir = os.path.join(courses_dir, name)
        if not os.path.isdir(course_dir):
            continue

        structure_path = os.path.join(course_dir, "structure.json")
        should_remove = False
        status = "unknown"

        if not os.path.exists(structure_path):
            # No structure.json = incomplete build, remove
            should_remove = True
            status = "no_structure"
        else:
            try:
                with open(structure_path, "r") as f:
                    course = json.load(f)

                status = course.get("status", "unknown")
                if status in INCOMPLETE_STATUSES:
                    # Deleting is now the LAST resort, not the default.
                    if _has_recoverable_work(course_dir):
                        logger.warning(
                            f"Course '{name}' is '{status}' but has hydrated "
                            f"content — preserving it so it can be resumed "
                            f"rather than rebuilt.")
                    else:
                        should_remove = True
                elif status != "ready":
                    # Unknown status — preserve but log warning
                    logger.warning(f"Course '{name}' has unknown status '{status}', preserving.")
            except (json.JSONDecodeError, IOError) as e:
                # Corrupted structure.json — remove
                should_remove = True
                status = f"corrupted ({e})"

        if should_remove:
            logger.info(f"Auto-cleaner removing course '{name}' (status: {status})")
            try:
                shutil.rmtree(course_dir)
                removed_count += 1

                # Also clean up SQLite metadata
                _cleanup_sqlite(db_path, name)

            except Exception as rm_err:
                logger.error(f"Failed to remove directory {course_dir}: {rm_err}")
        else:
            preserved_count += 1

    if removed_count > 0:
        logger.info(f"Auto-cleaner: {removed_count} removed, {preserved_count} preserved.")
    else:
        logger.debug(f"Auto-cleaner: No incomplete courses found. {preserved_count} preserved.")


def _cleanup_sqlite(db_path: str, course_uid: str):
    """Remove orphaned SQLite records for a deleted course."""
    if not os.path.exists(db_path):
        return

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            # Clean courses metadata table
            cursor.execute("DELETE FROM courses WHERE uid = ?", (course_uid,))
            # Clean flashcards for this course
            cursor.execute("DELETE FROM flashcards WHERE course_uid = ?", (course_uid,))
            # Clean user_progress for this course
            cursor.execute("DELETE FROM user_progress WHERE course_uid = ?", (course_uid,))
            # Clean activity_log for this course
            cursor.execute("DELETE FROM activity_log WHERE course_uid = ?", (course_uid,))
            # Clean scheduled_reviews for this course
            cursor.execute("DELETE FROM scheduled_reviews WHERE course_uid = ?", (course_uid,))
            conn.commit()
            logger.debug(f"Cleaned up SQLite records for course '{course_uid}'")
    except Exception as e:
        logger.warning(f"Failed to clean SQLite records for course '{course_uid}': {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    clean_failed_courses()
