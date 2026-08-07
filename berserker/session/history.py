"""
berserker.session.history — Command history persistence backed to SQLite.

Provides:
- CommandHistory class: add, get, clear operations for command history
- Module-level singleton: command_history
- Auto-creates command_history table at import time

Python 3.8.10 compatible: uses type comments, Optional/List, no match/case.
"""

from __future__ import annotations

import logging
from typing import List

from berserker.storage import get_db

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS command_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    command_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
)
"""


class CommandHistory:
    """Manages command history for sessions, persisted to SQLite.

    All operations use the shared database connection from berserker.storage.
    """

    def ensure_table(self):
        # type: () -> None
        """Create the command_history table if it doesn't exist."""
        db = get_db()
        db.execute(_CREATE_TABLE_SQL)
        db.commit()

    def add(self, session_id, command_text):
        # type: (str, str) -> int
        """Insert a new command entry for the given session.

        Args:
            session_id: The session identifier.
            command_text: The command string to store.

        Returns:
            The row id of the newly inserted entry.
        """
        db = get_db()
        cursor = db.execute(
            "INSERT INTO command_history (session_id, command_text) VALUES (?, ?)",
            (session_id, command_text),
        )
        db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get(self, session_id, limit=50):
        # type: (str, int) -> List[str]
        """Return command strings for a session, oldest first.

        Args:
            session_id: The session identifier.
            limit: Maximum number of entries to return (default 50).

        Returns:
            List of command text strings, ordered oldest-first.
        """
        db = get_db()
        rows = db.fetchall(
            "SELECT command_text FROM command_history WHERE session_id = ? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        )
        return [row["command_text"] for row in rows]

    def clear(self, session_id):
        # type: (str) -> int
        """Delete all command history for a session.

        Args:
            session_id: The session identifier.

        Returns:
            Number of rows deleted.
        """
        db = get_db()
        cursor = db.execute(
            "DELETE FROM command_history WHERE session_id = ?",
            (session_id,),
        )
        db.commit()
        return cursor.rowcount


# Module-level singleton
command_history = CommandHistory()
command_history.ensure_table()
