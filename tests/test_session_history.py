"""Tests for CommandHistory: add, get, clear, and session isolation."""

from __future__ import annotations

import pytest

from berserker.session.history import CommandHistory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def command_history(isolated_db):
    # type: (str) -> CommandHistory
    """Create a CommandHistory backed by an isolated temporary DB.

    The isolated_db fixture ensures the DB singleton is reset per test.
    We call ensure_table() here so the command_history table exists in
    the fresh database.
    """
    from berserker.session.history import command_history as _ch

    _ch.ensure_table()
    return _ch


# ---------------------------------------------------------------------------
# CommandHistory CRUD Tests
# ---------------------------------------------------------------------------


class TestCommandHistoryAdd:
    """Test adding commands to history."""

    def test_add_returns_row_id(self, command_history, session_manager):
        """Should return the row id of the newly inserted entry."""
        sid = session_manager.create()
        row_id = command_history.add(sid, "ls -la")
        assert row_id is not None
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_add_preserves_content(self, command_history, session_manager):
        """Should store the exact command text."""
        sid = session_manager.create()
        command_history.add(sid, "git commit -m 'fix: typo'")
        entries = command_history.get(sid)
        assert entries == ["git commit -m 'fix: typo'"]

    def test_add_appends_in_order(self, command_history, session_manager):
        """Should append commands in insertion order."""
        sid = session_manager.create()
        command_history.add(sid, "first")
        command_history.add(sid, "second")
        command_history.add(sid, "third")
        entries = command_history.get(sid)
        assert entries == ["first", "second", "third"]

    def test_add_empty_command(self, command_history, session_manager):
        """Should allow storing empty command text."""
        sid = session_manager.create()
        row_id = command_history.add(sid, "")
        assert row_id is not None
        entries = command_history.get(sid)
        assert entries == [""]

    def test_add_special_characters(self, command_history, session_manager):
        """Should handle special characters and unicode."""
        sid = session_manager.create()
        command_history.add(sid, "echo 'hello & world < > \"test\"'")
        command_history.add(sid, "echo '\u4f60\u597d'")
        entries = command_history.get(sid)
        assert len(entries) == 2
        assert "hello & world" in entries[0]
        assert "\u4f60\u597d" in entries[1]


class TestCommandHistoryGet:
    """Test retrieving commands from history."""

    def test_get_empty_session(self, command_history, session_manager):
        """Should return empty list for session with no commands."""
        sid = session_manager.create()
        entries = command_history.get(sid)
        assert entries == []

    def test_get_returns_oldest_first(self, command_history, session_manager):
        """Should return commands ordered oldest first."""
        sid = session_manager.create()
        command_history.add(sid, "oldest")
        command_history.add(sid, "middle")
        command_history.add(sid, "newest")
        entries = command_history.get(sid)
        assert entries == ["oldest", "middle", "newest"]

    def test_get_respects_limit(self, command_history, session_manager):
        """Should limit the number of returned entries."""
        sid = session_manager.create()
        for i in range(10):
            command_history.add(sid, "cmd-{}".format(i))
        entries = command_history.get(sid, limit=3)
        assert len(entries) == 3
        # Should return the first 3 (oldest) entries
        assert entries == ["cmd-0", "cmd-1", "cmd-2"]

    def test_get_default_limit(self, command_history, session_manager):
        """Should use default limit of 50."""
        sid = session_manager.create()
        for i in range(60):
            command_history.add(sid, "cmd-{}".format(i))
        entries = command_history.get(sid)
        assert len(entries) == 50
        assert entries[0] == "cmd-0"
        assert entries[-1] == "cmd-49"

    def test_get_nonexistent_session(self, command_history):
        """Should return empty list for nonexistent session."""
        entries = command_history.get("nonexistent-session-id")
        assert entries == []


class TestCommandHistoryClear:
    """Test clearing command history."""

    def test_clear_removes_all(self, command_history, session_manager):
        """Should remove all commands for a session."""
        sid = session_manager.create()
        command_history.add(sid, "cmd1")
        command_history.add(sid, "cmd2")
        command_history.add(sid, "cmd3")
        deleted = command_history.clear(sid)
        assert deleted == 3
        assert command_history.get(sid) == []

    def test_clear_nonexistent_session(self, command_history):
        """Should return 0 for nonexistent session."""
        deleted = command_history.clear("nonexistent-session-id")
        assert deleted == 0

    def test_clear_empty_session(self, command_history, session_manager):
        """Should return 0 for session with no commands."""
        sid = session_manager.create()
        deleted = command_history.clear(sid)
        assert deleted == 0


class TestCommandHistorySessionIsolation:
    """Test that command history is isolated per session."""

    def test_different_sessions_dont_mix(self, command_history, session_manager):
        """Commands from different sessions should not appear together."""
        sid_a = session_manager.create()
        sid_b = session_manager.create()

        command_history.add(sid_a, "session-a-cmd")
        command_history.add(sid_b, "session-b-cmd")

        entries_a = command_history.get(sid_a)
        entries_b = command_history.get(sid_b)

        assert entries_a == ["session-a-cmd"]
        assert entries_b == ["session-b-cmd"]

    def test_clear_one_session_does_not_affect_other(self, command_history, session_manager):
        """Clearing one session's history should not affect another."""
        sid_a = session_manager.create()
        sid_b = session_manager.create()

        command_history.add(sid_a, "a-cmd")
        command_history.add(sid_b, "b-cmd")

        command_history.clear(sid_a)

        assert command_history.get(sid_a) == []
        assert command_history.get(sid_b) == ["b-cmd"]

    def test_many_sessions_isolated(self, command_history, session_manager):
        """Should isolate history across many sessions."""
        sids = []
        for i in range(5):
            sid = session_manager.create()
            command_history.add(sid, "cmd-for-session-{}".format(i))
            sids.append(sid)

        for i, sid in enumerate(sids):
            entries = command_history.get(sid)
            assert entries == ["cmd-for-session-{}".format(i)]


class TestCommandHistoryCascadeDelete:
    """Test that deleting a session cascades to command history."""

    def test_delete_session_removes_history(self, command_history, session_manager):
        """Deleting a session should cascade and remove its command history."""
        sid = session_manager.create()
        command_history.add(sid, "cmd1")
        command_history.add(sid, "cmd2")

        # Verify history exists
        assert len(command_history.get(sid)) == 2

        # Delete the session
        session_manager.delete(sid)

        # History should be gone (table has no rows for this session_id)
        entries = command_history.get(sid)
        assert entries == []

    def test_delete_session_does_not_affect_others(self, command_history, session_manager):
        """Deleting one session should not affect another's history."""
        sid_a = session_manager.create()
        sid_b = session_manager.create()

        command_history.add(sid_a, "a-cmd")
        command_history.add(sid_b, "b-cmd")

        session_manager.delete(sid_a)

        assert command_history.get(sid_a) == []
        assert command_history.get(sid_b) == ["b-cmd"]


class TestCommandHistoryEnsureTable:
    """Test table creation idempotency."""

    def test_ensure_table_idempotent(self, command_history):
        """Calling ensure_table multiple times should not raise."""
        command_history.ensure_table()
        command_history.ensure_table()
        command_history.ensure_table()

    def test_ensure_table_creates_schema(self, command_history, session_manager):
        """After ensure_table, add/get should work without errors."""
        sid = session_manager.create()
        command_history.ensure_table()
        command_history.add(sid, "test-cmd")
        entries = command_history.get(sid)
        assert entries == ["test-cmd"]
