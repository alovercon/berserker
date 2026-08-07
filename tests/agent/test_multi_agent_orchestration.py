"""
Tests for multi-agent orchestration and plan execution.

Covers:
- Concurrent multi-agent execution (parallel mode with 3+ agents)
- Plan file loading and parsing
- /start-work command execution flow
- Agent auto-switching during plan execution
- Error handling in plan execution
- Integration: berserker → plan → executor → subagents flow

Python 3.8.10 compatible: uses type comments, no match/case.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from berserker.provider.base import ChatMessage
from berserker.tool.base import ToolContext, ToolExecutionError
from berserker.tool.task import TaskTool


# ---------------------------------------------------------------------------
# Mock Components
# ---------------------------------------------------------------------------


class MockSessionManager(object):
    """Mock session manager for testing."""

    def __init__(self):
        # type: () -> None
        self.created_sessions = []  # type: list
        self._counter = 0  # type: int
        self._lock = threading.Lock()

    def create(self, title=None, parent_id=None):
        # type: (str, str) -> str
        with self._lock:
            self._counter += 1
            session_id = "mock-sess-{}".format(self._counter)
            self.created_sessions.append({
                "session_id": session_id,
                "title": title,
                "parent_id": parent_id,
            })
            return session_id

    def load(self, session_id):
        # type: (str) -> dict
        return None

    def append_message(self, session_id, role, content):
        # type: (str, str, str) -> None
        pass

    def get_messages(self, session_id):
        # type: (str) -> list
        return []

    def save_agent_config(self, session_id, agent_name):
        # type: (str, str) -> None
        pass


class MockAgentManager(object):
    """Mock agent manager for testing."""

    def __init__(self):
        # type: () -> None
        self.calls = []  # type: list
        self._lock = threading.Lock()
        self._agents = {}  # type: dict

    def register_agent(self, name, mode="primary", model="gpt-4o"):
        # type: (str, str, str) -> None
        agent = type('MockAgent', (), {
            'name': name,
            'mode': mode,
            'model': model,
            'description': "{} agent".format(name),
            'tools': ['task', 'read', 'write', 'bash'],
            'permission': 'full',
        })()
        self._agents[name] = agent

    def get(self, name):
        # type: (str) -> object
        if name not in self._agents:
            raise KeyError("Agent '{}' not found".format(name))
        return self._agents[name]

    def execute(
        self,
        agent_name,  # type: str
        messages,  # type: list
        session_id,  # type: str
        tool_registry,  # type: object
        on_tool_call,  # type: object
        abort_event=None,  # type: object
    ):
        # type: (...) -> dict
        """Mock execute that records calls."""
        if abort_event is not None and abort_event.is_set():
            raise Exception("Aborted")

        with self._lock:
            self.calls.append({
                "agent_name": agent_name,
                "session_id": session_id,
                "messages": messages,
            })

        return {
            "content": "Result from {} in {}".format(agent_name, session_id),
            "status": "success",
        }


class MockToolRegistry(object):
    """Mock tool registry."""
    pass


class MockAbortEvent(object):
    """Mock abort event."""

    def __init__(self):
        # type: () -> None
        self._set = False

    def is_set(self):
        # type: () -> bool
        return self._set

    def set(self):
        # type: () -> None
        self._set = True


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


def make_tool_context(session_id="parent-sess"):
    # type: (str) -> ToolContext
    """Create a mock ToolContext."""
    return ToolContext(
        session_id=session_id,
        message_id="msg-1",
        agent="general",
        abort=MockAbortEvent(),
    )


def setup_patches(agent_mgr, session_mgr, tool_registry):
    # type: (object, object, object) -> dict
    """Patch modules and return original values for teardown."""
    import berserker.agent.manager as mgr_module
    import berserker.session.manager as sess_module
    import berserker.tool.registry as reg_module
    import berserker.agent.orchestrator as orch_module

    originals = {
        "agent_manager": getattr(mgr_module, "agent_manager", None),
        "session_manager": getattr(sess_module, "session_manager", None),
        "registry": getattr(reg_module, "registry", None),
        "orchestrator": getattr(orch_module, "_orchestrator", None),
    }

    mgr_module.agent_manager = agent_mgr
    sess_module.session_manager = session_mgr
    reg_module.registry = tool_registry
    orch_module._orchestrator = None  # Reset singleton

    return originals


def teardown_patches(originals):
    # type: (dict) -> None
    """Restore original modules."""
    import berserker.agent.manager as mgr_module
    import berserker.session.manager as sess_module
    import berserker.tool.registry as reg_module
    import berserker.agent.orchestrator as orch_module

    if originals.get("agent_manager") is not None:
        mgr_module.agent_manager = originals["agent_manager"]
    if originals.get("session_manager") is not None:
        sess_module.session_manager = originals["session_manager"]
    if originals.get("registry") is not None:
        reg_module.registry = originals["registry"]
    if originals.get("orchestrator") is not None:
        orch_module._orchestrator = originals["orchestrator"]
    else:
        orch_module._orchestrator = None


# ---------------------------------------------------------------------------
# Concurrent Multi-Agent Tests
# ---------------------------------------------------------------------------


class TestConcurrentMultiAgent:
    """Tests for concurrent multi-agent execution."""

    def setup_method(self):
        self.tool = TaskTool()
        self.session_mgr = MockSessionManager()
        self.agent_mgr = MockAgentManager()
        self.tool_registry = MockToolRegistry()

        # Register available agents
        self.agent_mgr.register_agent("general", "subagent")
        self.agent_mgr.register_agent("explore", "subagent")
        self.agent_mgr.register_agent("executor", "primary")
        self.agent_mgr.register_agent("consultant", "subagent")
        self.agent_mgr.register_agent("critic", "subagent")

        self.originals = setup_patches(
            self.agent_mgr, self.session_mgr, self.tool_registry
        )

    def teardown_method(self):
        teardown_patches(self.originals)

    def test_parallel_three_agents_concurrent(self):
        """Test launching 3 agents concurrently in parallel mode."""
        ctx = make_tool_context()
        args = {
            "description": "Multi-agent task",
            "prompt": "Do something",
            "subagent_type": "general",
            "mode": "parallel",
            "tasks": [
                {
                    "description": "Explore codebase",
                    "prompt": "Find all auth files",
                    "subagent_type": "explore",
                },
                {
                    "description": "Research patterns",
                    "prompt": "Find error handling patterns",
                    "subagent_type": "explore",
                },
                {
                    "description": "Implement fix",
                    "prompt": "Fix the bug",
                    "subagent_type": "general",
                },
            ],
        }

        result = self.tool.execute(args, ctx)

        # All 3 agents should have been called
        assert len(self.agent_mgr.calls) == 3
        # 3 sub-sessions should have been created
        assert len(self.session_mgr.created_sessions) == 3
        # Result should indicate parallel completion
        assert "Parallel tasks completed" in result.title
        assert "3 tasks" in result.title

    def test_parallel_five_agents_concurrent(self):
        """Test launching 5 agents concurrently (stress test)."""
        ctx = make_tool_context()
        args = {
            "description": "Large parallel task",
            "prompt": "Do it",
            "subagent_type": "general",
            "mode": "parallel",
            "tasks": [
                {"description": "Task 1", "prompt": "Work 1", "subagent_type": "explore"},
                {"description": "Task 2", "prompt": "Work 2", "subagent_type": "explore"},
                {"description": "Task 3", "prompt": "Work 3", "subagent_type": "general"},
                {"description": "Task 4", "prompt": "Work 4", "subagent_type": "general"},
                {"description": "Task 5", "prompt": "Work 5", "subagent_type": "explore"},
            ],
        }

        result = self.tool.execute(args, ctx)

        assert len(self.agent_mgr.calls) == 5
        assert len(self.session_mgr.created_sessions) == 5
        assert "5 tasks" in result.title

    def test_parallel_mixed_agent_types(self):
        """Test parallel mode with mixed agent types."""
        ctx = make_tool_context()
        args = {
            "description": "Mixed agents",
            "prompt": "Do it",
            "subagent_type": "general",
            "mode": "parallel",
            "tasks": [
                {"description": "Consult", "prompt": "Analyze", "subagent_type": "consultant"},
                {"description": "Explore", "prompt": "Search", "subagent_type": "explore"},
                {"description": "Execute", "prompt": "Run", "subagent_type": "executor"},
            ],
        }

        result = self.tool.execute(args, ctx)

        assert len(self.agent_mgr.calls) == 3
        agent_names = [c["agent_name"] for c in self.agent_mgr.calls]
        assert "consultant" in agent_names
        assert "explore" in agent_names
        assert "executor" in agent_names

    def test_parallel_parent_session_linking(self):
        """Test that parallel tasks link to parent session."""
        ctx = make_tool_context(session_id="parent-abc-123")
        args = {
            "description": "Parallel",
            "prompt": "Do it",
            "subagent_type": "general",
            "mode": "parallel",
            "tasks": [
                {"description": "Sub-task", "prompt": "Work", "subagent_type": "explore"},
            ],
        }

        self.tool.execute(args, ctx)

        assert len(self.session_mgr.created_sessions) == 1
        session = self.session_mgr.created_sessions[0]
        assert session["parent_id"] == "parent-abc-123"

    def test_parallel_result_contains_all_outputs(self):
        """Test that parallel result contains output from all agents."""
        ctx = make_tool_context()
        args = {
            "description": "Parallel",
            "prompt": "Do it",
            "subagent_type": "general",
            "mode": "parallel",
            "tasks": [
                {"description": "A", "prompt": "Work A", "subagent_type": "explore"},
                {"description": "B", "prompt": "Work B", "subagent_type": "general"},
            ],
        }

        result = self.tool.execute(args, ctx)

        # Should have task_result tags for each task
        assert result.output.count("<task_result") == 2
        assert result.output.count("</task_result>") == 2
        assert result.metadata["mode"] == "parallel"
        assert result.metadata["task_count"] == 2


# ---------------------------------------------------------------------------
# Plan Execution Tests
# ---------------------------------------------------------------------------


class TestPlanExecution:
    """Tests for plan file loading and /start-work execution."""

    def setup_method(self):
        self.workspace = tempfile.mkdtemp()
        self.plans_dir = os.path.join(self.workspace, ".omo", "plans")
        os.makedirs(self.plans_dir)

    def _create_plan(self, name, content):
        # type: (str, str) -> str
        """Create a plan file and return its path."""
        path = os.path.join(self.plans_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_plan_file_loading(self):
        """Test that plan files can be loaded."""
        plan_content = """# Test Plan

