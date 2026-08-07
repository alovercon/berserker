"""
Tests for berserker.tool.task orchestration support.

Covers:
- TaskTool with mode="parallel" launching multiple subagents.
- TaskTool with mode="sequential" launching subagents in order.
- TaskTool backward compatibility (mode="single" or default).
- Validation of subagent types in orchestration modes.
- Result formatting for parallel and sequential modes.

Python 3.8.10 compatible: uses type comments, no match/case.
"""

import threading
import pytest

from berserker.provider.base import ChatMessage
from berserker.tool.base import ToolContext
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
        return None  # No existing sessions

    def get_messages(self, session_id):
        # type: (str) -> list
        return []  # No persisted messages


class MockAgentManager(object):
    """Mock agent manager for testing."""

    def __init__(self):
        # type: () -> None
        self.calls = []  # type: list
        self._lock = threading.Lock()

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


# ---------------------------------------------------------------------------
# Backward Compatibility Tests
# ---------------------------------------------------------------------------


class TestTaskToolBackwardCompat:
    """Tests for backward compatibility (single mode)."""

    def setup_method(self):
        self.tool = TaskTool()
        self.session_mgr = MockSessionManager()
        self.agent_mgr = MockAgentManager()
        self.tool_registry = MockToolRegistry()

        # Patch imports
        import berserker.agent.manager as mgr_module
        import berserker.session.manager as sess_module
        import berserker.tool.registry as reg_module

        self._orig_agent_manager = getattr(mgr_module, "agent_manager", None)
        self._orig_session_manager = getattr(sess_module, "session_manager", None)
        self._orig_registry = getattr(reg_module, "registry", None)

        mgr_module.agent_manager = self.agent_mgr
        sess_module.session_manager = self.session_mgr
        reg_module.registry = self.tool_registry

    def teardown_method(self):
        """Restore original modules."""
        import berserker.agent.manager as mgr_module
        import berserker.session.manager as sess_module
        import berserker.tool.registry as reg_module

        if self._orig_agent_manager is not None:
            mgr_module.agent_manager = self._orig_agent_manager
        if self._orig_session_manager is not None:
            sess_module.session_manager = self._orig_session_manager
        if self._orig_registry is not None:
            reg_module.registry = self._orig_registry

    def test_single_mode_default(self):
        """Test default single mode (no mode specified)."""
        ctx = make_tool_context()
        args = {
            "description": "Test task",
            "prompt": "Do something",
            "subagent_type": "explore",
        }

        result = self.tool.execute(args, ctx)

        assert result.title is not None
        assert "Task completed" in result.title
        assert "explore" in result.output
        assert len(self.session_mgr.created_sessions) == 1
        assert len(self.agent_mgr.calls) == 1

    def test_single_mode_explicit(self):
        """Test explicit single mode."""
        ctx = make_tool_context()
        args = {
            "description": "Test task",
            "prompt": "Do something",
            "subagent_type": "general",
            "mode": "single",
        }

        result = self.tool.execute(args, ctx)

        assert "general" in result.output
        assert len(self.agent_mgr.calls) == 1

    def test_single_mode_with_task_id_resume(self):
        """Test single mode with existing task_id (resume)."""
        # Pre-create a session that "exists"
        self.session_mgr.created_sessions.append({
            "session_id": "existing-sess",
            "title": "Existing",
            "parent_id": "parent-sess",
        })

        # Override load to return the existing session
        def mock_load(sid):
            # type: (str) -> dict
            if sid == "existing-sess":
                return {"session_id": sid}
            return None

        self.session_mgr.load = mock_load

        ctx = make_tool_context()
        args = {
            "description": "Resume task",
            "prompt": "Continue",
            "subagent_type": "explore",
            "task_id": "existing-sess",
        }

        result = self.tool.execute(args, ctx)

        # Should not create a new session
        assert len(self.session_mgr.created_sessions) == 1  # Only the pre-created one
        assert self.agent_mgr.calls[0]["session_id"] == "existing-sess"


# ---------------------------------------------------------------------------
# Parallel Mode Tests
# ---------------------------------------------------------------------------


