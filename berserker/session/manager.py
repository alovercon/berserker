"""
berserker.session.manager — SessionManager class for session lifecycle and message persistence.

Provides CRUD operations for sessions and messages backed by SQLite,
with event publishing via the berserker event bus.

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

_active_contexts_lock = threading.Lock()

if TYPE_CHECKING:
    from berserker.session.context import SessionContext

from berserker.storage import get_db
from berserker.storage import insert as db_insert
from berserker.storage import delete as db_delete
from berserker.id import generate_session_id, generate_message_id
from berserker.bus import bus, SESSION_CREATED, SESSION_DELETED, SESSION_COMPACTED, MESSAGE_ADDED

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages sessions and their messages for a specific project.

    All operations persist to SQLite via berserker.storage and publish
    events via berserker.bus.

    Args:
        project_id: Identifier tying sessions to a project.
    """

    def __init__(self, project_id):
        # type: (str) -> None
        self.project_id = project_id

    # ------------------------------------------------------------------
    # Session context coordination
    # ------------------------------------------------------------------

    _active_contexts = {}  # type: Dict[str, SessionContext]
    _current_context = None  # type: Optional[SessionContext]

    def switch_to(self, session_id):
        # type: (str) -> SessionContext
        """Switch active session. Aborts the previous session's agent.

        Args:
            session_id: The session ID to switch to.

        Returns:
            The SessionContext for the active session.
        """
        from berserker.session.context import SessionContext
        from berserker.session.instruction import instruction_loader

        # Abort previous active session
        if self._current_context is not None:
            prev_session_id = self._current_context.session_id
            self._current_context.switch_out()
            # Clean up instruction injection tracking for the previous session
            instruction_loader.clear_session(prev_session_id)

        # Activate new session
        with _active_contexts_lock:
            ctx = self._active_contexts.get(session_id)
        if ctx is None:
            ctx = SessionContext(session_id, session_manager=self)
            with _active_contexts_lock:
                self._active_contexts[session_id] = ctx

        ctx.switch_in()
        self._current_context = ctx
        return ctx

    def cleanup_active_contexts(self, max_age=3600):
        # type: (int) -> int
        """Remove active contexts older than max_age seconds.

        Args:
            max_age: Maximum age in seconds before a context is considered stale.

        Returns:
            Number of stale contexts removed.
        """
        now = time.time()
        with _active_contexts_lock:
            stale = [
                sid for sid, ctx in self._active_contexts.items()
                if now - getattr(ctx, '_last_access', now) > max_age
            ]
            for sid in stale:
                del self._active_contexts[sid]
        return len(stale)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_project(self, db):
        # type: (Any) -> None
        """Ensure the project row exists in the database (upsert pattern)."""
        existing = db.fetchone(
            "SELECT id FROM project WHERE id = ?",
            (self.project_id,),
        )
        if existing is None:
            now = int(time.time())
            db_insert(
                db,
                "project",
                {
                    "id": self.project_id,
                    "directory": ".",
                    "created_at": now,
                    "updated_at": now,
                },
            )

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create(self, session_id=None, title=None, parent_id=None):
        # type: (Optional[str], Optional[str], Optional[str]) -> str
        """Create a new session, persist to DB, publish event.

        Args:
            session_id: Optional pre-generated session ID. If None, generated.
            title: Optional human-readable title for the session.
            parent_id: Optional parent session ID for hierarchical sessions.

        Returns:
            The session ID string.
        """
        sid = session_id if session_id is not None else generate_session_id()
        now = int(time.time())

        data = {
            "id": sid,
            "project_id": self.project_id,
            "workspace_id": None,
            "parent_id": parent_id,
            "slug": sid,
            "directory": ".",
            "title": title if title is not None else "",
            "version": "1",
            "share_url": None,
            "summary_additions": 0,
            "summary_deletions": 0,
            "summary_files": 0,
            "summary_diffs": None,
            "revert": None,
            "permission": None,
            "created_at": now,
            "updated_at": now,
            "time_compacting": 0,
            "time_archived": None,
        }

        db = get_db()
        self._ensure_project(db)
        db_insert(db, "session", data)
        db.commit()

        bus.publish(SESSION_CREATED, {"id": sid, "project_id": self.project_id})

        return sid

    def load(self, session_id):
        # type: (str) -> Optional[Dict[str, Any]]
        """Load a session from DB with all its messages.

        Args:
            session_id: The session ID to load.

        Returns:
            Dict with session info and messages, or None if not found.
        """
        db = get_db()

        row = db.fetchone(
            "SELECT id, project_id, title, created_at, updated_at FROM session WHERE id = ?",
            (session_id,),
        )
        if row is None:
            return None

        session = {
            "id": row["id"],
            "project_id": row["project_id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

        messages = self.get_messages(session_id)
        session["messages"] = messages

        return session

    def list_sessions(self, limit=50, offset=0):
        # type: (int, int) -> List[Dict[str, Any]]
        """List sessions ordered by created_at DESC.

        Args:
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.

        Returns:
            List of session dicts.
        """
        db = get_db()

        rows = db.fetchall(
            "SELECT id, project_id, title, created_at, updated_at "
            "FROM session "
            "WHERE project_id = ? "
            "ORDER BY created_at DESC "
            "LIMIT ? OFFSET ?",
            (self.project_id, limit, offset),
        )

        return [
            {
                "id": row["id"],
                "project_id": row["project_id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def delete(self, session_id):
        # type: (str) -> bool
        """Delete a session and all associated messages/parts (cascade).

        Args:
            session_id: The session ID to delete.

        Returns:
            True if a session was deleted, False if not found.
        """
        db = get_db()

        # Check session exists and belongs to this project
        row = db.fetchone(
            "SELECT id FROM session WHERE id = ? AND project_id = ?",
            (session_id, self.project_id),
        )
        if row is None:
            return False

        # CASCADE foreign keys handle message and part deletion
        db_delete(db, "session", {"id": session_id})
        db.commit()

        # Clean up instruction injection tracking for the deleted session
        from berserker.session.instruction import instruction_loader
        instruction_loader.clear_session(session_id)

        bus.publish(SESSION_DELETED, {"id": session_id, "project_id": self.project_id})

        return True

    # ------------------------------------------------------------------
    # Extended session operations
    # ------------------------------------------------------------------

    def update_title(self, session_id, title):
        # type: (str, str) -> bool
        """Update the title for an existing session.

        Args:
            session_id: The session ID to update.
            title: The new title for the session.

        Returns:
            True if updated, False if session not found.
        """
        db = get_db()

        # Check if session exists and belongs to this project
        row = db.fetchone(
            "SELECT id FROM session WHERE id = ? AND project_id = ?",
            (session_id, self.project_id),
        )
        if row is None:
            return False

        now = int(time.time())
        db.execute(
            "UPDATE session SET title = ?, updated_at = ? WHERE id = ? AND project_id = ?",
            (title, now, session_id, self.project_id),
        )
        db.commit()

        return True

    def list_sessions_by_workspace(self, workspace_id, limit=50, offset=0):
        # type: (str, int, int) -> List[Dict[str, Any]]
        """List sessions filtered by workspace_id, ordered by updated_at DESC.

        Args:
            workspace_id: The workspace ID to filter by.
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.

        Returns:
            List of session dicts with id, project_id, title, workspace_id, created_at, updated_at.
        """
        db = get_db()

        rows = db.fetchall(
            "SELECT id, project_id, title, workspace_id, created_at, updated_at "
            "FROM session "
            "WHERE project_id = ? AND workspace_id = ? "
            "ORDER BY updated_at DESC "
            "LIMIT ? OFFSET ?",
            (self.project_id, workspace_id, limit, offset),
        )

        return [
            {
                "id": row["id"],
                "project_id": row["project_id"],
                "title": row["title"],
                "workspace_id": row["workspace_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def set_workspace(self, session_id, workspace_id):
        # type: (str, str) -> bool
        """Set the workspace_id for an existing session.

        Args:
            session_id: The session ID to update.
            workspace_id: The new workspace ID for the session.

        Returns:
            True if updated, False if session not found.
        """
        db = get_db()

        # Check if session exists and belongs to this project
        row = db.fetchone(
            "SELECT id FROM session WHERE id = ? AND project_id = ?",
            (session_id, self.project_id),
        )
        if row is None:
            return False

        now = int(time.time())
        db.execute(
            "UPDATE session SET workspace_id = ?, updated_at = ? WHERE id = ? AND project_id = ?",
            (workspace_id, now, session_id, self.project_id),
        )
        db.commit()

        return True

    def update_project_id(self, directory):
        # type: (str) -> None
        """Update self.project_id based on a workspace directory hash.

        Args:
            directory: The workspace directory path to derive project_id from.
        """
        project_id = hashlib.sha256(directory.encode()).hexdigest()[:16]
        self.project_id = project_id

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def append_message(self, session_id, role, content, tool_calls=None, tool_result_for=None, tool_name=None, reasoning_content=None):
        # type: (str, str, str, Optional[List[Dict[str, Any]]], Optional[str], Optional[str], Optional[str]) -> str
        """Append a message to a session, persist, publish event.

        Args:
            session_id: Target session ID.
            role: Message role ('user', 'assistant', 'system', 'tool').
            content: Message text content.
            tool_calls: Optional list of tool call dicts (for assistant messages).
            tool_result_for: Optional message ID this is a tool result for.
            tool_name: Optional tool name (for tool role messages).
            reasoning_content: Optional thinking-mode reasoning text (assistant
                messages; DeepSeek V4 thinking chains need it passed back).

        Returns:
            The generated message ID string.

        Raises:
            ValueError: If session does not exist.
        """
        db = get_db()

        # Verify session exists
        row = db.fetchone(
            "SELECT id FROM session WHERE id = ? AND project_id = ?",
            (session_id, self.project_id),
        )
        if row is None:
            raise ValueError(
                "Session '{}' not found or does not belong to project '{}'".format(
                    session_id, self.project_id
                )
            )

        mid = generate_message_id()
        now = int(time.time())

        # Build message data as JSON (matches the message.data column schema)
        message_data = {
            "id": mid,
            "session_id": session_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tool_result_for": tool_result_for,
            "created_at": now,
        }
        # Add tool_name for tool role messages
        if role == "tool" and tool_name:
            message_data["tool_name"] = tool_name
        # Keep thinking-mode reasoning for assistant messages (round-trip)
        if role == "assistant" and reasoning_content:
            message_data["reasoning_content"] = reasoning_content

        # Insert into message table (data column stores JSON)
        db_insert(
            db,
            "message",
            {
                "id": mid,
                "session_id": session_id,
                "data": json.dumps(message_data),
                "created_at": now,
                "updated_at": now,
            },
        )

        # Update session's updated_at timestamp
        db.execute(
            "UPDATE session SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        db.commit()

        bus.publish(MESSAGE_ADDED, {"session_id": session_id, "message_id": mid})

        return mid

    def get_messages(self, session_id):
        # type: (str) -> List[Dict[str, Any]]
        """Get all messages for a session ordered by created_at ASC.

        Args:
            session_id: Target session ID.

        Returns:
            List of message dicts with deserialized data.
        """
        db = get_db()

        rows = db.fetchall(
            "SELECT data FROM message WHERE session_id = ? ORDER BY created_at ASC, id ASC",
            (session_id,),
        )

        messages = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError as e:
                    logger.warning(
                        "Skipping corrupted message data for session: %s, error: %s", session_id, e
                    )
                    continue
            else:
                msg = data
            messages.append(msg)

        return messages

    def session_exists(self, session_id):
        # type: (str) -> bool
        """Check whether a session exists in the current project."""
        db = get_db()
        row = db.fetchone(
            "SELECT id FROM session WHERE id = ? AND project_id = ?",
            (session_id, self.project_id),
        )
        return row is not None

    def get_message_count(self, session_id):
        # type: (str) -> int
        """Return the number of messages stored for a session."""
        db = get_db()
        row = db.fetchone(
            "SELECT COUNT(*) AS c FROM message WHERE session_id = ?",
            (session_id,),
        )
        return row["c"] if row else 0

    def get_messages_window(self, session_id, limit, offset_from_end=0):
        # type: (str, int, int) -> List[Dict[str, Any]]
        """Return up to ``limit`` messages, skipping the ``offset_from_end`` newest ones.

        Efficient windowed read used by the GUI pagination: queries newest-first
        with LIMIT/OFFSET (SQLite-optimized), then reverses to chronological
        order. Keeps history scrolling O(batch) instead of re-reading every
        message of a large session on each click.

        Args:
            session_id: Target session ID.
            limit: Maximum number of messages to return.
            offset_from_end: Number of newest messages to skip.

        Returns:
            Chronologically ordered list of message dicts.
        """
        db = get_db()
        rows = db.fetchall(
            "SELECT data FROM message WHERE session_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (session_id, limit, offset_from_end),
        )

        messages = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
            else:
                msg = data
            messages.append(msg)

        messages.reverse()
        return messages

    def delete_message(self, session_id, message_id):
        # type: (str, str) -> None
        """Delete a single message from a session.

        Also invalidates the active SessionContext cache so the deletion is
        reflected the next time the session's messages are read.
        """
        db = get_db()
        db.execute(
            "DELETE FROM message WHERE session_id = ? AND id = ?",
            (session_id, message_id),
        )
        db.commit()

        with _active_contexts_lock:
            ctx = self._active_contexts.get(session_id)
        if ctx is not None:
            ctx.refresh()

    def archive_messages(self, session_id, reason="compaction"):
        # type: (str, str) -> str
        """Archive the session's current messages before they are replaced.

        Compaction deletes the originals via replace_messages(); this keeps a
        recoverable copy in the message_archive table. Returns the archive id.
        """
        db = get_db()
        rows = db.fetchall(
            "SELECT data FROM message WHERE session_id = ? ORDER BY created_at ASC, id ASC",
            (session_id,),
        )
        if not rows:
            return ""
        workspace_id = None
        try:
            srow = db.fetchone(
                "SELECT workspace_id FROM session WHERE id = ?", (session_id,))
            workspace_id = srow["workspace_id"] if srow else None
        except Exception:
            pass
        aid = "arc_" + uuid.uuid4().hex[:12]
        now = int(time.time())
        db.execute(
            "INSERT INTO message_archive (id, session_id, workspace_id, reason,"
            " message_count, data, created_at) VALUES (?,?,?,?,?,?,?)",
            (aid, session_id, workspace_id, reason, len(rows),
             json.dumps([r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
                         for r in rows], ensure_ascii=False), now),
        )
        db.commit()
        logger.info(
            "Archived %d messages for session %s before %s (archive %s)",
            len(rows), session_id, reason, aid,
        )
        return aid

    def list_archives(self, session_id):
        # type: (str) -> List[Dict[str, Any]]
        """List compaction archives of a session (metadata only)."""
        db = get_db()
        rows = db.fetchall(
            "SELECT id, reason, message_count, created_at FROM message_archive"
            " WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def get_archive(self, archive_id):
        # type: (str) -> Optional[Dict[str, Any]]
        """Return one archive with its full message list, or None."""
        db = get_db()
        row = db.fetchone(
            "SELECT id, session_id, reason, message_count, data, created_at"
            " FROM message_archive WHERE id = ?",
            (archive_id,),
        )
        if row is None:
            return None
        try:
            messages = json.loads(row["data"] or "[]")
        except (ValueError, TypeError):
            messages = []
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "reason": row["reason"],
            "message_count": row["message_count"],
            "created_at": row["created_at"],
            "messages": messages,
        }

    def replace_messages(self, session_id, messages):
        # type: (str, List[Dict[str, Any]]) -> int
        """Replace all messages for a session with a new list.

        Deletes all existing messages and inserts the new list in order.
        Used by compaction to persist compacted conversation history.

        Args:
            session_id: Target session ID.
            messages: List of message dicts to insert. Each dict should have
                      keys matching the message.data schema (id, session_id,
                      role, content, tool_calls, tool_result_for, created_at).

        Returns:
            Number of messages inserted.

        Raises:
            ValueError: If session does not exist.
        """
        db = get_db()

        # Verify session exists
        row = db.fetchone(
            "SELECT id FROM session WHERE id = ? AND project_id = ?",
            (session_id, self.project_id),
        )
        if row is None:
            raise ValueError(
                "Session '{}' not found or does not belong to project '{}'".format(
                    session_id, self.project_id
                )
            )

        now = int(time.time())

        try:
            db.execute("BEGIN IMMEDIATE")

            # Delete all existing messages for this session
            db.execute(
                "DELETE FROM message WHERE session_id = ?",
                (session_id,),
            )

            # Insert new messages in order
            for idx, msg in enumerate(messages):
                mid = msg.get("id") or generate_message_id()
                msg_created = msg.get("created_at", now)

                # Ensure message has required fields
                message_data = {
                    "id": mid,
                    "session_id": session_id,
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                    "tool_calls": msg.get("tool_calls"),
                    "tool_result_for": msg.get("tool_result_for"),
                    "created_at": msg_created,
                }
                # Preserve thinking-mode reasoning across compaction
                if msg.get("reasoning_content"):
                    message_data["reasoning_content"] = msg["reasoning_content"]
                # Preserve the tool name for tool role messages
                if msg.get("tool_name"):
                    message_data["tool_name"] = msg["tool_name"]

                # Keep the original created_at so units stay in seconds
                # (append_message uses int(time.time())); messages without a
                # timestamp fall back to now, ordered by id as tie-break.
                db_insert(
                    db,
                    "message",
                    {
                        "id": mid,
                        "session_id": session_id,
                        "data": json.dumps(message_data),
                        "created_at": msg_created,
                        "updated_at": now,
                    },
                )

            # Update session's updated_at timestamp
            db.execute(
                "UPDATE session SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error("Failed to compact session %s: %s", session_id, e)
            raise

        count = len(messages)
        bus.publish(
            SESSION_COMPACTED,
            {"session_id": session_id, "message_count": count},
        )

        return count

    # ------------------------------------------------------------------
    # Agent configuration persistence
    # ------------------------------------------------------------------

    def save_agent_config(self, session_id, agent_name, model=None):
        # type: (str, str, Optional[str]) -> None
        """Save the active agent configuration for a session.

        Uses upsert pattern: inserts new row or updates existing one.

        Args:
            session_id: Target session ID.
            agent_name: Name of the active agent (e.g., 'build', 'plan').
            model: Optional model override for this session.
        """
        db = get_db()
        now = int(time.time())

        # Check if row exists
        existing = db.fetchone(
            "SELECT session_id FROM session_agent_config WHERE session_id = ?",
            (session_id,),
        )

        if existing is None:
            db_insert(
                db,
                "session_agent_config",
                {
                    "session_id": session_id,
                    "agent_name": agent_name,
                    "model": model,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        else:
            db.execute(
                "UPDATE session_agent_config SET agent_name = ?, model = ?, updated_at = ? "
                "WHERE session_id = ?",
                (agent_name, model, now, session_id),
            )

        db.commit()

    def load_agent_config(self, session_id):
        # type: (str) -> Optional[Dict[str, Any]]
        """Load the agent configuration for a session.

        Args:
            session_id: Target session ID.

        Returns:
            Dict with 'agent_name' and optional 'model' keys, or None if
            no configuration exists for this session.
        """
        db = get_db()

        row = db.fetchone(
            "SELECT agent_name, model FROM session_agent_config WHERE session_id = ?",
            (session_id,),
        )

        if row is None:
            return None

        return {
            "agent_name": row["agent_name"],
            "model": row.get("model"),
        }

    def delete_agent_config(self, session_id):
        # type: (str) -> None
        """Delete the agent configuration for a session.

        Called when a session is deleted (CASCADE handles this automatically,
        but this method is available for explicit cleanup).

        Args:
            session_id: Target session ID.
        """
        db = get_db()
        db.execute(
            "DELETE FROM session_agent_config WHERE session_id = ?",
            (session_id,),
        )
        db.commit()


# Module-level singleton
session_manager = SessionManager(project_id="default")