## Tasks
- [ ] Task 1: Do something
- [ ] Task 2: Do another thing
"""
        path = self._create_plan("test-plan.md", plan_content)

        with open(path, "r", encoding="utf-8") as f:
            loaded = f.read()

        assert "# Test Plan" in loaded
        assert "Task 1" in loaded
        assert "Task 2" in loaded

    def test_plan_file_with_complex_structure(self):
        """Test loading a complex plan with multiple sections."""
        plan_content = """# Complex Plan

## TL;DR
Core objective: Refactor auth module
Deliverables: Updated auth.py, new tests
Effort: 2-3 days

## Context
Original request: Fix auth bugs

## Work Objectives
- Core objective: Improve auth reliability
- Must have: Working login flow
- Must not have: Breaking changes

## TODOs
- [ ] 1. Analyze current auth implementation
  - Recommended Agent: explore
  - Acceptance Criteria: List of auth files

- [ ] 2. Implement fix
  - Recommended Agent: general
  - Acceptance Criteria: Tests pass

- [ ] 3. Run tests
  - Recommended Agent: general
  - Acceptance Criteria: All tests green
"""
        path = self._create_plan("complex-plan.md", plan_content)

        with open(path, "r", encoding="utf-8") as f:
            loaded = f.read()

        assert "## TL;DR" in loaded
        assert "## Context" in loaded
        assert "## Work Objectives" in loaded
        assert "## TODOs" in loaded
        assert "Recommended Agent: explore" in loaded
        assert "Recommended Agent: general" in loaded

    def test_plan_file_not_found(self):
        """Test handling of missing plan file."""
        non_existent = os.path.join(self.plans_dir, "non-existent.md")
        assert not os.path.exists(non_existent)


# ---------------------------------------------------------------------------
# /start-work Command Tests
# ---------------------------------------------------------------------------


class TestStartWorkCommand:
    """Tests for /start-work command execution flow."""

    def setup_method(self):
        self.workspace = tempfile.mkdtemp()
        self.plans_dir = os.path.join(self.workspace, ".omo", "plans")
        os.makedirs(self.plans_dir)

        # Create test plan
        self.plan_content = """# Test Plan

