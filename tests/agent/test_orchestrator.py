"""
Tests for berserker.agent.orchestrator module.

Covers:
- OrchestrationMode enum values.
- OrchestrationTask lifecycle (create, cancel, serialize).
- Orchestrator parallel execution concurrency.
- Orchestrator sequential order preservation.
- Orchestrator fan-out/fan-in flow.
- Task cancellation and status tracking.
- get_orchestrator singleton behavior.

Python 3.8.10 compatible: uses type comments, no match/case.
"""

import threading
import time
import pytest

from berserker.agent.orchestrator import (
    OrchestrationMode,
    OrchestrationTask,
    Orchestrator,
    get_orchestrator,
)


# ---------------------------------------------------------------------------
# OrchestrationMode Tests
# ---------------------------------------------------------------------------


class TestOrchestrationMode:
    """Tests for OrchestrationMode enum."""

    def test_enum_values(self):
        """Test all enum values are defined correctly."""
        assert OrchestrationMode.PARALLEL.value == "parallel"
        assert OrchestrationMode.SEQUENTIAL.value == "sequential"
        assert OrchestrationMode.FAN_OUT_FAN_IN.value == "fan_out_fan_in"

    def test_enum_count(self):
        """Test exactly 3 modes exist."""
        assert len(OrchestrationMode) == 3


# ---------------------------------------------------------------------------
# OrchestrationTask Tests
# ---------------------------------------------------------------------------


