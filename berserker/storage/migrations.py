"""
berserker.storage.migrations — Migration system.

Reads SQL migration files from berserker/storage/migrations/,
tracks applied versions in the `migration` table, and applies
pending migrations in order.

Migration files are named: 001_initial.sql, 002_add_column.sql, etc.
Each migration runs inside a transaction.
"""

import logging
import os
import time
import glob as _glob

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


def run_migrations(db):
    """
    Apply all pending migrations in version order.

    Args:
        db: Database instance

    Returns:
        List of applied migration names (empty if none).
    """
    # Ensure migration table exists
    db.execute("""\
        CREATE TABLE IF NOT EXISTS migration (
            version     INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            applied_at  INTEGER NOT NULL
        )
    """)
    db.commit()

    # Get already-applied versions
    applied = set()
    for row in db.fetchall("SELECT version FROM migration"):
        applied.add(row["version"])

    # Discover migration files
    pattern = os.path.join(MIGRATIONS_DIR, "*.sql")
    files = sorted(_glob.glob(pattern))

    applied_names = []

    for filepath in files:
        filename = os.path.basename(filepath)
        # Parse version from filename: 001_initial.sql -> 1
        version_str = filename.split("_")[0]
        try:
            version = int(version_str)
        except ValueError:
            continue

        if version in applied:
            continue

        # Read and execute migration
        with open(filepath, "r", encoding="utf-8") as f:
            sql = f.read()

        # Run in a transaction
        try:
            db.execute("BEGIN")
            db.executescript(sql)
            db.execute(
                "INSERT INTO migration (version, name, applied_at) VALUES (?, ?, ?)",
                (version, filename, int(time.time())),
            )
            db.execute("COMMIT")
            applied_names.append(filename)
        except Exception as e:
            logger.error("Migration %s failed: %s", filename, e)
            db.execute("ROLLBACK")
            raise

    return applied_names


def migration_status(db):
    """
    Return the current migration status.

    Returns:
        dict with keys:
            - applied: list of applied migration dicts
            - pending: list of pending migration filenames
    """
    # Ensure migration table exists
    db.execute("""\
        CREATE TABLE IF NOT EXISTS migration (
            version     INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            applied_at  INTEGER NOT NULL
        )
    """)
    db.commit()

    applied = db.fetchall("SELECT version, name, applied_at FROM migration ORDER BY version")

    applied_versions = {r["version"] for r in applied}

    pattern = os.path.join(MIGRATIONS_DIR, "*.sql")
    all_files = sorted(_glob.glob(pattern))

    pending = []
    for filepath in all_files:
        filename = os.path.basename(filepath)
        version_str = filename.split("_")[0]
        try:
            version = int(version_str)
        except ValueError:
            continue
        if version not in applied_versions:
            pending.append(filename)

    return {"applied": applied, "pending": pending}