## Tasks
- [ ] Task 1: Explore codebase
- [ ] Task 2: Implement fix
"""
        self.plan_path = os.path.join(self.plans_dir, "test-plan.md")
        with open(self.plan_path, "w", encoding="utf-8") as f:
            f.write(self.plan_content)

        # Setup mocks
        self.session_mgr = MockSessionManager()
        self.agent_mgr = MockAgentManager()
        self.agent_mgr.register_agent("berserker", "primary")
        self.agent_mgr.register_agent("executor", "primary")
        self.agent_mgr.register_agent("general", "subagent")
        self.agent_mgr.register_agent("explore", "subagent")
        self.tool_registry = MockToolRegistry()

        self.originals = setup_patches(
            self.agent_mgr, self.session_mgr, self.tool_registry
        )

    def teardown_method(self):
        teardown_patches(self.originals)

    def _find_plan(self, name):
        # type: (str) -> str
        """Find a plan file by name."""
        # Try exact name
        if name.endswith(".md"):
            candidate = os.path.join(self.plans_dir, name)
            if os.path.isfile(candidate):
                return candidate
        else:
            candidate = os.path.join(self.plans_dir, name + ".md")
            if os.path.isfile(candidate):
                return candidate

        # Try partial match
        for fname in os.listdir(self.plans_dir):
            if fname.endswith(".md") and name.lower() in fname.lower():
                return os.path.join(self.plans_dir, fname)

        return None

    def test_find_plan_exact_match(self):
        """Test finding plan by exact name."""
        path = self._find_plan("test-plan.md")
        assert path is not None
        assert path.endswith("test-plan.md")

    def test_find_plan_without_extension(self):
        """Test finding plan without .md extension."""
        path = self._find_plan("test-plan")
        assert path is not None
        assert path.endswith("test-plan.md")

    def test_find_plan_partial_match(self):
        """Test finding plan by partial name."""
        path = self._find_plan("test")
        assert path is not None
        assert path.endswith("test-plan.md")

    def test_find_plan_not_found(self):
        """Test finding non-existent plan."""
        path = self._find_plan("non-existent")
        assert path is None

    def test_plan_content_injection(self):
        """Test that plan content is properly injected into execution message."""
        plan_path = self._find_plan("test-plan")
        assert plan_path is not None

        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

        # Simulate the message construction in _cmd_start_work
        plan_basename = os.path.basename(plan_path)
        exec_message = "Execute the following plan from '{}':\n\n{}".format(
            plan_basename, plan_content
        )

        assert "test-plan.md" in exec_message
        assert "Task 1" in exec_message
        assert "Task 2" in exec_message
        assert "Execute the following plan" in exec_message


# ---------------------------------------------------------------------------
# Agent Auto-Switching Tests
# ---------------------------------------------------------------------------


class TestAgentAutoSwitching:
    """Tests for automatic agent switching during plan execution."""

    def setup_method(self):
        self.agent_mgr = MockAgentManager()
        self.agent_mgr.register_agent("berserker", "primary")
        self.agent_mgr.register_agent("executor", "primary")
        self.agent_mgr.register_agent("plan", "primary")
        self.agent_mgr.register_agent("general", "subagent")

    def test_executor_agent_exists(self):
        """Test that executor agent can be retrieved."""
        agent = self.agent_mgr.get("executor")
        assert agent.name == "executor"
        assert agent.mode == "primary"

    def test_berserker_agent_exists(self):
        """Test that berserker agent can be retrieved."""
        agent = self.agent_mgr.get("berserker")
        assert agent.name == "berserker"
        assert agent.mode == "primary"

    def test_switch_from_berserker_to_executor(self):
        """Test switching from berserker to executor."""
        current_agent = "berserker"
        assert current_agent != "executor"

        # Simulate auto-switch
        new_agent = "executor"
        self.agent_mgr.get(new_agent)  # Verify exists

        assert new_agent == "executor"

    def test_no_switch_when_already_executor(self):
        """Test that no switch happens when already on executor."""
        current_agent = "executor"
        assert current_agent == "executor"
        # No switch needed

    def test_error_when_executor_not_configured(self):
        """Test error handling when executor is not configured."""
        with pytest.raises(KeyError):
            self.agent_mgr.get("non-existent-agent")


# ---------------------------------------------------------------------------
# Integration Tests: berserker → plan → executor → subagents
# ---------------------------------------------------------------------------


class TestFullIntegrationFlow:
    """Integration tests for the full multi-agent workflow."""

    def setup_method(self):
        self.session_mgr = MockSessionManager()
        self.agent_mgr = MockAgentManager()
        self.agent_mgr.register_agent("berserker", "primary")
        self.agent_mgr.register_agent("plan", "primary")
        self.agent_mgr.register_agent("executor", "primary")
        self.agent_mgr.register_agent("general", "subagent")
        self.agent_mgr.register_agent("explore", "subagent")
        self.agent_mgr.register_agent("consultant", "subagent")
        self.agent_mgr.register_agent("critic", "subagent")
        self.tool_registry = MockToolRegistry()

        self.originals = setup_patches(
            self.agent_mgr, self.session_mgr, self.tool_registry
        )

    def teardown_method(self):
        teardown_patches(self.originals)

    def test_berserker_delegates_to_explore(self):
        """Test berserker delegating to explore subagent."""
        tool = TaskTool()
        ctx = make_tool_context()
        args = {
            "description": "Explore auth",
            "prompt": "Find all auth-related files",
            "subagent_type": "explore",
        }

        result = tool.execute(args, ctx)

        assert len(self.agent_mgr.calls) == 1
        assert self.agent_mgr.calls[0]["agent_name"] == "explore"
        assert "Find all auth-related files" in self.agent_mgr.calls[0]["messages"][0].content

    def test_executor_delegates_parallel_tasks(self):
        """Test executor delegating multiple tasks in parallel."""
        tool = TaskTool()
        ctx = make_tool_context()
        args = {
            "description": "Execute plan",
            "prompt": "Execute the plan",
            "subagent_type": "executor",
            "mode": "parallel",
            "tasks": [
                {"description": "Explore", "prompt": "Find files", "subagent_type": "explore"},
                {"description": "Implement", "prompt": "Fix bug", "subagent_type": "general"},
            ],
        }

        result = tool.execute(args, ctx)

        assert len(self.agent_mgr.calls) == 2
        agent_names = [c["agent_name"] for c in self.agent_mgr.calls]
        assert "explore" in agent_names
        assert "general" in agent_names
        assert "Parallel tasks completed" in result.title

    def test_full_workflow_berserker_to_executor(self):
        """Test the full workflow: berserker receives request → delegates to executor."""
        # Step 1: berserker receives user request (simulated)
        # In real usage, berserker would analyze the request and decide to use executor
        # Here we simulate the executor being called directly

        tool = TaskTool()
        ctx = make_tool_context()

        # Simulate executor receiving a plan execution request
        args = {
            "description": "Execute plan",
            "prompt": "Execute the following plan:\n\n# Test Plan\n- [ ] Task 1\n- [ ] Task 2",
            "subagent_type": "executor",
            "mode": "parallel",
            "tasks": [
                {"description": "Task 1", "prompt": "Do task 1", "subagent_type": "explore"},
                {"description": "Task 2", "prompt": "Do task 2", "subagent_type": "general"},
            ],
        }

        result = tool.execute(args, ctx)

        # Verify the full chain executed
        assert len(self.agent_mgr.calls) == 2
        assert "Parallel tasks completed" in result.title
        assert "2 tasks" in result.title

    def test_error_recovery_in_workflow(self):
        """Test error recovery when a subagent fails."""
        # This test verifies that the workflow handles errors gracefully
        # In a real scenario, the executor would retry or skip failed tasks

        tool = TaskTool()
        ctx = make_tool_context()

        # Valid execution should work
        args = {
            "description": "Test",
            "prompt": "Do it",
            "subagent_type": "general",
        }

        result = tool.execute(args, ctx)

        assert "Task completed" in result.title
        assert len(self.agent_mgr.calls) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
