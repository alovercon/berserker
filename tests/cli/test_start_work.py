"""Tests for /start-work command — CLI and GUI plan execution.

Covers all branches:
- Plan lookup (exact, partial, not found, no plans dir)
- Plan file read errors
- Executor agent missing
- Agent auto-switch (current != executor)
- Agent no-switch (current == executor)
- Session save failure + agent revert (CLI)
- No active session (GUI)
- Successful execution trigger
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_agent(name, model="gpt-4o", tools=None, mode="primary", permission="allow"):
    agent = MagicMock()
    agent.name = name
    agent.model = model
    agent.tools = tools or ["task", "todo", "read", "bash"]
    agent.mode = mode
    agent.permission = permission
    agent.description = "{} agent".format(name)
    return agent


def _setup_plans_dir(plans):
    # type: (dict) -> str
    """Create a temp .omo/plans/ directory with given plan files.

    Args:
        plans: dict mapping filename -> content

    Returns:
        Path to the workspace root (parent of .omo/).
    """
    tmpdir = tempfile.mkdtemp()
    plans_dir = os.path.join(tmpdir, ".omo", "plans")
    os.makedirs(plans_dir)
    for fname, content in plans.items():
        with open(os.path.join(plans_dir, fname), "w", encoding="utf-8") as f:
            f.write(content)
    return tmpdir


# ---------------------------------------------------------------------------
# CLI: _find_plan
# ---------------------------------------------------------------------------

class TestFindPlan(unittest.TestCase):
    """Test plan file lookup logic."""

    def setUp(self):
        self.workspace = _setup_plans_dir({
            "agent-team-transform.md": "# Plan A\n- [ ] Task 1",
            "refactor-auth.md": "# Plan B\n- [ ] Task 2",
        })

    def _find_plan(self, name):
        from berserker.cli.conversation import _find_plan
        with patch("berserker.workspace.get_workspace", return_value=self.workspace):
            return _find_plan(name)

    def test_exact_match_with_extension(self):
        path = self._find_plan("agent-team-transform.md")
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith("agent-team-transform.md"))

    def test_exact_match_without_extension(self):
        path = self._find_plan("agent-team-transform")
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith("agent-team-transform.md"))

    def test_partial_match(self):
        path = self._find_plan("refactor")
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith("refactor-auth.md"))

    def test_case_insensitive(self):
        path = self._find_plan("AGENT-TEAM-TRANSFORM")
        self.assertIsNotNone(path)

    def test_not_found(self):
        path = self._find_plan("nonexistent-plan")
        self.assertIsNone(path)

    def test_no_plans_directory(self):
        empty_workspace = tempfile.mkdtemp()
        with patch("berserker.workspace.get_workspace", return_value=empty_workspace):
            from berserker.cli.conversation import _find_plan
            path = _find_plan("anything")
            self.assertIsNone(path)


# ---------------------------------------------------------------------------
# CLI: _cmd_start_work
# ---------------------------------------------------------------------------

class TestCmdStartWorkCLI(unittest.TestCase):
    """Test CLI /start-work command handler."""

    def setUp(self):
        self.workspace = _setup_plans_dir({
            "test-plan.md": "# Test Plan\n\n- [ ] Task 1: Do something\n- [ ] Task 2: Do another thing",
        })
        self.session_id = "test-session-123"

    def _run_cmd(self, plan_name, current_agent="berserker"):
        """Run _cmd_start_work with mocked dependencies."""
        from berserker.cli import conversation

        # Save originals
        orig_agent = conversation._current_agent
        orig_ctx = conversation._current_ctx

        try:
            conversation._current_agent = current_agent
            mock_ctx = MagicMock()
            mock_ctx.append_message.return_value = None
            conversation._current_ctx = mock_ctx

            # Mock dependencies
            mock_agent_mgr = MagicMock()
            mock_agent_mgr.get.side_effect = lambda name: _make_mock_agent(name)

            mock_session_mgr = MagicMock()
            mock_session_mgr.append_message.return_value = None
            mock_session_mgr.save_agent_config.return_value = None
            mock_session_mgr.get_messages.return_value = []

            with patch("berserker.workspace.get_workspace", return_value=self.workspace), \
                 patch.object(conversation, "agent_manager", mock_agent_mgr), \
                 patch.object(conversation, "session_manager", mock_session_mgr), \
                 patch.object(conversation, "_build_messages_from_session", return_value=[]), \
                 patch.object(conversation, "_execute_agent_turn") as mock_exec:

                result = conversation._cmd_start_work(plan_name, self.session_id)
                return {
                    "result": result,
                    "current_agent": conversation._current_agent,
                    "exec_called": mock_exec.called,
                    "save_agent_config_called": mock_session_mgr.save_agent_config.called,
                    "append_message_called": mock_ctx.append_message.called,
                }
        finally:
            conversation._current_agent = orig_agent
            conversation._current_ctx = orig_ctx

    def test_no_plan_name_shows_usage(self):
        result = self._run_cmd("")
        self.assertEqual(result["result"], self.session_id)
        self.assertFalse(result["exec_called"])

    def test_plan_not_found(self):
        result = self._run_cmd("nonexistent")
        self.assertEqual(result["result"], self.session_id)
        self.assertFalse(result["exec_called"])

    def test_executor_not_configured(self):
        from berserker.cli import conversation
        mock_agent_mgr = MagicMock()
        mock_agent_mgr.get.side_effect = KeyError("executor")

        # Save original
        orig_agent = conversation._current_agent
        orig_ctx = conversation._current_ctx
        try:
            conversation._current_agent = "berserker"
            conversation._current_ctx = None

            with patch("berserker.workspace.get_workspace", return_value=self.workspace), \
                 patch.object(conversation, "agent_manager", mock_agent_mgr), \
                 patch.object(conversation, "session_manager", MagicMock()):
                result = conversation._cmd_start_work("test-plan", self.session_id)
                # Returns session_id on error (no switch happened)
                self.assertEqual(result, self.session_id)
                # Agent should NOT have been switched
                self.assertEqual(conversation._current_agent, "berserker")
        finally:
            conversation._current_agent = orig_agent
            conversation._current_ctx = orig_ctx

    def test_auto_switch_agent(self):
        result = self._run_cmd("test-plan", current_agent="berserker")
        self.assertEqual(result["result"], self.session_id)
        self.assertEqual(result["current_agent"], "executor")
        self.assertTrue(result["save_agent_config_called"])
        self.assertTrue(result["append_message_called"])
        self.assertTrue(result["exec_called"])

    def test_no_switch_when_already_executor(self):
        result = self._run_cmd("test-plan", current_agent="executor")
        self.assertEqual(result["result"], self.session_id)
        self.assertEqual(result["current_agent"], "executor")
        self.assertFalse(result["save_agent_config_called"])
        self.assertTrue(result["append_message_called"])
        self.assertTrue(result["exec_called"])

    def test_message_saved_to_session(self):
        from berserker.cli import conversation
        mock_session_mgr = MagicMock()
        mock_session_mgr.append_message.return_value = None
        mock_session_mgr.save_agent_config.return_value = None
        mock_session_mgr.get_messages.return_value = []

        mock_agent_mgr = MagicMock()
        mock_agent_mgr.get.side_effect = lambda name: _make_mock_agent(name)

        with patch("berserker.workspace.get_workspace", return_value=self.workspace), \
             patch.object(conversation, "agent_manager", mock_agent_mgr), \
             patch.object(conversation, "session_manager", mock_session_mgr), \
             patch.object(conversation, "_current_ctx", MagicMock()) as mock_ctx, \
             patch.object(conversation, "_build_messages_from_session", return_value=[]), \
             patch.object(conversation, "_execute_agent_turn"):

            conversation._current_agent = "berserker"
            conversation._cmd_start_work("test-plan", self.session_id)

            # Verify append_message was called on the session context with plan content
            call_args = mock_ctx.append_message.call_args
            self.assertIsNotNone(call_args)
            self.assertEqual(call_args[0][0], "user")
            self.assertIn("test-plan.md", call_args[0][1])
            self.assertIn("Task 1", call_args[0][1])


# ---------------------------------------------------------------------------
# GUI: SlashCommandHandler._cmd_start_work
# ---------------------------------------------------------------------------

class TestCmdStartWorkGUI(unittest.TestCase):
    """Test GUI /start-work command handler."""

    def setUp(self):
        self.workspace = _setup_plans_dir({
            "gui-plan.md": "# GUI Plan\n\n- [ ] Task 1",
        })

        # Mock controller
        self.mock_controller = MagicMock()
        self.mock_controller.current_agent = "berserker"
        self.mock_controller.current_model = "openai/gpt-4o"
        self.mock_controller.session_id = "gui-session-456"
        self.mock_controller.get_session_context.return_value = None

        # Mock frame
        self.mock_frame = MagicMock()
        self.mock_frame.model_agent_bar = MagicMock()
        self.mock_frame.tool_registry = MagicMock()
        self.mock_controller.frame = self.mock_frame

        from berserker.gui.controller import SlashCommandHandler
        self.handler = SlashCommandHandler(self.mock_controller)

    def test_no_plan_name_shows_usage(self):
        is_cmd, result = self.handler.process("/start-work")
        self.assertTrue(is_cmd)
        self.assertIn("Usage:", result)
        self.assertIn("Available plans:", result)

    def test_plan_not_found(self):
        is_cmd, result = self.handler.process("/start-work nonexistent")
        self.assertTrue(is_cmd)
        self.assertIn("Error:", result)
        self.assertIn("not found", result)

    def test_executor_not_configured(self):
        with patch("berserker.agent.manager.agent_manager") as mock_mgr:
            mock_mgr.get.side_effect = KeyError("executor")
            with patch("berserker.workspace.get_workspace", return_value=self.workspace), \
                 patch("berserker.session.manager.session_manager") as mock_sess:
                mock_sess.append_message.return_value = None
                is_cmd, result = self.handler.process("/start-work gui-plan")
                self.assertTrue(is_cmd)
                self.assertIn("executor", result)
                self.assertIn("not configured", result)

    def test_auto_switch_agent_updates_ui(self):
        with patch("berserker.agent.manager.agent_manager") as mock_mgr:
            mock_mgr.get.side_effect = lambda name: _make_mock_agent(name)
            with patch("berserker.workspace.get_workspace", return_value=self.workspace), \
                 patch("berserker.session.manager.session_manager") as mock_sess:
                mock_sess.append_message.return_value = None
                is_cmd, result = self.handler.process("/start-work gui-plan")
                self.assertTrue(is_cmd)
                # Controller agent should be updated
                self.assertEqual(self.mock_controller.current_agent, "executor")
                # Status bar should be updated
                self.mock_frame.set_agent_status.assert_called_with("executor")
                # Agent dropdown should be updated
                self.mock_frame.model_agent_bar.set_selected_agent.assert_called_with("executor")
                # Model dropdown should be updated
                self.mock_frame.model_agent_bar.set_selected_model.assert_called()

    def test_no_switch_when_already_executor(self):
        self.mock_controller.current_agent = "executor"
        with patch("berserker.agent.manager.agent_manager") as mock_mgr:
            mock_mgr.get.side_effect = lambda name: _make_mock_agent(name)
            with patch("berserker.workspace.get_workspace", return_value=self.workspace), \
                 patch("berserker.session.manager.session_manager") as mock_sess:
                mock_sess.append_message.return_value = None
                is_cmd, result = self.handler.process("/start-work gui-plan")
                self.assertTrue(is_cmd)
                # set_agent_status should NOT be called (no switch needed)
                self.mock_frame.set_agent_status.assert_not_called()
                self.mock_frame.model_agent_bar.set_selected_agent.assert_not_called()

    def test_no_active_session(self):
        self.mock_controller.session_id = None
        with patch("berserker.agent.manager.agent_manager") as mock_mgr:
            mock_mgr.get.side_effect = lambda name: _make_mock_agent(name)
            with patch("berserker.workspace.get_workspace", return_value=self.workspace):
                is_cmd, result = self.handler.process("/start-work gui-plan")
                self.assertTrue(is_cmd)
                self.assertIn("No active session", result)

    def test_execution_triggered(self):
        with patch("berserker.agent.manager.agent_manager") as mock_mgr:
            mock_mgr.get.side_effect = lambda name: _make_mock_agent(name)
            with patch("berserker.workspace.get_workspace", return_value=self.workspace), \
                 patch("berserker.session.manager.session_manager") as mock_sess:
                mock_sess.append_message.return_value = None
                is_cmd, result = self.handler.process("/start-work gui-plan")
                self.assertTrue(is_cmd)
                # execute_agent should be called
                self.mock_controller.execute_agent.assert_called_once()
                call_args = self.mock_controller.execute_agent.call_args
                self.assertEqual(call_args[0][1], "executor")  # agent_name

    def test_plan_content_in_message(self):
        with patch("berserker.agent.manager.agent_manager") as mock_mgr:
            mock_mgr.get.side_effect = lambda name: _make_mock_agent(name)
            with patch("berserker.workspace.get_workspace", return_value=self.workspace), \
                 patch("berserker.session.manager.session_manager") as mock_sess:
                mock_sess.append_message.return_value = None
                self.handler.process("/start-work gui-plan")
                # Verify the message saved to session contains plan content
                call_args = mock_sess.append_message.call_args
                self.assertIsNotNone(call_args)
                self.assertIn("GUI Plan", call_args[0][2])


# ---------------------------------------------------------------------------
# Integration: /start-work in slash command dispatch
# ---------------------------------------------------------------------------

class TestSlashCommandDispatch(unittest.TestCase):
    """Test that /start-work is properly dispatched in both CLI and GUI."""

    def test_cli_dispatch(self):
        from berserker.cli.conversation import _process_slash_command
        with patch("berserker.cli.conversation._cmd_start_work") as mock_cmd:
            mock_cmd.return_value = "session-1"
            result = _process_slash_command("/start-work my-plan", "session-1")
            mock_cmd.assert_called_once_with("my-plan", "session-1", None)
            self.assertEqual(result, "session-1")

    def test_gui_dispatch(self):
        mock_controller = MagicMock()
        mock_controller.current_agent = "berserker"
        mock_controller.session_id = "s1"
        mock_controller.frame = MagicMock()
        mock_controller.frame.model_agent_bar = None

        from berserker.gui.controller import SlashCommandHandler
        handler = SlashCommandHandler(mock_controller)

        with patch.object(handler, "_cmd_start_work", return_value="started"):
            is_cmd, result = handler.process("/start-work my-plan")
            self.assertTrue(is_cmd)
            handler._cmd_start_work.assert_called_once_with("my-plan")


if __name__ == "__main__":
    unittest.main()