class TestTaskToolParallel:
    """Tests for parallel execution mode."""

    def setup_method(self):
        self.tool = TaskTool()
        self.session_mgr = MockSessionManager()
        self.agent_mgr = MockAgentManager()
        self.tool_registry = MockToolRegistry()

        # Patch imports
        import berserker.agent.manager as mgr_module
        import berserker.session.manager as sess_module
        import berserker.tool.registry as reg_module
        import berserker.agent.orchestrator as orch_module

        self._orig_agent_manager = getattr(mgr_module, "agent_manager", None)
        self._orig_session_manager = getattr(sess_module, "session_manager", None)
        self._orig_registry = getattr(reg_module, "registry", None)
        self._orig_orchestrator = getattr(orch_module, "_orchestrator", None)

        mgr_module.agent_manager = self.agent_mgr
        sess_module.session_manager = self.session_mgr
        reg_module.registry = self.tool_registry
        orch_module._orchestrator = None  # Reset singleton

    def teardown_method(self):
        """Restore original modules and reset singleton."""
        import berserker.agent.manager as mgr_module
        import berserker.session.manager as sess_module
        import berserker.tool.registry as reg_module
        import berserker.agent.orchestrator as orch_module

        if self._orig_agent_manager is not None:
            mgr_module.agent_manager = self._orig_agent_manager
        if self._orig_session_manager is not None:
            sess_module.session_manager = self._orig_session_manager
        if self._orig_registry is not None:
            reg_module.registry = self._orig_registry
        if self._orig_orchestrator is not None:
            orch_module._orchestrator = self._orig_orchestrator
        else:
            orch_module._orchestrator = None

    def test_parallel_mode_basic(self):
        """Test basic parallel execution."""
        ctx = make_tool_context()
        args = {
            "description": "Parallel tasks",
            "prompt": "Do something",
            "subagent_type": "explore",
            "mode": "parallel",
            "tasks": [
                {
                    "description": "Task A",
                    "prompt": "Research topic A",
                    "subagent_type": "explore",
                },
                {
                    "description": "Task B",
                    "prompt": "Research topic B",
                    "subagent_type": "general",
                },
            ],
        }

        result = self.tool.execute(args, ctx)

        assert "Parallel tasks completed" in result.title
        assert "2 tasks" in result.title
        assert "<task_result" in result.output
        assert len(self.session_mgr.created_sessions) == 2
        assert len(self.agent_mgr.calls) == 2

    def test_parallel_creates_sub_sessions(self):
        """Test that parallel mode creates sub-sessions with parent linking."""
        ctx = make_tool_context(session_id="parent-123")
        args = {
            "description": "Parallel",
            "prompt": "Do it",
            "subagent_type": "explore",
            "mode": "parallel",
            "tasks": [
                {
                    "description": "Sub-task",
                    "prompt": "Work",
                    "subagent_type": "explore",
                },
            ],
        }

        self.tool.execute(args, ctx)

        assert len(self.session_mgr.created_sessions) == 1
        session = self.session_mgr.created_sessions[0]
        assert session["parent_id"] == "parent-123"
        assert "Sub-task" in session["title"]

    def test_parallel_invalid_subagent_type(self):
        """Test that parallel mode rejects invalid subagent_type."""
        ctx = make_tool_context()
        args = {
            "description": "Parallel",
            "prompt": "Do it",
            "subagent_type": "explore",
            "mode": "parallel",
            "tasks": [
                {
                    "description": "Bad task",
                    "prompt": "Work",
                    "subagent_type": "compaction",  # Hidden agent
                },
            ],
        }

        from berserker.tool.base import ToolExecutionError

        with pytest.raises(ToolExecutionError) as exc_info:
            self.tool.execute(args, ctx)

        assert "Invalid subagent_type" in str(exc_info.value)

    def test_parallel_result_formatting(self):
        """Test that parallel results are properly formatted."""
        ctx = make_tool_context()
        args = {
            "description": "Parallel",
            "prompt": "Do it",
            "subagent_type": "explore",
            "mode": "parallel",
            "tasks": [
                {
                    "description": "Task 1",
                    "prompt": "Work 1",
                    "subagent_type": "explore",
                },
            ],
        }

        result = self.tool.execute(args, ctx)

        assert "<task_result" in result.output
        assert "status='completed'" in result.output
        assert "</task_result>" in result.output
        assert result.metadata["mode"] == "parallel"
        assert result.metadata["task_count"] == 1


# ---------------------------------------------------------------------------
# Sequential Mode Tests
# ---------------------------------------------------------------------------