class TestOrchestrationTask:
    """Tests for OrchestrationTask dataclass."""

    def test_create_task(self):
        """Test creating a task with all fields."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Find auth patterns",
            session_id="sess-1",
            timeout=60,
        )

        assert task.task_id == "task-1"
        assert task.agent_name == "explore"
        assert task.prompt == "Find auth patterns"
        assert task.session_id == "sess-1"
        assert task.timeout == 60
        assert task.result is None
        assert task.status == "pending"

    def test_create_task_without_timeout(self):
        """Test creating a task without timeout."""
        task = OrchestrationTask(
            task_id="task-2",
            agent_name="general",
            prompt="Do something",
            session_id="sess-2",
        )

        assert task.timeout is None

    def test_task_cancel(self):
        """Test task cancellation."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Test",
            session_id="sess-1",
        )

        assert task.status == "pending"
        assert not task.is_cancelled()

        task.cancel()

        assert task.is_cancelled()
        assert task.status == "cancelled"

    def test_task_cancel_already_cancelled(self):
        """Test cancelling an already cancelled task."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Test",
            session_id="sess-1",
        )

        task.cancel()
        task.cancel()  # Should not raise

        assert task.is_cancelled()
        assert task.status == "cancelled"

    def test_task_to_dict(self):
        """Test task serialization to dict."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Test prompt",
            session_id="sess-1",
            timeout=30,
        )
        task.result = {"content": "done"}
        task.status = "completed"

        d = task.to_dict()

        assert d["task_id"] == "task-1"
        assert d["agent_name"] == "explore"
        assert d["prompt"] == "Test prompt"
        assert d["session_id"] == "sess-1"
        assert d["timeout"] == 30
        assert d["result"] == {"content": "done"}
        assert d["status"] == "completed"

    def test_task_repr(self):
        """Test task string representation."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Test",
            session_id="sess-1",
        )

        repr_str = repr(task)
        assert "task-1" in repr_str
        assert "explore" in repr_str
        assert "pending" in repr_str


# ---------------------------------------------------------------------------
# Mock Agent Manager for Testing
# ---------------------------------------------------------------------------


class MockAgentManager(object):
    """Mock agent manager that simulates execution with configurable behavior."""

    def __init__(self):
        # type: () -> None
        self.calls = []  # type: list
        self.delay = 0  # type: float
        self.fail_on = set()  # type: set
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
        """Mock execute that records calls and optionally fails/delays."""
        with self._lock:
            self.calls.append({
                "agent_name": agent_name,
                "session_id": session_id,
                "messages": messages,
            })

        # Check abort event
        if abort_event is not None and abort_event.is_set():
            raise Exception("Aborted")

        # Simulate delay
        if self.delay > 0:
            time.sleep(self.delay)

        # Check if this agent should fail
        if agent_name in self.fail_on:
            raise Exception("Simulated failure for agent: {}".format(agent_name))

        return {
            "content": "Result from {} in session {}".format(agent_name, session_id),
            "status": "success",
        }


class MockToolRegistry(object):
    """Mock tool registry for testing."""
    pass


# ---------------------------------------------------------------------------
# Orchestrator Tests
# ---------------------------------------------------------------------------


class TestOrchestratorParallel:
    """Tests for parallel execution."""

    def setup_method(self):
        self.mock_agent = MockAgentManager()
        self.mock_registry = MockToolRegistry()
        self.orchestrator = Orchestrator(self.mock_agent, self.mock_registry)

    def test_parallel_empty(self):
        """Test parallel execution with no tasks."""
        results = self.orchestrator.execute_parallel([])
        assert results == []

    def test_parallel_single_task(self):
        """Test parallel execution with a single task."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Find patterns",
            session_id="sess-1",
        )

        results = self.orchestrator.execute_parallel([task])

        assert len(results) == 1
        assert results[0]["task_id"] == "task-1"
        assert results[0]["status"] == "completed"
        assert results[0]["result"] is not None
        assert "explore" in results[0]["result"]["content"]

    def test_parallel_multiple_tasks(self):
        """Test parallel execution with multiple tasks."""
        tasks = [
            OrchestrationTask(
                task_id="task-{}".format(i),
                agent_name="explore",
                prompt="Task {}".format(i),
                session_id="sess-{}".format(i),
            )
            for i in range(5)
        ]

        results = self.orchestrator.execute_parallel(tasks)

        assert len(results) == 5
        result_ids = {r["task_id"] for r in results}
        assert result_ids == {"task-0", "task-1", "task-2", "task-3", "task-4"}

    def test_parallel_concurrency(self):
        """Test that parallel tasks actually run concurrently."""
        self.mock_agent.delay = 0.2  # 200ms per task

        tasks = [
            OrchestrationTask(
                task_id="task-{}".format(i),
                agent_name="explore",
                prompt="Task {}".format(i),
                session_id="sess-{}".format(i),
            )
            for i in range(5)
        ]

        start = time.time()
        results = self.orchestrator.execute_parallel(tasks)
        elapsed = time.time() - start

        # If truly parallel, should take ~200ms, not ~1000ms (5 * 200ms)
        assert elapsed < 0.8, "Tasks did not run in parallel: {:.2f}s".format(elapsed)
        assert len(results) == 5

    def test_parallel_preserves_order(self):
        """Test that results are returned in original task order."""
        tasks = [
            OrchestrationTask(
                task_id="task-{}".format(i),
                agent_name="explore",
                prompt="Task {}".format(i),
                session_id="sess-{}".format(i),
            )
            for i in range(3)
        ]

        results = self.orchestrator.execute_parallel(tasks)

        assert results[0]["task_id"] == "task-0"
        assert results[1]["task_id"] == "task-1"
        assert results[2]["task_id"] == "task-2"

    def test_parallel_with_failure(self):
        """Test parallel execution when some tasks fail."""
        self.mock_agent.fail_on = {"fail-agent"}

        tasks = [
            OrchestrationTask(
                task_id="task-ok",
                agent_name="explore",
                prompt="OK task",
                session_id="sess-1",
            ),
            OrchestrationTask(
                task_id="task-fail",
                agent_name="fail-agent",
                prompt="Failing task",
                session_id="sess-2",
            ),
        ]

        results = self.orchestrator.execute_parallel(tasks)

        assert len(results) == 2
        ok_result = next(r for r in results if r["task_id"] == "task-ok")
        fail_result = next(r for r in results if r["task_id"] == "task-fail")

        assert ok_result["status"] == "completed"
        assert fail_result["status"] == "failed"
        assert "error" in fail_result["result"]


