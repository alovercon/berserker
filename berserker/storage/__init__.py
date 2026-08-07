"""
berserker.storage — SQLite storage layer.

Public API:
    get_db()        — Get the shared Database instance (auto-initializes schema)
    db_context()    — Context manager for a fresh Database instance
    Database        — Database class with execute/fetchone/fetchall/insert/update/delete
    run_migrations  — Apply pending SQL migrations
    migration_status — Check applied/pending migrations
    insert, insert_many, update, delete, upsert — CRUD helpers
"""

from berserker.storage.db import Database, get_db, db_context
from berserker.storage.migrations import run_migrations, migration_status
from berserker.storage.crud import insert, insert_many, update, delete, upsert

__all__ = [
    "Database",
    "get_db",
    "db_context",
    "run_migrations",
    "migration_status",
    "insert",
    "insert_many",
    "update",
    "delete",
    "upsert",
]
