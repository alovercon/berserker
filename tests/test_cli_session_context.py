"""Tests for CLI SessionContext usage in cli.py and conversation.py."""

import os
import signal
import pytest
from unittest.mock import MagicMock, patch, call

from berserker.session.context import SessionContext, SessionState
from berserker.session.manager import SessionManager


# ===================================================================
# Test CLI _cmd_run uses SessionContext
# ===================================================================


class TestCmdRunUsesSessionContext:
    """Tests that _cmd_run uses SessionContext for message operations."""

    @pytest.fixture
    def mock_dependencies(self):
        # type: () -> dict
        """Mock all external dependencies for _cmd_run."""
        mocks = {}

        # Mock config
        mocks["config"] = {
            "providers": {
                "test-provider": {
                    "type": "openai",
                    "api_key": "test-key",
                    "models": ["gpt-4"],
                }
            },
            "agents": [],
            "logging": {"enabled": False},
        }

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.id = "test-provider"
        mock_provider.name = "Test Provider"
        mock_provider.list_models.return_value = ["gpt-4"]

        # Mock registry
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_provider
        mock_registry.list_providers.return_value = ["test-provider"]
        mock_registry.get_provider_for_model.return_value = (mock_provider, "gpt-4")

        # Mock session manager
        mock_session_manager = MagicMock()
        mock_session_manager.create.return_value = "test-session-001"
        mock_session_manager.load.return_value = {
            "id": "test-session-001",
            "messages": [],
        }
        mock_session_manager.list_sessions.return_value = []
        mock_session_manager.delete.return_value = True

        # Mock session context
        mock_ctx = MagicMock()
        mock_ctx.get_messages.return_value = []
        mock_ctx.append_message.return_value = "msg-001"

        # Make switch_to return the mock context
        mock_session_manager.switch_to.return_value = mock_ctx

        mocks["provider"] = mock_provider
        mocks["registry"] = mock_registry
        mocks["session_manager"] = mock_session_manager
        mocks["ctx"] = mock_ctx

        return mocks

    def test_cmd_run_calls_switch_to(self, mock_dependencies, isolated_db):
        # type: (dict, str) -> None
        """_cmd_run should call session_manager.switch_to() to get SessionContext."""
        from berserker.session.manager import session_manager

        # Patch the session_manager singleton
        with patch("berserker.session.manager.session_manager", mock_dependencies["session_manager"]):
            with patch("berserker.provider.registry.registry", mock_dependencies["registry"]):
                with patch("berserker.config.load_config", return_value=mock_dependencies["config"]):
                    with patch("berserker.agent.manager.agent_manager") as mock_agent_mgr:
                        with patch("berserker.permission.permission_checker"):
                            with patch("berserker.plugin_system.plugin_manager") as mock_plugin_mgr:
                                mock_plugin_mgr.load_from_config.return_value = None
                                mock_plugin_mgr.activate_all.return_value = None
                                mock_plugin_mgr.get_registered_commands.return_value = {}

                                mock_agent_mgr.execute.return_value = {
                                    "content": "Test response",
                                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                                }

                                # Build args namespace
                                import argparse
                                args = argparse.Namespace(
                                    message="Hello",
                                    session=None,
                                    provider=None,
                                    model=None,
                                    config=None,
                                    verbose=False,
                                    project=None,
                                    agent=None,
                                )

                                from berserker.cli.cli import _cmd_run
                                _cmd_run(args)

                                # Verify switch_to was called
                                mock_dependencies["session_manager"].switch_to.assert_called_once()

    def test_cmd_run_uses_ctx_for_messages(self, mock_dependencies, isolated_db):
        # type: (dict, str) -> None
        """_cmd_run should use SessionContext for get_messages and append_message."""
        from berserker.session.manager import session_manager

        with patch("berserker.session.manager.session_manager", mock_dependencies["session_manager"]):
            with patch("berserker.provider.registry.registry", mock_dependencies["registry"]):
                with patch("berserker.config.load_config", return_value=mock_dependencies["config"]):
                    with patch("berserker.agent.manager.agent_manager") as mock_agent_mgr:
                        with patch("berserker.permission.permission_checker"):
                            with patch("berserker.plugin_system.plugin_manager") as mock_plugin_mgr:
                                mock_plugin_mgr.load_from_config.return_value = None
                                mock_plugin_mgr.activate_all.return_value = None
                                mock_plugin_mgr.get_registered_commands.return_value = {}

                                mock_agent_mgr.execute.return_value = {
                                    "content": "Test response",
                                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                                }

                                import argparse
                                args = argparse.Namespace(
                                    message="Hello",
                                    session=None,
                                    provider=None,
                                    model=None,
                                    config=None,
                                    verbose=False,
                                    project=None,
                                    agent=None,
                                )

                                from berserker.cli.cli import _cmd_run
                                _cmd_run(args)

                                # Verify ctx.get_messages was called
                                mock_dependencies["ctx"].get_messages.assert_called_once()

                                # Verify ctx.append_message was called for user message
                                mock_dependencies["ctx"].append_message.assert_any_call("user", "Hello")

                                # Verify ctx.append_message was called for assistant response
                                mock_dependencies["ctx"].append_message.assert_any_call("assistant", "Test response")

    def test_cmd_run_uses_ctx_for_existing_session(self, mock_dependencies, isolated_db):
        # type: (dict, str) -> None
        """_cmd_run should use SessionContext when continuing an existing session."""
        mock_dependencies["session_manager"].load.return_value = {
            "id": "existing-session",
            "messages": [
                {"role": "user", "content": "Previous message"},
                {"role": "assistant", "content": "Previous response"},
            ],
        }
        mock_dependencies["ctx"].get_messages.return_value = [
            {"role": "user", "content": "Previous message"},
            {"role": "assistant", "content": "Previous response"},
        ]

        with patch("berserker.session.manager.session_manager", mock_dependencies["session_manager"]):
            with patch("berserker.provider.registry.registry", mock_dependencies["registry"]):
                with patch("berserker.config.load_config", return_value=mock_dependencies["config"]):
                    with patch("berserker.agent.manager.agent_manager") as mock_agent_mgr:
                        with patch("berserker.permission.permission_checker"):
                            with patch("berserker.plugin_system.plugin_manager") as mock_plugin_mgr:
                                mock_plugin_mgr.load_from_config.return_value = None
                                mock_plugin_mgr.activate_all.return_value = None
                                mock_plugin_mgr.get_registered_commands.return_value = {}

                                mock_agent_mgr.execute.return_value = {
                                    "content": "Continued response",
                                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                                }

                                import argparse
                                args = argparse.Namespace(
                                    message="Continue",
                                    session="existing-session",
                                    provider=None,
                                    model=None,
                                    config=None,
                                    verbose=False,
                                    project=None,
                                    agent=None,
                                )

                                from berserker.cli.cli import _cmd_run
                                _cmd_run(args)

                                # Verify switch_to was called with the existing session
                                mock_dependencies["session_manager"].switch_to.assert_called_once_with("existing-session")


