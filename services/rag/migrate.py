"""Database migration system for Helga SQLite schema."""

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get('HELGA_DB_PATH', '/app/data/helga.db')
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'schema.sql')
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), 'migrations')


def get_connection(db_path=None):
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def get_current_version(conn):
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] or 0
    except sqlite3.OperationalError:
        return 0


def init_schema(conn):
    """Apply the base schema if database is empty."""
    current = get_current_version(conn)
    if current == 0:
        logger.info("Initializing database schema...")
        with open(SCHEMA_PATH, 'r') as f:
            conn.executescript(f.read())
        logger.info("Base schema applied (version 1)")


def apply_migrations(conn):
    """Apply any pending numbered migration files."""
    current = get_current_version(conn)
    if not os.path.isdir(MIGRATIONS_DIR):
        return

    migration_files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith('.sql'))

    for filename in migration_files:
        version = int(filename.split('_')[0])
        if version <= current:
            continue

        filepath = os.path.join(MIGRATIONS_DIR, filename)
        logger.info(f"Applying migration {filename}...")
        with open(filepath, 'r') as f:
            conn.executescript(f.read())

        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, filename)
        )
        conn.commit()
        logger.info(f"Migration {filename} applied successfully")


def ensure_schema(db_path=None):
    """Initialize schema and apply any pending migrations. Returns connection."""
    conn = get_connection(db_path)
    init_schema(conn)
    apply_migrations(conn)
    return conn


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    conn = ensure_schema()
    version = get_current_version(conn)
    logger.info(f"Database at version {version}")
    conn.close()