class TestOrchestratorSequential:
    """Tests for sequential execution."""

    def setup_method(self):
        self.mock_agent = MockAgentManager()
        self.mock_registry = MockToolRegistry()
        self.orchestrator = Orchestrator(self.mock_agent, self.mock_registry)

    def test_sequential_empty(self):
        """Test sequential execution with no tasks."""
        results = self.orchestrator.execute_sequential([])
        assert results == []

    def test_sequential_single_task(self):
        """Test sequential execution with a single task."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Find patterns",
            session_id="sess-1",
        )

        results = self.orchestrator.execute_sequential([task])

        assert len(results) == 1
        assert results[0]["status"] == "completed"

    def test_sequential_order_preserved(self):
        """Test that sequential execution preserves order."""
        tasks = [
            OrchestrationTask(
                task_id="task-{}".format(i),
                agent_name="explore",
                prompt="Task {}".format(i),
                session_id="sess-{}".format(i),
            )
            for i in range(5)
        ]

        results = self.orchestrator.execute_sequential(tasks)

        assert len(results) == 5
        for i, result in enumerate(results):
            assert result["task_id"] == "task-{}".format(i)

    def test_sequential_stops_on_failure(self):
        """Test that sequential execution stops after a failure."""
        self.mock_agent.fail_on = {"fail-agent"}

        tasks = [
            OrchestrationTask(
                task_id="task-1",
                agent_name="explore",
                prompt="First",
                session_id="sess-1",
            ),
            OrchestrationTask(
                task_id="task-2",
                agent_name="fail-agent",
                prompt="Second (will fail)",
                session_id="sess-2",
            ),
            OrchestrationTask(
                task_id="task-3",
                agent_name="explore",
                prompt="Third (should not run)",
                session_id="sess-3",
            ),
        ]

        results = self.orchestrator.execute_sequential(tasks)

        # Should have 2 results: task-1 completed + task-2 failed (task-3 not executed, not returned)
        assert len(results) == 2
        assert results[0]["status"] == "completed"
        assert results[1]["status"] == "failed"

    def test_sequential_respects_cancellation(self):
        """Test that sequential execution respects pre-cancelled tasks."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Cancelled before start",
            session_id="sess-1",
        )
        task.cancel()

        results = self.orchestrator.execute_sequential([task])

        assert len(results) == 1
        assert results[0]["status"] == "cancelled"


class TestOrchestratorFanOutFanIn:
    """Tests for fan-out/fan-in execution."""

    def setup_method(self):
        self.mock_agent = MockAgentManager()
        self.mock_registry = MockToolRegistry()
        self.orchestrator = Orchestrator(self.mock_agent, self.mock_registry)

    def test_fan_out_fan_in_empty(self):
        """Test fan-out/fan-in with no fan-out tasks."""
        result = self.orchestrator.execute_fan_out_fan_in(
            fan_out_tasks=[],
            fan_in_agent="general",
            fan_in_prompt="Aggregate results",
        )

        assert result["fan_out_results"] == []
        assert result["fan_in_result"] is None
        assert "error" in result

    def test_fan_out_fan_in_basic(self):
        """Test basic fan-out/fan-in flow."""
        fan_out_tasks = [
            OrchestrationTask(
                task_id="fan-{}".format(i),
                agent_name="explore",
                prompt="Fan task {}".format(i),
                session_id="fan-sess-{}".format(i),
            )
            for i in range(3)
        ]

        result = self.orchestrator.execute_fan_out_fan_in(
            fan_out_tasks=fan_out_tasks,
            fan_in_agent="general",
            fan_in_prompt="Summarize findings",
            fan_in_session_id="fan-in-sess",
        )

        assert len(result["fan_out_results"]) == 3
        assert result["fan_in_result"] is not None
        assert result["fan_in_result"]["task_id"] == "fan-in-aggregation"
        assert result["fan_in_result"]["status"] == "completed"

    def test_fan_out_fan_in_aggregation_prompt_includes_results(self):
        """Test that fan-in prompt includes fan-out results."""
        fan_out_tasks = [
            OrchestrationTask(
                task_id="fan-1",
                agent_name="explore",
                prompt="Research topic A",
                session_id="fan-sess-1",
            ),
        ]

        result = self.orchestrator.execute_fan_out_fan_in(
            fan_out_tasks=fan_out_tasks,
            fan_in_agent="general",
            fan_in_prompt="Summarize",
            fan_in_session_id="fan-in-sess",
        )

        # Check the fan-in agent was called with a prompt containing results
        fan_in_call = None
        for call in self.mock_agent.calls:
            if call["session_id"] == "fan-in-sess":
                fan_in_call = call
                break

        assert fan_in_call is not None
        # The prompt should contain the fan-out task results
        messages = fan_in_call["messages"]
        prompt_content = messages[0].content if messages else ""
        assert "fan-1" in prompt_content
        # The fan-in prompt includes result content from fan-out tasks
        assert "Result from explore" in prompt_content