# ===================================================================
# Test Conversation SessionContext Integration
# ===================================================================


class TestConversationSessionContext:
    """Tests that conversation.py uses SessionContext correctly."""

    def test_sigint_handler_signals_abort_via_context(self):
        # type: () -> None
        """_sigint_handler should call request_abort on the active SessionContext."""
        from berserker.cli import conversation

        # Create a mock context
        mock_ctx = MagicMock()

        # Set the global context
        old_ctx = conversation._current_ctx
        conversation._current_ctx = mock_ctx

        try:
            # Simulate SIGINT
            conversation._sigint_handler(signal.SIGINT, None)

            # Verify abort was requested
            mock_ctx.request_abort.assert_called_once()
        finally:
            conversation._current_ctx = old_ctx

    def test_sigint_handler_handles_missing_context(self):
        # type: () -> None
        """_sigint_handler should not crash when _current_ctx is None."""
        from berserker.cli import conversation

        old_ctx = conversation._current_ctx
        conversation._current_ctx = None

        try:
            # Should not raise
            conversation._sigint_handler(signal.SIGINT, None)
        finally:
            conversation._current_ctx = old_ctx

    def test_cmd_clear_creates_new_context(self, session_manager):
        # type: (SessionManager) -> None
        """_cmd_clear should create a new session and switch to it."""
        from berserker.cli import conversation

        # Create an initial session
        initial_sid = session_manager.create()

        old_ctx = conversation._current_ctx
        conversation._current_ctx = None

        try:
            # Patch session_manager in conversation module
            with patch.object(conversation, "session_manager", session_manager):
                new_sid = conversation._cmd_clear(initial_sid)

                # Verify new session was created
                assert new_sid != initial_sid

                # Verify context was switched
                assert conversation._current_ctx is not None
                assert conversation._current_ctx.session_id == new_sid
        finally:
            conversation._current_ctx = old_ctx

    def test_cmd_session_switches_context(self, session_manager):
        # type: (SessionManager) -> None
        """_cmd_session should switch to the new session context."""
        from berserker.cli import conversation

        # Create two sessions
        sid1 = session_manager.create()
        sid2 = session_manager.create()

        old_ctx = conversation._current_ctx
        conversation._current_ctx = None

        try:
            with patch.object(conversation, "session_manager", session_manager):
                result = conversation._cmd_session(sid2)

                # Verify session switched
                assert result == sid2
                assert conversation._current_ctx is not None
                assert conversation._current_ctx.session_id == sid2
        finally:
            conversation._current_ctx = old_ctx

    def test_build_messages_uses_context(self, session_manager):
        # type: (SessionManager) -> None
        """_build_messages_from_session should use SessionContext when available."""
        from berserker.cli import conversation
        from berserker.session.context import SessionContext

        sid = session_manager.create()
        session_manager.append_message(sid, "user", "Hello")
        session_manager.append_message(sid, "assistant", "Hi there")

        old_ctx = conversation._current_ctx

        try:
            # Create and set a SessionContext
            ctx = SessionContext(sid, session_manager=session_manager)
            ctx.get_messages()  # Load cache
            conversation._current_ctx = ctx

            with patch.object(conversation, "session_manager", session_manager):
                messages = conversation._build_messages_from_session(sid)

                # Should have 2 messages
                assert len(messages) == 2
                assert messages[0].content == "Hello"
                assert messages[1].content == "Hi there"
        finally:
            conversation._current_ctx = old_ctx

    def test_build_messages_falls_back_without_context(self, session_manager):
        # type: (SessionManager) -> None
        """_build_messages_from_session should fall back to session_manager when no context."""
        from berserker.cli import conversation

        sid = session_manager.create()
        session_manager.append_message(sid, "user", "Fallback test")

        old_ctx = conversation._current_ctx
        conversation._current_ctx = None

        try:
            with patch.object(conversation, "session_manager", session_manager):
                messages = conversation._build_messages_from_session(sid)

                assert len(messages) == 1
                assert messages[0].content == "Fallback test"
        finally:
            conversation._current_ctx = old_ctx