class TestTaskToolSequential:
    """Tests for sequential execution mode."""

    def setup_method(self):
        self.tool = TaskTool()
        self.session_mgr = MockSessionManager()
        self.agent_mgr = MockAgentManager()
        self.tool_registry = MockToolRegistry()

        # Patch imports
        import berserker.agent.manager as mgr_module
        import berserker.session.manager as sess_module
        import berserker.tool.registry as reg_module
        import berserker.agent.orchestrator as orch_module

        self._orig_agent_manager = getattr(mgr_module, "agent_manager", None)
        self._orig_session_manager = getattr(sess_module, "session_manager", None)
        self._orig_registry = getattr(reg_module, "registry", None)
        self._orig_orchestrator = getattr(orch_module, "_orchestrator", None)

        mgr_module.agent_manager = self.agent_mgr
        sess_module.session_manager = self.session_mgr
        reg_module.registry = self.tool_registry
        orch_module._orchestrator = None  # Reset singleton

    def teardown_method(self):
        """Restore original modules and reset singleton."""
        import berserker.agent.manager as mgr_module
        import berserker.session.manager as sess_module
        import berserker.tool.registry as reg_module
        import berserker.agent.orchestrator as orch_module

        if self._orig_agent_manager is not None:
            mgr_module.agent_manager = self._orig_agent_manager
        if self._orig_session_manager is not None:
            sess_module.session_manager = self._orig_session_manager
        if self._orig_registry is not None:
            reg_module.registry = self._orig_registry
        if self._orig_orchestrator is not None:
            orch_module._orchestrator = self._orig_orchestrator
        else:
            orch_module._orchestrator = None

    def test_sequential_mode_basic(self):
        """Test basic sequential execution."""
        ctx = make_tool_context()
        args = {
            "description": "Sequential tasks",
            "prompt": "Do something",
            "subagent_type": "explore",
            "mode": "sequential",
            "tasks": [
                {
                    "description": "Task A",
                    "prompt": "First task",
                    "subagent_type": "explore",
                },
                {
                    "description": "Task B",
                    "prompt": "Second task",
                    "subagent_type": "general",
                },
            ],
        }

        result = self.tool.execute(args, ctx)

        assert "Sequential tasks completed" in result.title
        assert "2 tasks" in result.title
        assert len(self.session_mgr.created_sessions) == 2
        assert len(self.agent_mgr.calls) == 2

    def test_sequential_order_preserved(self):
        """Test that sequential execution preserves order."""
        ctx = make_tool_context()
        args = {
            "description": "Sequential",
            "prompt": "Do it",
            "subagent_type": "explore",
            "mode": "sequential",
            "tasks": [
                {
                    "description": "First",
                    "prompt": "Work 1",
                    "subagent_type": "explore",
                },
                {
                    "description": "Second",
                    "prompt": "Work 2",
                    "subagent_type": "explore",
                },
                {
                    "description": "Third",
                    "prompt": "Work 3",
                    "subagent_type": "explore",
                },
            ],
        }

        self.tool.execute(args, ctx)

        # Check that agents were called in order
        assert len(self.agent_mgr.calls) == 3
        assert "Work 1" in self.agent_mgr.calls[0]["messages"][0].content
        assert "Work 2" in self.agent_mgr.calls[1]["messages"][0].content
        assert "Work 3" in self.agent_mgr.calls[2]["messages"][0].content

    def test_sequential_invalid_subagent_type(self):
        """Test that sequential mode rejects invalid subagent_type."""
        ctx = make_tool_context()
        args = {
            "description": "Sequential",
            "prompt": "Do it",
            "subagent_type": "explore",
            "mode": "sequential",
            "tasks": [
                {
                    "description": "Bad task",
                    "prompt": "Work",
                    "subagent_type": "title",  # Hidden agent
                },
            ],
        }

        from berserker.tool.base import ToolExecutionError

        with pytest.raises(ToolExecutionError) as exc_info:
            self.tool.execute(args, ctx)

        assert "Invalid subagent_type" in str(exc_info.value)

    def test_sequential_result_formatting(self):
        """Test that sequential results are properly formatted."""
        ctx = make_tool_context()
        args = {
            "description": "Sequential",
            "prompt": "Do it",
            "subagent_type": "explore",
            "mode": "sequential",
            "tasks": [
                {
                    "description": "Task 1",
                    "prompt": "Work 1",
                    "subagent_type": "explore",
                },
            ],
        }

        result = self.tool.execute(args, ctx)

        assert "<task_result" in result.output
        assert "status='completed'" in result.output
        assert result.metadata["mode"] == "sequential"
        assert result.metadata["task_count"] == 1


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------