class TestOrchestratorCancellation:
    """Tests for task cancellation."""

    def setup_method(self):
        self.mock_agent = MockAgentManager()
        self.mock_registry = MockToolRegistry()
        self.orchestrator = Orchestrator(self.mock_agent, self.mock_registry)

    def test_cancel_nonexistent_task(self):
        """Test cancelling a task that doesn't exist."""
        result = self.orchestrator.cancel("nonexistent-task")
        assert result is False

    def test_cancel_before_execution(self):
        """Test cancelling a task before it starts."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Will be cancelled",
            session_id="sess-1",
        )

        # Register the task manually
        self.orchestrator._tasks["task-1"] = task

        result = self.orchestrator.cancel("task-1")
        assert result is True
        assert task.status == "cancelled"

    def test_get_status_existing_task(self):
        """Test getting status of an existing task."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Test",
            session_id="sess-1",
        )
        self.orchestrator._tasks["task-1"] = task

        status = self.orchestrator.get_status("task-1")

        assert status is not None
        assert status["task_id"] == "task-1"
        assert status["status"] == "pending"

    def test_get_status_nonexistent_task(self):
        """Test getting status of a nonexistent task."""
        status = self.orchestrator.get_status("nonexistent")
        assert status is None

    def test_get_all_statuses(self):
        """Test getting statuses for all tasks."""
        task1 = OrchestrationTask(
            task_id="task-1",
            agent_name="explore",
            prompt="Test 1",
            session_id="sess-1",
        )
        task2 = OrchestrationTask(
            task_id="task-2",
            agent_name="general",
            prompt="Test 2",
            session_id="sess-2",
        )
        self.orchestrator._tasks["task-1"] = task1
        self.orchestrator._tasks["task-2"] = task2

        statuses = self.orchestrator.get_all_statuses()

        assert len(statuses) == 2
        assert "task-1" in statuses
        assert "task-2" in statuses


class TestGetOrchestratorSingleton:
    """Tests for the get_orchestrator singleton."""

    def setup_method(self):
        # Reset singleton state before each test
        import berserker.agent.orchestrator as orch_module
        orch_module._orchestrator = None

    def test_singleton_requires_args_on_first_call(self):
        """Test that first call without args raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_orchestrator()

        assert "not initialized" in str(exc_info.value)

    def test_singleton_initialization(self):
        """Test singleton initialization with args."""
        mock_agent = MockAgentManager()
        mock_registry = MockToolRegistry()

        orch = get_orchestrator(mock_agent, mock_registry)

        assert orch is not None
        assert isinstance(orch, Orchestrator)

    def test_singleton_returns_same_instance(self):
        """Test that subsequent calls return the same instance."""
        mock_agent = MockAgentManager()
        mock_registry = MockToolRegistry()

        orch1 = get_orchestrator(mock_agent, mock_registry)
        orch2 = get_orchestrator()  # No args needed after init

        assert orch1 is orch2