# ===================================================================
# Test Session Manager switch_to Integration
# ===================================================================


class TestSessionManagerSwitchTo:
    """Tests for session_manager.switch_to() integration."""

    def test_switch_to_creates_context(self, session_manager):
        # type: (SessionManager) -> None
        """switch_to should create a SessionContext if one doesn't exist."""
        sid = session_manager.create()
        ctx = session_manager.switch_to(sid)

        assert ctx is not None
        assert ctx.session_id == sid
        assert ctx.state == SessionState.ACTIVE

    def test_switch_to_aborts_previous_session(self, session_manager):
        # type: (SessionManager) -> None
        """switch_to should abort the previous active session."""
        sid1 = session_manager.create()
        sid2 = session_manager.create()

        ctx1 = session_manager.switch_to(sid1)
        # switch_to already transitions to ACTIVE, so go directly to AGENT_RUNNING
        ctx1.transition(SessionState.AGENT_RUNNING)

        # Switch to second session
        ctx2 = session_manager.switch_to(sid2)

        # First session should be aborted
        assert ctx1.is_aborted()
        assert ctx1.state == SessionState.IDLE

        # Second session should be active
        assert ctx2.state == SessionState.ACTIVE
        assert not ctx2.is_aborted()

    def test_switch_to_reuses_existing_context(self, session_manager):
        # type: (SessionManager) -> None
        """switch_to should reuse an existing SessionContext if available."""
        sid = session_manager.create()

        ctx1 = session_manager.switch_to(sid)
        ctx2 = session_manager.switch_to(sid)

        # Should be the same object
        assert ctx1 is ctx2