class TestTaskToolValidation:
    """Tests for input validation."""

    def setup_method(self):
        self.tool = TaskTool()
        self.session_mgr = MockSessionManager()
        self.agent_mgr = MockAgentManager()
        self.tool_registry = MockToolRegistry()

        # Patch imports
        import berserker.agent.manager as mgr_module
        import berserker.session.manager as sess_module
        import berserker.tool.registry as reg_module

        self._orig_agent_manager = getattr(mgr_module, "agent_manager", None)
        self._orig_session_manager = getattr(sess_module, "session_manager", None)
        self._orig_registry = getattr(reg_module, "registry", None)

        mgr_module.agent_manager = self.agent_mgr
        sess_module.session_manager = self.session_mgr
        reg_module.registry = self.tool_registry

    def teardown_method(self):
        """Restore original modules."""
        import berserker.agent.manager as mgr_module
        import berserker.session.manager as sess_module
        import berserker.tool.registry as reg_module

        if self._orig_agent_manager is not None:
            mgr_module.agent_manager = self._orig_agent_manager
        if self._orig_session_manager is not None:
            sess_module.session_manager = self._orig_session_manager
        if self._orig_registry is not None:
            reg_module.registry = self._orig_registry

    def test_missing_subagent_type_raises(self):
        """Test that missing subagent_type raises error."""
        ctx = make_tool_context()
        args = {
            "description": "Test",
            "prompt": "Do it",
            # No subagent_type
        }

        from berserker.tool.base import ToolExecutionError

        with pytest.raises(ToolExecutionError) as exc_info:
            self.tool.execute(args, ctx)

        assert "subagent_type is required" in str(exc_info.value)

    def test_unknown_subagent_type_raises(self):
        """Test that unknown subagent_type raises error."""
        ctx = make_tool_context()
        args = {
            "description": "Test",
            "prompt": "Do it",
            "subagent_type": "unknown-agent",
        }

        from berserker.tool.base import ToolExecutionError

        with pytest.raises(ToolExecutionError) as exc_info:
            self.tool.execute(args, ctx)

        assert "Unknown agent type" in str(exc_info.value)

    def test_hidden_subagent_type_raises(self):
        """Test that hidden agents cannot be spawned."""
        ctx = make_tool_context()
        args = {
            "description": "Test",
            "prompt": "Do it",
            "subagent_type": "compaction",
        }

        from berserker.tool.base import ToolExecutionError

        with pytest.raises(ToolExecutionError) as exc_info:
            self.tool.execute(args, ctx)

        assert "Cannot spawn hidden agent" in str(exc_info.value)

    def test_abort_signal_raises(self):
        """Test that abort signal raises error."""
        ctx = make_tool_context()
        ctx.abort.set()  # Set abort before execution

        args = {
            "description": "Test",
            "prompt": "Do it",
            "subagent_type": "explore",
        }

        from berserker.tool.base import ToolExecutionError

        with pytest.raises(ToolExecutionError) as exc_info:
            self.tool.execute(args, ctx)

        assert "Task cancelled" in str(exc_info.value)

    def test_parallel_mode_without_tasks_falls_back_to_single(self):
        """Test that parallel mode without tasks falls back to single."""
        ctx = make_tool_context()
        args = {
            "description": "Test",
            "prompt": "Do it",
            "subagent_type": "explore",
            "mode": "parallel",
            # No tasks array
        }

        result = self.tool.execute(args, ctx)

        # Should execute as single mode
        assert len(self.agent_mgr.calls) == 1
        assert "Task completed" in result.title

    def test_sequential_mode_without_tasks_falls_back_to_single(self):
        """Test that sequential mode without tasks falls back to single."""
        ctx = make_tool_context()
        args = {
            "description": "Test",
            "prompt": "Do it",
            "subagent_type": "explore",
            "mode": "sequential",
            # No tasks array
        }

        result = self.tool.execute(args, ctx)

        # Should execute as single mode
        assert len(self.agent_mgr.calls) == 1
        assert "Task completed" in result.title
