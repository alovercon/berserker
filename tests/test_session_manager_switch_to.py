"""Tests for SessionManager.switch_to() and SessionContext coordination."""

import pytest
from unittest.mock import MagicMock, patch

from berserker.session.manager import SessionManager
from berserker.session.context import SessionContext, SessionState


@pytest.fixture(autouse=True)
def reset_session_manager_state():
    """Reset class-level SessionManager state between tests."""
    SessionManager._active_contexts = {}
    SessionManager._current_context = None
    yield
    SessionManager._active_contexts = {}
    SessionManager._current_context = None


class TestSwitchTo:
    """Tests for SessionManager.switch_to() method."""

    def test_switch_to_creates_context(self, session_manager):
        """switch_to() should create a new SessionContext for unknown session."""
        ctx = session_manager.switch_to("new-session-001")

        assert ctx is not None
        assert isinstance(ctx, SessionContext)
        assert ctx.session_id == "new-session-001"
        assert session_manager._current_context is ctx
        assert "new-session-001" in session_manager._active_contexts

    def test_switch_to_reuses_existing_context(self, session_manager):
        """switch_to() should reuse an existing SessionContext."""
        ctx1 = session_manager.switch_to("session-001")
        ctx2 = session_manager.switch_to("session-001")

        assert ctx1 is ctx2
        assert len(session_manager._active_contexts) == 1

    def test_switch_to_aborts_previous_session(self, session_manager):
        """switch_to() should abort the previous active session's agent."""
        ctx1 = session_manager.switch_to("session-a")
        # switch_to() already transitions to ACTIVE, so go to AGENT_RUNNING
        ctx1.transition(SessionState.AGENT_RUNNING)

        # Switch to a different session
        ctx2 = session_manager.switch_to("session-b")

        # Previous session should be aborted (IDLE state)
        assert ctx1.state == SessionState.IDLE
        assert ctx1.is_aborted()
        # New session should be active
        assert ctx2.state == SessionState.ACTIVE
        assert not ctx2.is_aborted()

    def test_switch_to_sets_current_context(self, session_manager):
        """switch_to() should update _current_context."""
        ctx1 = session_manager.switch_to("session-a")
        assert session_manager._current_context is ctx1

        ctx2 = session_manager.switch_to("session-b")
        assert session_manager._current_context is ctx2
        assert session_manager._current_context is not ctx1

    def test_switch_to_returns_context(self, session_manager):
        """switch_to() should return the SessionContext."""
        ctx = session_manager.switch_to("test-session")
        assert isinstance(ctx, SessionContext)

    def test_switch_to_activates_new_session(self, session_manager):
        """switch_to() should transition the new session to ACTIVE."""
        ctx = session_manager.switch_to("test-session")
        assert ctx.state == SessionState.ACTIVE

    def test_switch_to_clears_abort_on_new_session(self, session_manager):
        """switch_to() should clear any pending abort on the new session."""
        # Create a context manually with abort set
        ctx = SessionContext("pre-existing", session_manager=session_manager)
        ctx._abort_event.set()
        session_manager._active_contexts["pre-existing"] = ctx

        # Switch to it
        result = session_manager.switch_to("pre-existing")
        assert not result.is_aborted()
        assert result.state == SessionState.ACTIVE


class TestActiveContextsTracking:
    """Tests for _active_contexts dict management."""

    def test_active_contexts_grows_with_new_sessions(self, session_manager):
        """_active_contexts should track all switched-to sessions."""
        session_manager.switch_to("session-1")
        session_manager.switch_to("session-2")
        session_manager.switch_to("session-3")

        assert len(session_manager._active_contexts) == 3
        assert "session-1" in session_manager._active_contexts
        assert "session-2" in session_manager._active_contexts
        assert "session-3" in session_manager._active_contexts

    def test_active_contexts_persists_across_switches(self, session_manager):
        """Switching away and back should not create duplicate contexts."""
        ctx1a = session_manager.switch_to("session-a")
        session_manager.switch_to("session-b")
        ctx1b = session_manager.switch_to("session-a")

        assert ctx1a is ctx1b
        assert len(session_manager._active_contexts) == 2


class TestSwitchToWithPersistence:
    """Tests for switch_to() with real database persistence."""

    def test_switch_to_context_can_append_messages(self, session_manager):
        """Context from switch_to() should be able to append messages."""
        # Create a session first
        sid = session_manager.create()
        session_manager.append_message(sid, "system", "You are helpful")

        # Switch to it
        ctx = session_manager.switch_to(sid)

        # Append via context
        ctx.append_message("user", "Hello")

        # Verify via manager
        messages = session_manager.get_messages(sid)
        assert len(messages) == 2
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"

    def test_switch_to_context_can_get_messages(self, session_manager):
        """Context from switch_to() should be able to get messages."""
        sid = session_manager.create()
        session_manager.append_message(sid, "user", "Test message")

        ctx = session_manager.switch_to(sid)
        messages = ctx.get_messages()

        assert len(messages) == 1
        assert messages[0]["content"] == "Test message"

    def test_switch_to_context_build_messages_for_llm(self, session_manager):
        """Context from switch_to() should build messages for LLM."""
        sid = session_manager.create()
        session_manager.append_message(sid, "system", "System prompt")
        session_manager.append_message(sid, "user", "Hello")
        session_manager.append_message(sid, "tool", "")  # Empty tool message

        ctx = session_manager.switch_to(sid)
        llm_messages = ctx.build_messages_for_llm()

        # Empty tool message should be filtered out
        assert len(llm_messages) == 2
        assert llm_messages[0]["role"] == "system"
        assert llm_messages[1]["role"] == "user"


class TestGUIControllerSessionContext:
    """Tests for GUIController session context integration."""

    def test_controller_has_session_context_methods(self):
        """GUIController should have set_session_context and get_session_context."""
        from berserker.gui.controller import GUIController

        # Create a mock frame
        mock_frame = MagicMock()
        controller = GUIController(mock_frame, session_id="test-001")

        assert hasattr(controller, "set_session_context")
        assert hasattr(controller, "get_session_context")

    def test_controller_session_context_defaults_to_none(self):
        """GUIController._session_context should default to None."""
        from berserker.gui.controller import GUIController

        mock_frame = MagicMock()
        controller = GUIController(mock_frame, session_id="test-001")

        assert controller.get_session_context() is None

    def test_controller_set_get_session_context(self):
        """GUIController should store and retrieve session context."""
        from berserker.gui.controller import GUIController

        mock_frame = MagicMock()
        controller = GUIController(mock_frame, session_id="test-001")

        mock_ctx = MagicMock(spec=SessionContext)
        controller.set_session_context(mock_ctx)

        assert controller.get_session_context() is mock_ctx

    def test_controller_set_session_context_to_none(self):
        """GUIController should allow clearing session context."""
        from berserker.gui.controller import GUIController

        mock_frame = MagicMock()
        controller = GUIController(mock_frame, session_id="test-001")

        mock_ctx = MagicMock(spec=SessionContext)
        controller.set_session_context(mock_ctx)
        controller.set_session_context(None)

        assert controller.get_session_context() is None
