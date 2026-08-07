"""
Thread safety tests for berserker.agent.orchestrator module.

Covers:
- Concurrent orchestration from multiple threads.
- No race conditions in task registration and status updates.
- Thread-safe task cancellation during execution.
- Concurrent status reads during task execution.
- No data corruption in result collection under heavy concurrency.

Python 3.8.10 compatible: uses type comments, no match/case.
"""

import threading
import time
import pytest

from berserker.agent.orchestrator import OrchestrationTask, Orchestrator


# ---------------------------------------------------------------------------
# Mock Components
# ---------------------------------------------------------------------------


class MockAgentManager(object):
    """Mock agent manager with configurable delay for thread safety testing."""

    def __init__(self):
        # type: () -> None
        self.calls = []  # type: list
        self.delay = 0.05  # type: float
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
        """Mock execute with delay to simulate real work."""
        if abort_event is not None and abort_event.is_set():
            raise Exception("Aborted")

        time.sleep(self.delay)

        with self._lock:
            self.calls.append({
                "agent_name": agent_name,
                "session_id": session_id,
            })

        return {
            "content": "Result from {} in {}".format(agent_name, session_id),
            "status": "success",
        }


class MockToolRegistry(object):
    """Mock tool registry."""
    pass


# ---------------------------------------------------------------------------
# Thread Safety Tests
# ---------------------------------------------------------------------------


