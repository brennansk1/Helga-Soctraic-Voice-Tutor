import os
import shutil
import json
import sqlite3
import logging
import time

logger = logging.getLogger(__name__)

# Courses with these statuses did not finish building.
#
# TWO INDEPENDENT FIXES FOR THE SAME DANGER, AND BOTH ARE KEPT.
#
# This set used to include "building" and "skeleton", which is what a LIVE
# build looks like — a course sits in "skeleton" for the whole of hydration —
# so a librarian restart mid-build deleted the course out from under the
# running hydrator. Narrowing the set to genuinely terminal states fixes that.
#
# But status alone is still not enough. Hydration is resumable: it skips every
# concept that already has content, so a course stopped at 60 of 100 concepts
# is hours of model time that only needs finishing, and the courses list now
# offers a "Resume build" button to finish it. Deleting a "failed" course
# throws that away and makes the button unreachable — and the stale-build
# reaper MARKS abandoned builds "failed", so the mechanism meant to rescue an
# interrupted build was feeding it to the one that destroyed it.
#
# On 2026-08-24 a 101-concept course sat at "skeleton" for five and a half
# hours while it hydrated. Under the old set, starting the stack during that
# window would have deleted all of it, silently, with no prompt and no backup.
#
# So: only terminal states are collectable at all, and among those, only the
# ones with nothing to lose. See `_has_recoverable_work`.
INCOMPLETE_STATUSES = {"failed", "hydration_failed"}


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
                # An unparseable structure.json is NOT evidence that the course
                # is junk. The likeliest cause is a torn write — the file was
                # being rewritten at the instant we read it — and every concept
                # markdown under content/ is still perfectly good. Deleting the
                # whole directory on that signal destroys a finished course.
                # An IOError is weaker still: transient (permissions, EMFILE)
                # and says nothing at all about the contents.
                #
                # Quarantine instead: copy the unreadable file aside for
                # inspection, leave the course in place, and let a human or a
                # rebuild decide. Nothing is deleted on a guess.
                _quarantine_structure(structure_path, e)
                status = f"corrupted ({e})"
                logger.error(
                    f"Course '{name}' has an unreadable structure.json ({e}); "
                    f"preserving the directory. If this persists it needs a "
                    f"rebuild, but a single bad read is usually a torn write."
                )

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


def _quarantine_structure(structure_path: str, err: Exception):
    """Copy an unreadable structure.json aside without disturbing the original.

    A COPY, not a move: if the file is unreadable because a writer is midway
    through replacing it, moving it would turn a transient state into a
    permanent loss — exactly the failure mode this replaced.
    """
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = f"{structure_path}.corrupt.{stamp}"
        if not os.path.exists(dest):
            shutil.copy2(structure_path, dest)
            logger.warning(f"Quarantined unreadable structure.json to {dest} ({err})")
    except Exception as e:
        logger.warning(f"Could not quarantine {structure_path}: {e}")


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
