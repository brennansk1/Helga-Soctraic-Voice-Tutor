"""
Unified Storage Manager for Helga.

Replaces KuzuDB with three storage mechanisms:
- SQLite: user progress, activity log, scheduled reviews, settings
- JSON files: course structure (data/courses/{uid}/structure.json)
- Markdown files: concept content (data/courses/{uid}/content/{concept_uid}.md)
"""

import os
import json
import sqlite3
import logging
import uuid
import shutil
import threading
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


class _ThreadLocalDB:
    """Thread-local SQLite connection manager. One connection per thread, reused
    across calls. WAL mode enables concurrent reads from different threads."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()

    def get(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None


class StorageManager:
    """Main facade — creates SQLite DB, ensures directories, provides sub-stores."""

    def __init__(self, data_dir: str = "/app/data"):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "helga.db")
        self.courses_dir = os.path.join(data_dir, "courses")
        os.makedirs(self.courses_dir, exist_ok=True)

        self._init_db()

        # Sub-stores
        self.courses = CourseStore(self.courses_dir, self.data_dir)
        self.progress = ProgressStore(self.db_path)
        self.activity = ActivityStore(self.db_path)
        self.schedule = ScheduleStore(self.db_path)
        self.settings = SettingsStore(self.db_path)
        self.flashcards = FlashcardStore(self.db_path)

    def _init_db(self):
        """Create SQLite tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_progress (
                    concept_uid TEXT PRIMARY KEY,
                    course_uid TEXT NOT NULL,
                    status TEXT DEFAULT 'locked',
                    grade INTEGER DEFAULT 0,
                    easiness_factor REAL DEFAULT 2.5,
                    interval_days INTEGER DEFAULT 0,
                    repetitions INTEGER DEFAULT 0,
                    next_review_date TEXT,
                    last_review_date TEXT,
                    times_reviewed INTEGER DEFAULT 0,
                    times_correct INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_uid TEXT NOT NULL,
                    concept_uid TEXT,
                    unit_uid TEXT,
                    activity_type TEXT NOT NULL,
                    duration_seconds INTEGER DEFAULT 0,
                    grade INTEGER,
                    details TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS scheduled_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_uid TEXT NOT NULL,
                    unit_uid TEXT NOT NULL,
                    unit_title TEXT NOT NULL,
                    scheduled_date TEXT NOT NULL,
                    review_number INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'pending',
                    completed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS flashcards (
                    uid TEXT PRIMARY KEY,
                    course_uid TEXT NOT NULL,
                    concept_uid TEXT NOT NULL,
                    front TEXT NOT NULL,
                    back TEXT NOT NULL,
                    status TEXT DEFAULT 'new',
                    next_review_date TEXT,
                    easiness_factor REAL DEFAULT 2.5,
                    interval_days INTEGER DEFAULT 0,
                    repetitions INTEGER DEFAULT 0,
                    stability REAL,
                    difficulty REAL,
                    last_review_date TEXT,
                    lapses INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'manual',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                -- 6. Schema Versioning
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
                
                -- 7. Courses metadata table for fast listing
                CREATE TABLE IF NOT EXISTS courses (
                    uid TEXT PRIMARY KEY,
                    title TEXT,
                    overview TEXT,
                    status TEXT,
                    teaching_style TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                -- BUG-9: Performance indexes
                CREATE INDEX IF NOT EXISTS idx_progress_course ON user_progress(course_uid);
                CREATE INDEX IF NOT EXISTS idx_progress_review ON user_progress(next_review_date);
                CREATE INDEX IF NOT EXISTS idx_progress_status ON user_progress(status);
                CREATE INDEX IF NOT EXISTS idx_activity_course ON activity_log(course_uid);
                CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);
                CREATE INDEX IF NOT EXISTS idx_schedule_course ON scheduled_reviews(course_uid);
                CREATE INDEX IF NOT EXISTS idx_schedule_date ON scheduled_reviews(scheduled_date);
                CREATE INDEX IF NOT EXISTS idx_schedule_status ON scheduled_reviews(status);
                CREATE INDEX IF NOT EXISTS idx_flashcards_course ON flashcards(course_uid);
                CREATE INDEX IF NOT EXISTS idx_flashcards_concept ON flashcards(concept_uid);
                CREATE INDEX IF NOT EXISTS idx_flashcards_review ON flashcards(next_review_date);
                CREATE INDEX IF NOT EXISTS idx_courses_status ON courses(status);
            """)
            
            # Set initial version if empty
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_version")
            row = cursor.fetchone()
            current_version = row[0] if row else 0
            if current_version == 0:
                cursor.execute("INSERT INTO schema_version (version) VALUES (1)")
                current_version = 1

            # Schema migration v1 → v2: Add FSRS columns to flashcards
            if current_version < 2:
                for col, col_type, default in [
                    ("stability", "REAL", None),
                    ("difficulty", "REAL", None),
                    ("last_review_date", "TEXT", None),
                    ("lapses", "INTEGER", "0"),
                    ("source", "TEXT", "'manual'"),
                ]:
                    try:
                        default_clause = f" DEFAULT {default}" if default else ""
                        cursor.execute(f"ALTER TABLE flashcards ADD COLUMN {col} {col_type}{default_clause}")
                    except sqlite3.OperationalError:
                        pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 2")
                logger.info("Schema migrated to v2: FSRS columns added to flashcards")

            # Schema migration v2 → v3: Add bloom_level to user_progress
            if current_version < 3:
                try:
                    cursor.execute("ALTER TABLE user_progress ADD COLUMN bloom_level INTEGER DEFAULT 1")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                cursor.execute("UPDATE schema_version SET version = 3")
                logger.info("Schema migrated to v3: bloom_level added to user_progress")

            conn.commit()
        finally:
            conn.close()

    def reset(self):
        """Delete all data — equivalent to clean_slate."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.courses_dir):
            shutil.rmtree(self.courses_dir)
        os.makedirs(self.courses_dir, exist_ok=True)
        self._init_db()


class CourseStore:
    """JSON course structure CRUD + Markdown content files."""

    def __init__(self, courses_dir: str, data_dir: str):
        self.courses_dir = courses_dir
        self.data_dir = data_dir
        self._cache = {}

    def create_course(self, course_dict: dict) -> str:
        """Write course structure.json and sync metadata to SQLite."""
        uid = course_dict.get("uid") or f"course_{uuid.uuid4().hex[:8]}"
        course_dict["uid"] = uid
        if "created_at" not in course_dict:
            course_dict["created_at"] = datetime.utcnow().isoformat()
        if "status" not in course_dict:
            course_dict["status"] = "skeleton"

        # AUTO-10: Write SQLite row first; only write JSON if SQLite succeeds
        db_path = os.path.join(self.data_dir, "helga.db")
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO courses (uid, title, overview, status, teaching_style)
                VALUES (?, ?, ?, ?, ?)
            """, (
                uid,
                course_dict.get("title", ""),
                course_dict.get("overview", ""),
                course_dict.get("status", "unknown"),
                course_dict.get("teaching_style", "")
            ))
            conn.commit()

        course_dir = os.path.join(self.courses_dir, uid)
        os.makedirs(course_dir, exist_ok=True)
        os.makedirs(os.path.join(course_dir, "content"), exist_ok=True)

        structure_path = os.path.join(course_dir, "structure.json")
        with open(structure_path, "w") as f:
            json.dump(course_dict, f, indent=2)

        logger.info(f"Created course structure: {structure_path}")
        return uid

    def get_course(self, uid: str) -> Optional[dict]:
        """Read course structure.json."""
        if uid in self._cache:
            # Return a copy to prevent accidental in-memory mutations affecting the cache
            import copy
            return copy.deepcopy(self._cache[uid])
            
        path = os.path.join(self.courses_dir, uid, "structure.json")
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            course = json.load(f)
            import copy
            self._cache[uid] = copy.deepcopy(course)
            return course

    def update_course(self, uid: str, course_dict: dict):
        """Overwrite course structure.json and update metadata in SQLite."""
        course_dict["uid"] = uid
        course_dict["updated_at"] = datetime.utcnow().isoformat()
        
        import copy
        self._cache[uid] = copy.deepcopy(course_dict)
        
        path = os.path.join(self.courses_dir, uid, "structure.json")
        with open(path, "w") as f:
            json.dump(course_dict, f, indent=2)
            
        # Update metadata table
        try:
            db_path = os.path.join(self.data_dir, "helga.db")
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE courses SET title=?, overview=?, status=?, teaching_style=?
                    WHERE uid=?
                """, (
                    course_dict.get("title", ""),
                    course_dict.get("overview", ""),
                    course_dict.get("status", "unknown"),
                    course_dict.get("teaching_style", ""),
                    uid
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to update course metadata in SQLite: {e}")

    def list_courses(self) -> List[dict]:
        """List all courses (metadata only from SQLite)."""
        courses = []
        try:
            with sqlite3.connect(os.path.join(self.data_dir, "helga.db")) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM courses ORDER BY created_at DESC")
                for row in cursor.fetchall():
                    courses.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to list courses from SQLite: {e}")
            # Fallback to filesystem if SQLite fails or table missing
            if not os.path.exists(self.courses_dir):
                return courses
            for name in sorted(os.listdir(self.courses_dir)):
                course_dir = os.path.join(self.courses_dir, name)
                if os.path.isdir(course_dir):
                    c = self.get_course(name)
                    if c: courses.append(c)
        return courses

    def delete_course(self, uid: str) -> bool:
        """Delete course directory and ALL related SQLite rows.

        Cascades the delete to every table that references course_uid so no
        orphan rows leak into review stats, activity logs, flashcard queues,
        or progress history. Previously this only cleared the `courses` row,
        leaving stale flashcards/progress/activity entries behind that would
        pollute aggregate queries (e.g., review stats counting cards from a
        deleted course, or fresh rebuilds inheriting old progress).
        """
        if uid in self._cache:
            del self._cache[uid]

        course_dir = os.path.join(self.courses_dir, uid)
        deleted = False
        if os.path.exists(course_dir):
            shutil.rmtree(course_dir)
            logger.info(f"Deleted course directory: {uid}")
            deleted = True

        # Cascade delete across every table that references course_uid.
        try:
            db_path = os.path.join(self.data_dir, "helga.db")
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cascade_tables = [
                    ("courses", "uid"),
                    ("user_progress", "course_uid"),
                    ("flashcards", "course_uid"),
                    ("scheduled_reviews", "course_uid"),
                    ("activity_log", "course_uid"),
                ]
                total_rows = 0
                for table, col in cascade_tables:
                    try:
                        cursor.execute(
                            f"DELETE FROM {table} WHERE {col}=?", (uid,)
                        )
                        if cursor.rowcount > 0:
                            total_rows += cursor.rowcount
                            logger.info(
                                f"Deleted {cursor.rowcount} row(s) from {table} "
                                f"for course {uid}"
                            )
                    except sqlite3.OperationalError as e:
                        # Table or column may not exist in older schemas — skip.
                        logger.debug(f"Cascade skip for {table}: {e}")
                conn.commit()
                if total_rows > 0:
                    logger.info(
                        f"Course {uid} cascade delete removed {total_rows} total row(s)"
                    )
        except Exception as e:
            logger.error(f"Failed to cascade delete course {uid}: {e}")

        return deleted

    def get_flat_concepts(self, uid: str) -> List[dict]:
        """Flatten course hierarchy into ordered concept list for syllabus queue."""
        course = self.get_course(uid)
        if not course:
            return []
        concepts = []
        for module in course.get("modules", []):
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        concepts.append({
                            "uid": concept["uid"],
                            "title": concept["title"],
                            "module_title": module["title"],
                            "unit_title": unit["title"],
                            "lesson_title": lesson["title"],
                            "depth_level": concept.get("depth_level", 3),
                            "learning_objectives": concept.get("learning_objectives", []),
                            "bloom_level": concept.get("bloom_level"),
                            "complexity_role": concept.get("complexity_role", ""),
                            "module_bloom_target": module.get("bloom_target"),
                            "text": "",  # Will be loaded from .md on demand
                        })
        return concepts

    def get_concept_by_uid(self, course_uid: str, concept_uid: str) -> Optional[dict]:
        """Find a concept by UID within a course structure."""
        course = self.get_course(course_uid)
        if not course:
            return None
        for module in course.get("modules", []):
            for unit in module.get("units", []):
                for lesson in unit.get("lessons", []):
                    for concept in lesson.get("concepts", []):
                        if concept["uid"] == concept_uid:
                            return {
                                **concept,
                                "module_title": module["title"],
                                "unit_title": unit["title"],
                                "lesson_title": lesson["title"],
                            }
        return None

    def find_concept_across_courses(self, concept_uid: str) -> Optional[dict]:
        """Search for a concept UID across all courses."""
        for course in self.list_courses():
            result = self.get_concept_by_uid(course["uid"], concept_uid)
            if result:
                result["course_uid"] = course["uid"]
                result["course_title"] = course["title"]
                return result
        return None

    def get_concept_content(self, course_uid: str, concept_uid: str) -> str:
        """Read concept .md file content. Checks content/ then topics/ for compat."""
        for subdir in ["content", "topics"]:
            path = os.path.join(self.courses_dir, course_uid, subdir, f"{concept_uid}.md")
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read()
        return ""

    def save_concept_content(self, course_uid: str, concept_uid: str, markdown: str) -> str:
        """Write concept .md file. Returns the file path."""
        content_dir = os.path.join(self.courses_dir, course_uid, "content")
        os.makedirs(content_dir, exist_ok=True)
        path = os.path.join(content_dir, f"{concept_uid}.md")
        with open(path, "w") as f:
            f.write(markdown)
        return path

    def get_unit_concepts(self, course_uid: str, unit_uid: str) -> List[dict]:
        """Get all concepts in a specific unit."""
        course = self.get_course(course_uid)
        if not course:
            return []
        for module in course.get("modules", []):
            for unit in module.get("units", []):
                if unit["uid"] == unit_uid:
                    concepts = []
                    for lesson in unit.get("lessons", []):
                        concepts.extend(lesson.get("concepts", []))
                    return concepts
        return []

    def get_course_stats(self, uid: str) -> dict:
        """Count modules, units, lessons, concepts in a course."""
        course = self.get_course(uid)
        if not course:
            return {"modules": 0, "units": 0, "lessons": 0, "concepts": 0}
        m, u, l, c = 0, 0, 0, 0
        for mod in course.get("modules", []):
            m += 1
            for unit in mod.get("units", []):
                u += 1
                for lesson in unit.get("lessons", []):
                    l += 1
                    c += len(lesson.get("concepts", []))
        return {"modules": m, "units": u, "lessons": l, "concepts": c}


class ProgressStore:
    """SQLite user progress per concept."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    # BUG-7: Whitelist of valid column names to prevent SQL injection
    _VALID_COLUMNS = {
        'status', 'grade', 'easiness_factor', 'interval_days', 'repetitions',
        'next_review_date', 'last_review_date', 'times_reviewed', 'times_correct',
        'updated_at', 'concept_uid', 'course_uid', 'bloom_level'
    }

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def get_progress(self, concept_uid: str) -> Optional[dict]:
        conn = self._get_db()
        row = conn.execute("SELECT * FROM user_progress WHERE concept_uid = ?", (concept_uid,)).fetchone()
        return dict(row) if row else None

    def update_progress(self, concept_uid: str, course_uid: str, **kwargs):
        """Upsert progress for a concept."""
        # BUG-7: Validate column names against whitelist
        invalid_keys = set(kwargs.keys()) - self._VALID_COLUMNS
        if invalid_keys:
            logger.warning(f"Rejected invalid column names in update_progress: {invalid_keys}")
            kwargs = {k: v for k, v in kwargs.items() if k in self._VALID_COLUMNS}
        
        conn = self._get_db()
        kwargs["updated_at"] = datetime.utcnow().isoformat()
        # PERF-1: Use INSERT OR REPLACE upsert pattern
        kwargs["concept_uid"] = concept_uid
        kwargs["course_uid"] = course_uid
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        conn.execute(f"INSERT OR REPLACE INTO user_progress ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        conn.commit()

    def mark_completed(self, concept_uid: str, course_uid: str):
        self.update_progress(concept_uid, course_uid, status="completed")

    def get_course_progress(self, course_uid: str) -> List[dict]:
        conn = self._get_db()
        rows = conn.execute("SELECT * FROM user_progress WHERE course_uid = ?", (course_uid,)).fetchall()
        return [dict(r) for r in rows]

    def get_due_reviews(self, target_date: str = None) -> List[dict]:
        """Get concepts due for review on or before target_date."""
        if not target_date:
            target_date = date.today().isoformat()
        conn = self._get_db()
        rows = conn.execute(
            "SELECT * FROM user_progress WHERE next_review_date <= ? AND status != 'locked'",
            (target_date,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_completion_percentage(self, course_uid: str, total_concepts: int) -> float:
        """Calculate completion percentage for a course."""
        conn = self._get_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM user_progress WHERE course_uid = ? AND status = 'completed'",
            (course_uid,)
        ).fetchone()
        completed = row["cnt"] if row else 0
        return (completed / total_concepts * 100) if total_concepts > 0 else 0


class FlashcardStore:
    """SQLite user flashcards tracking."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    # BUG-7: Whitelist of valid column names for flashcard updates
    _VALID_COLUMNS = {
        'status', 'next_review_date', 'easiness_factor', 'interval_days',
        'repetitions', 'updated_at', 'front', 'back',
        'stability', 'difficulty', 'last_review_date', 'lapses', 'source'
    }

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def add_card(self, course_uid: str, concept_uid: str, front: str, back: str) -> str:
        conn = self._get_db()
        uid = f"card_{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO flashcards (uid, course_uid, concept_uid, front, back) VALUES (?, ?, ?, ?, ?)",
            (uid, course_uid, concept_uid, front, back)
        )
        conn.commit()
        return uid

    def get_due_cards(self, course_uid: str = None, target_date: str = None) -> List[dict]:
        if not target_date:
            target_date = date.today().isoformat()
        conn = self._get_db()
        query = "SELECT * FROM flashcards WHERE (next_review_date <= ? OR next_review_date IS NULL) AND status != 'suspended'"
        params = [target_date]
        if course_uid:
            query += " AND course_uid = ?"
            params.append(course_uid)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def update_card(self, uid: str, **kwargs):
        # BUG-7: Validate column names against whitelist
        invalid_keys = set(kwargs.keys()) - self._VALID_COLUMNS
        if invalid_keys:
            logger.warning(f"Rejected invalid column names in update_card: {invalid_keys}")
            kwargs = {k: v for k, v in kwargs.items() if k in self._VALID_COLUMNS}
        
        conn = self._get_db()
        kwargs["updated_at"] = datetime.utcnow().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [uid]
        conn.execute(f"UPDATE flashcards SET {sets} WHERE uid = ?", vals)
        conn.commit()

    def grade_card_fsrs(self, uid: str, rating: int, fsrs_engine) -> dict:
        """Grade a card using FSRS algorithm and update all scheduling fields.

        Args:
            uid: Card UID
            rating: 1=Again, 2=Hard, 3=Good, 4=Easy
            fsrs_engine: FSRSEngine instance

        Returns:
            Dict with new scheduling info (interval, next_review_date, stability, etc.)
        """
        conn = self._get_db()
        row = conn.execute("SELECT * FROM flashcards WHERE uid = ?", (uid,)).fetchone()
        if not row:
            raise ValueError(f"Card {uid} not found")

        card = dict(row)
        rating = max(1, min(4, int(rating)))

        # Calculate days elapsed since last review
        days_elapsed = 0
        if card.get("last_review_date"):
            try:
                last = date.fromisoformat(card["last_review_date"])
                days_elapsed = max(0, (date.today() - last).days)
            except (ValueError, TypeError):
                days_elapsed = card.get("interval_days", 0) or 0

        # Run FSRS algorithm
        new_stability, new_difficulty = fsrs_engine.calculate_memory(
            stability=card.get("stability"),
            difficulty=card.get("difficulty"),
            rating=rating,
            days_elapsed=days_elapsed,
        )
        new_interval = fsrs_engine.next_interval(new_stability)

        # Update repetition count and lapses
        reps = (card.get("repetitions") or 0) + 1
        lapses = card.get("lapses") or 0
        if rating == 1:  # Again — lapse
            lapses += 1
            new_interval = 1  # Re-learn: show again tomorrow

        next_review = (date.today() + timedelta(days=new_interval)).isoformat()
        now = datetime.utcnow().isoformat()

        conn.execute("""
            UPDATE flashcards SET
                stability = ?, difficulty = ?, interval_days = ?,
                next_review_date = ?, last_review_date = ?,
                repetitions = ?, lapses = ?, status = 'review',
                updated_at = ?
            WHERE uid = ?
        """, (new_stability, new_difficulty, new_interval,
              next_review, date.today().isoformat(),
              reps, lapses, now, uid))
        conn.commit()

        retention = fsrs_engine.get_retention(new_stability, 0)

        return {
            "uid": uid,
            "rating": rating,
            "interval_days": new_interval,
            "next_review_date": next_review,
            "stability": round(new_stability, 4),
            "difficulty": round(new_difficulty, 4),
            "repetitions": reps,
            "lapses": lapses,
            "retention": round(retention, 4),
        }

    def get_review_stats(self, course_uid: str = None) -> dict:
        """Get aggregated review statistics for the schedule view."""
        conn = self._get_db()
        today = date.today().isoformat()
        base_where = "WHERE status != 'suspended'"
        params = []
        if course_uid:
            base_where += " AND course_uid = ?"
            params.append(course_uid)

        # Due today or overdue
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM flashcards {base_where} AND (next_review_date <= ? OR next_review_date IS NULL)",
            params + [today]
        ).fetchone()
        due_count = row["cnt"] if row else 0

        # Due in next 7 days
        week = (date.today() + timedelta(days=7)).isoformat()
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM flashcards {base_where} AND next_review_date > ? AND next_review_date <= ?",
            params + [today, week]
        ).fetchone()
        upcoming_count = row["cnt"] if row else 0

        # Average retention (for cards with stability)
        rows = conn.execute(
            f"SELECT stability, last_review_date FROM flashcards {base_where} AND stability IS NOT NULL",
            params
        ).fetchall()
        if rows:
            total_ret = 0
            for r in rows:
                elapsed = 0
                if r["last_review_date"]:
                    try:
                        elapsed = max(0, (date.today() - date.fromisoformat(r["last_review_date"])).days)
                    except (ValueError, TypeError):
                        pass
                s = r["stability"] or 1.0
                DECAY = -0.5
                FACTOR = 19.0 / 81.0
                ret = (1 + FACTOR * elapsed / s) ** DECAY if s > 0 else 0
                total_ret += ret
            avg_retention = round(total_ret / len(rows) * 100)
        else:
            avg_retention = 0

        # Cards by next_review_date for calendar view
        card_dates = conn.execute(
            f"SELECT next_review_date, COUNT(*) as cnt FROM flashcards {base_where} AND next_review_date IS NOT NULL GROUP BY next_review_date ORDER BY next_review_date",
            params
        ).fetchall()
        calendar = {r["next_review_date"]: r["cnt"] for r in card_dates}

        return {
            "due_today": due_count,
            "upcoming_7d": upcoming_count,
            "avg_retention": avg_retention,
            "calendar": calendar,
            "total_cards": due_count + upcoming_count,
        }

    def get_cards_for_concept(self, concept_uid: str) -> List[dict]:
        conn = self._get_db()
        rows = conn.execute("SELECT * FROM flashcards WHERE concept_uid = ?", (concept_uid,)).fetchall()
        return [dict(r) for r in rows]

    def get_cards_for_course(self, course_uid: str) -> List[dict]:
        conn = self._get_db()
        rows = conn.execute("SELECT * FROM flashcards WHERE course_uid = ?", (course_uid,)).fetchall()
        return [dict(r) for r in rows]