class TestOrchestratorThreadSafety:
    """Tests for thread-safe orchestration."""

    def setup_method(self):
        """Create a fresh orchestrator for each test."""
        self.mock_agent = MockAgentManager()
        self.mock_registry = MockToolRegistry()
        self.orchestrator = Orchestrator(self.mock_agent, self.mock_registry)

    def test_concurrent_parallel_executions(self):
        """Test multiple parallel executions from different threads."""
        errors = []  # type: list
        all_results = []  # type: list
        lock = threading.Lock()

        def run_parallel(thread_id):
            # type: (int) -> None
            try:
                tasks = [
                    OrchestrationTask(
                        task_id="t{}-{}".format(thread_id, i),
                        agent_name="explore",
                        prompt="Task {}-{}".format(thread_id, i),
                        session_id="sess-{}-{}".format(thread_id, i),
                    )
                    for i in range(3)
                ]
                results = self.orchestrator.execute_parallel(tasks)
                with lock:
                    all_results.extend(results)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for t in range(5):
            thread = threading.Thread(target=run_parallel, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, "Errors occurred: {}".format(errors)
        # 5 threads * 3 tasks each = 15 results
        assert len(all_results) == 15

    def test_concurrent_status_reads_during_execution(self):
        """Test concurrent status reads while tasks are executing."""
        self.mock_agent.delay = 0.1  # 100ms per task

        task = OrchestrationTask(
            task_id="long-task",
            agent_name="explore",
            prompt="Long running task",
            session_id="sess-1",
        )

        status_reads = []  # type: list
        stop_reading = threading.Event()
        read_errors = []  # type: list

        def read_status():
            # type: () -> None
            try:
                while not stop_reading.is_set():
                    status = self.orchestrator.get_status("long-task")
                    if status is not None:
                        status_reads.append(status["status"])
                    time.sleep(0.01)  # Read every 10ms
            except Exception as e:
                read_errors.append(e)

        # Start status reader
        reader = threading.Thread(target=read_status)
        reader.start()

        # Execute the task
        self.orchestrator.execute_parallel([task])

        # Stop reading
        stop_reading.set()
        reader.join()

        assert len(read_errors) == 0, "Read errors: {}".format(read_errors)
        # Should have read some statuses (at least "pending" or "running" or "completed")
        assert len(status_reads) > 0

    def test_concurrent_cancel_during_execution(self):
        """Test cancelling tasks while they are executing."""
        self.mock_agent.delay = 0.3  # 300ms per task

        task = OrchestrationTask(
            task_id="cancel-me",
            agent_name="explore",
            prompt="Will be cancelled",
            session_id="sess-1",
        )

        cancel_errors = []  # type: list

        def cancel_after_delay():
            # type: () -> None
            try:
                time.sleep(0.05)  # Wait 50ms then cancel
                self.orchestrator.cancel("cancel-me")
            except Exception as e:
                cancel_errors.append(e)

        # Start canceller
        canceller = threading.Thread(target=cancel_after_delay)
        canceller.start()

        # Execute the task (should be cancelled mid-execution)
        results = self.orchestrator.execute_parallel([task])

        canceller.join()
        assert len(cancel_errors) == 0, "Cancel errors: {}".format(cancel_errors)

        # Task should be either cancelled or completed (race condition)
        assert results[0]["status"] in ("cancelled", "completed", "failed")

    def test_concurrent_get_all_statuses(self):
        """Test concurrent get_all_statuses calls."""
        # Pre-register some tasks
        for i in range(10):
            task = OrchestrationTask(
                task_id="task-{}".format(i),
                agent_name="explore",
                prompt="Task {}".format(i),
                session_id="sess-{}".format(i),
            )
            self.orchestrator._tasks["task-{}".format(i)] = task

        errors = []  # type: list
        status_snapshots = []  # type: list
        lock = threading.Lock()

        def read_all_statuses():
            # type: () -> None
            try:
                for _ in range(20):
                    statuses = self.orchestrator.get_all_statuses()
                    with lock:
                        status_snapshots.append(len(statuses))
                    time.sleep(0.001)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for _ in range(5):
            thread = threading.Thread(target=read_all_statuses)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, "Errors: {}".format(errors)
        # All snapshots should have 10 tasks
        assert all(count == 10 for count in status_snapshots)

    def test_no_task_duplication_under_concurrency(self):
        """Test that tasks are not duplicated under concurrent access."""
        errors = []  # type: list
        lock = threading.Lock()
        results = []  # type: list

        def register_and_execute(thread_id):
            # type: (int) -> None
            try:
                task = OrchestrationTask(
                    task_id="unique-{}".format(thread_id),
                    agent_name="explore",
                    prompt="Task {}".format(thread_id),
                    session_id="sess-{}".format(thread_id),
                )
                task_results = self.orchestrator.execute_parallel([task])
                with lock:
                    results.extend(task_results)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for t in range(20):
            thread = threading.Thread(target=register_and_execute, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, "Errors: {}".format(errors)

        # Each task_id must appear exactly once across all results.
        # Note: completed tasks are cleaned up from get_all_statuses() by
        # design, so execute_parallel() results are the source of truth here.
        task_ids = [r.get("task_id") for r in results]
        assert len(task_ids) == 20
        assert len(set(task_ids)) == 20
        assert all(r.get("status") == "completed" for r in results)

    def test_concurrent_cancel_nonexistent(self):
        """Test concurrent cancel calls on nonexistent tasks."""
        errors = []  # type: list

        def cancel_random():
            # type: () -> None
            try:
                # Should return False, not raise
                self.orchestrator.cancel("nonexistent-{}".format(threading.current_thread().ident))
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(10):
            thread = threading.Thread(target=cancel_random)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, "Errors: {}".format(errors)

    def test_mixed_parallel_and_sequential_concurrently(self):
        """Test running parallel and sequential executions concurrently."""
        errors = []  # type: list
        results_collected = []  # type: list
        lock = threading.Lock()

        def run_parallel_mode():
            # type: () -> None
            try:
                tasks = [
                    OrchestrationTask(
                        task_id="par-{}".format(i),
                        agent_name="explore",
                        prompt="Parallel {}".format(i),
                        session_id="par-sess-{}".format(i),
                    )
                    for i in range(3)
                ]
                results = self.orchestrator.execute_parallel(tasks)
                with lock:
                    results_collected.append(("parallel", len(results)))
            except Exception as e:
                with lock:
                    errors.append(e)

        def run_sequential_mode():
            # type: () -> None
            try:
                tasks = [
                    OrchestrationTask(
                        task_id="seq-{}".format(i),
                        agent_name="explore",
                        prompt="Sequential {}".format(i),
                        session_id="seq-sess-{}".format(i),
                    )
                    for i in range(3)
                ]
                results = self.orchestrator.execute_sequential(tasks)
                with lock:
                    results_collected.append(("sequential", len(results)))
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=run_parallel_mode))
            threads.append(threading.Thread(target=run_sequential_mode))

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, "Errors: {}".format(errors)
        assert len(results_collected) == 6  # 3 parallel + 3 sequential

    def test_abort_event_propagation(self):
        """Test that abort events are properly propagated to agent execution."""
        task = OrchestrationTask(
            task_id="abort-test",
            agent_name="explore",
            prompt="Will be aborted",
            session_id="sess-1",
        )

        # Pre-set the abort event
        task._abort_event.set()

        results = self.orchestrator.execute_parallel([task])

        assert len(results) == 1
        # Task should be cancelled or failed due to abort
        assert results[0]["status"] in ("cancelled", "failed")
