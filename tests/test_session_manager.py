"""Tests for SessionManager: CRUD, message append/replace."""

import pytest
import os

from berserker.session.manager import SessionManager


# ---------------------------------------------------------------------------
# Session CRUD Tests
# ---------------------------------------------------------------------------


class TestSessionCrud:
    """Test session create/load/list/delete."""

    def test_create_session(self, session_manager):
        """Should create a session and return its ID."""
        sid = session_manager.create()
        assert sid is not None
        assert isinstance(sid, str)
        assert len(sid) > 0

    def test_create_with_title(self, session_manager):
        """Should create a session with a custom title."""
        sid = session_manager.create(title="My Test Session")
        info = session_manager.load(sid)
        assert info is not None
        assert info["title"] == "My Test Session"

    def test_create_with_custom_id(self, session_manager):
        """Should accept a pre-generated session ID."""
        sid = session_manager.create(session_id="custom-id-123")
        assert sid == "custom-id-123"

    def test_load_session(self, session_manager):
        """Should load a session by ID."""
        sid = session_manager.create(title="Load Test")
        info = session_manager.load(sid)
        assert info is not None
        assert info["id"] == sid
        assert info["title"] == "Load Test"

    def test_load_nonexistent(self, session_manager):
        """Should return None for nonexistent session."""
        info = session_manager.load("nonexistent-session")
        assert info is None

    def test_list_sessions(self, session_manager):
        """Should list all sessions for the project."""
        session_manager.create(title="Session A")
        session_manager.create(title="Session B")
        sessions = session_manager.list_sessions()
        assert len(sessions) >= 2
        titles = [s["title"] for s in sessions]
        assert "Session A" in titles
        assert "Session B" in titles

    def test_list_sessions_limit(self, session_manager):
        """Should respect limit parameter."""
        for i in range(5):
            session_manager.create(title="Session {}".format(i))
        sessions = session_manager.list_sessions(limit=2)
        assert len(sessions) == 2

    def test_delete_session(self, session_manager):
        """Should delete a session and its messages."""
        sid = session_manager.create()
        session_manager.append_message(sid, "user", "hello")
        assert session_manager.delete(sid) is True
        assert session_manager.load(sid) is None

    def test_delete_nonexistent(self, session_manager):
        """Should return False for nonexistent session."""
        assert session_manager.delete("nonexistent") is False

    def test_delete_removes_messages(self, session_manager):
        """Deleting a session should also remove its messages."""
        sid = session_manager.create()
        session_manager.append_message(sid, "user", "msg1")
        session_manager.append_message(sid, "assistant", "msg2")
        session_manager.delete(sid)
        # Reload should fail since session is gone
        assert session_manager.load(sid) is None


# ---------------------------------------------------------------------------
# Message Tests
# ---------------------------------------------------------------------------


class TestMessages:
    """Test message append/get/replace."""

    def test_append_message(self, session_manager):
        """Should append a message and return its ID."""
        sid = session_manager.create()
        mid = session_manager.append_message(sid, "user", "Hello!")
        assert mid is not None
        assert isinstance(mid, str)

    def test_append_to_nonexistent_session(self, session_manager):
        """Should raise ValueError for nonexistent session."""
        with pytest.raises(ValueError) as exc_info:
            session_manager.append_message("nonexistent", "user", "hello")
        assert "not found" in str(exc_info.value)

    def test_get_messages_empty(self, session_manager):
        """Should return empty list for session with no messages."""
        sid = session_manager.create()
        messages = session_manager.get_messages(sid)
        assert messages == []

    def test_get_messages_order(self, session_manager):
        """Should return messages in creation order."""
        sid = session_manager.create()
        session_manager.append_message(sid, "system", "sys")
        session_manager.append_message(sid, "user", "user")
        session_manager.append_message(sid, "assistant", "assistant")

        messages = session_manager.get_messages(sid)
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    def test_get_messages_content(self, session_manager):
        """Should preserve message content."""
        sid = session_manager.create()
        session_manager.append_message(sid, "user", "Test content 123")
        messages = session_manager.get_messages(sid)
        assert messages[0]["content"] == "Test content 123"

    def test_append_message_with_tool_calls(self, session_manager):
        """Should support tool_calls in assistant messages."""
        sid = session_manager.create()
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read", "arguments": '{"path": "test.py"}'},
            }
        ]
        mid = session_manager.append_message(
            sid, "assistant", "Let me read that file.", tool_calls=tool_calls
        )
        messages = session_manager.get_messages(sid)
        assert messages[0]["tool_calls"] == tool_calls

    def test_replace_messages(self, session_manager):
        """Should replace all messages with a new list."""
        sid = session_manager.create()
        session_manager.append_message(sid, "user", "old1")
        session_manager.append_message(sid, "assistant", "old2")

        new_messages = [
            {
                "id": "new-1",
                "session_id": sid,
                "role": "system",
                "content": "new sys",
                "created_at": 1,
            },
            {
                "id": "new-2",
                "session_id": sid,
                "role": "user",
                "content": "new user",
                "created_at": 2,
            },
        ]
        count = session_manager.replace_messages(sid, new_messages)
        assert count == 2

        messages = session_manager.get_messages(sid)
        assert len(messages) == 2
        assert messages[0]["content"] == "new sys"
        assert messages[1]["content"] == "new user"

    def test_replace_messages_clears_old(self, session_manager):
        """Replacing messages should remove all old ones."""
        sid = session_manager.create()
        session_manager.append_message(sid, "user", "old")

        session_manager.replace_messages(
            sid,
            [
                {"id": "n1", "session_id": sid, "role": "user", "content": "new", "created_at": 1},
            ],
        )
        messages = session_manager.get_messages(sid)
        assert len(messages) == 1
        assert messages[0]["content"] == "new"

    def test_replace_messages_transactional_rollback(self, session_manager, isolated_db):
        """Should rollback and preserve old messages if insert fails mid-operation."""
        from unittest.mock import patch
        import berserker.session.manager as manager_module

        sid = session_manager.create()
        session_manager.append_message(sid, "user", "original-1")
        session_manager.append_message(sid, "assistant", "original-2")

        # Verify original messages exist
        original_messages = session_manager.get_messages(sid)
        assert len(original_messages) == 2

        # Prepare replacement messages (3 messages, failure on 2nd insert)
        new_messages = [
            {"id": "new-1", "session_id": sid, "role": "user", "content": "new-1", "created_at": 1},
            {"id": "new-2", "session_id": sid, "role": "user", "content": "new-2", "created_at": 2},
            {"id": "new-3", "session_id": sid, "role": "user", "content": "new-3", "created_at": 3},
        ]

        call_count = [0]

        def failing_db_insert(db, table, data):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Simulated insert failure")
            # Call the real db_insert for non-failing calls
            from berserker.storage import insert as real_db_insert
            return real_db_insert(db, table, data)

        with patch.object(manager_module, "db_insert", side_effect=failing_db_insert):
            with pytest.raises(RuntimeError, match="Simulated insert failure"):
                session_manager.replace_messages(sid, new_messages)

        # After rollback, original messages should be preserved
        messages_after = session_manager.get_messages(sid)
        assert len(messages_after) == 2
        assert messages_after[0]["content"] == "original-1"
        assert messages_after[1]["content"] == "original-2"