class ActivityStore:
    """SQLite activity logging."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def log_activity(self, course_uid: str, activity_type: str,
                     concept_uid: str = None, unit_uid: str = None,
                     duration_seconds: int = 0, grade: int = None,
                     details: dict = None):
        conn = self._get_db()
        conn.execute(
            "INSERT INTO activity_log (course_uid, concept_uid, unit_uid, activity_type, duration_seconds, grade, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (course_uid, concept_uid, unit_uid, activity_type, duration_seconds, grade,
             json.dumps(details) if details else None)
        )
        conn.commit()

    def get_activities(self, start_date: str = None, end_date: str = None,
                       course_uid: str = None, activity_type: str = None) -> List[dict]:
        conn = self._get_db()
        query = "SELECT * FROM activity_log WHERE 1=1"
        params = []
        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date + "T23:59:59")
        if course_uid:
            query += " AND course_uid = ?"
            params.append(course_uid)
        if activity_type:
            query += " AND activity_type = ?"
            params.append(activity_type)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_daily_summary(self, target_date: str = None) -> dict:
        if not target_date:
            target_date = date.today().isoformat()
        conn = self._get_db()
        rows = conn.execute(
            "SELECT activity_type, COUNT(*) as cnt, SUM(duration_seconds) as total_time "
            "FROM activity_log WHERE DATE(created_at) = ? GROUP BY activity_type",
            (target_date,)
        ).fetchall()
        summary = {}
        for r in rows:
            summary[r["activity_type"]] = {"count": r["cnt"], "total_seconds": r["total_time"] or 0}
        return summary

    def get_streak(self) -> int:
        """Calculate consecutive days with activity."""
        conn = self._get_db()
        rows = conn.execute(
            "SELECT DISTINCT DATE(created_at) as day FROM activity_log ORDER BY day DESC"
        ).fetchall()
        if not rows:
            return 0
        streak = 0
        today = date.today()
        for row in rows:
            day = date.fromisoformat(row["day"])
            expected = today - timedelta(days=streak)
            if day == expected:
                streak += 1
            elif day == expected - timedelta(days=1):
                # Allow today to not have activity yet
                streak += 1
            else:
                break
        return streak


class ScheduleStore:
    """SQLite scheduled reviews (SM-2 intervals)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def schedule_unit_reviews(self, course_uid: str, unit_uid: str,
                               unit_title: str, start_date: str,
                               intervals: List[int] = None):
        """DEPRECATED: Use FSRS-based flashcard scheduling instead.
        Create scheduled review entries for a unit."""
        if intervals is None:
            intervals = [1, 3, 7, 16, 35]
        conn = self._get_db()
        base = date.fromisoformat(start_date)
        for i, days in enumerate(intervals, 1):
            review_date = (base + timedelta(days=days)).isoformat()
            conn.execute(
                "INSERT INTO scheduled_reviews (course_uid, unit_uid, unit_title, scheduled_date, review_number) "
                "VALUES (?, ?, ?, ?, ?)",
                (course_uid, unit_uid, unit_title, review_date, i)
            )
        conn.commit()
        logger.info(f"Scheduled {len(intervals)} reviews for unit {unit_title}")

    def schedule_concept_review(self, course_uid: str, concept_uid: str,
                                 concept_title: str, rating: int = 3):
        """Schedule a review for a single concept based on Socratic grade.
        Uses simple grade-based intervals until FSRS engine is upgraded."""
        # Grade-to-interval mapping (days): lower grade = sooner review
        grade_intervals = {1: [1, 3], 2: [2, 7], 3: [3, 14], 4: [7, 30]}
        intervals = grade_intervals.get(min(max(rating, 1), 4), [3, 14])
        conn = self._get_db()
        try:
            base = date.today()
            for i, days in enumerate(intervals, 1):
                review_date = (base + timedelta(days=days)).isoformat()
                conn.execute(
                    "INSERT INTO scheduled_reviews (course_uid, unit_uid, unit_title, scheduled_date, review_number) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (course_uid, concept_uid, concept_title, review_date, i)
                )
            conn.commit()
            logger.info(f"Scheduled concept review for '{concept_title}' (grade {rating}): intervals {intervals}")
        except Exception as e:
            logger.warning(f"Failed to schedule concept review: {e}")

    def get_scheduled_reviews(self, start_date: str = None, end_date: str = None,
                               course_uid: str = None) -> List[dict]:
        conn = self._get_db()
        query = "SELECT * FROM scheduled_reviews WHERE 1=1"
        params = []
        if start_date:
            query += " AND scheduled_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND scheduled_date <= ?"
            params.append(end_date)
        if course_uid:
            query += " AND course_uid = ?"
            params.append(course_uid)
        query += " ORDER BY scheduled_date ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def complete_review(self, review_id: int):
        conn = self._get_db()
        conn.execute(
            "UPDATE scheduled_reviews SET status = 'completed', completed_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), review_id)
        )
        conn.commit()

    def reschedule_review(self, review_id: int, new_date: str):
        conn = self._get_db()
        conn.execute(
            "UPDATE scheduled_reviews SET scheduled_date = ?, status = 'pending' WHERE id = ?",
            (new_date, review_id)
        )
        conn.commit()

    def mark_overdue(self):
        """Mark past pending reviews as overdue."""
        today = date.today().isoformat()
        conn = self._get_db()
        conn.execute(
            "UPDATE scheduled_reviews SET status = 'overdue' WHERE scheduled_date < ? AND status = 'pending'",
            (today,)
        )
        conn.commit()

    def get_upcoming_count(self, days: int = 7) -> int:
        """Count reviews scheduled in the next N days."""
        today = date.today()
        end = (today + timedelta(days=days)).isoformat()
        conn = self._get_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM scheduled_reviews WHERE scheduled_date BETWEEN ? AND ? AND status IN ('pending', 'overdue')",
            (today.isoformat(), end)
        ).fetchone()
        return row["cnt"] if row else 0


class SettingsStore:
    """SQLite user settings key-value store."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = _ThreadLocalDB(db_path)

    def _get_db(self) -> sqlite3.Connection:
        return self._db.get()

    def get(self, key: str, default: str = None) -> Optional[str]:
        conn = self._get_db()
        row = conn.execute("SELECT value FROM user_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str):
        conn = self._get_db()
        conn.execute(
            "INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()

    def get_all(self) -> dict:
        conn = self._get_db()
        rows = conn.execute("SELECT key, value FROM user_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
