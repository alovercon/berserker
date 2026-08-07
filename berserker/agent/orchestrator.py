"""
Concurrent orchestration for berserker agent system.

Provides:
- OrchestrationMode enum for execution patterns.
- OrchestrationTask dataclass for task tracking.
- Orchestrator class for parallel, sequential, and fan-out/fan-in execution.

Thread-safe with proper synchronization (threading.Lock, threading.Event).
Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from berserker.provider.base import ChatMessage

logger = logging.getLogger(__name__)


class OrchestrationMode(Enum):
    """Execution modes for orchestration.

    Attributes:
        PARALLEL: Run all tasks concurrently in separate threads.
        SEQUENTIAL: Run tasks one after another in order.
        FAN_OUT_FAN_IN: Fan-out tasks in parallel, then aggregate results.
    """
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"
    FAN_OUT_FAN_IN = "fan_out_fan_in"


class OrchestrationTask(object):
    """Represents a single task in an orchestration workflow.

    Attributes:
        task_id: Unique identifier for the task.
        agent_name: Name of the agent to execute.
        prompt: The prompt/task description for the agent.
        session_id: Session ID for the task execution.
        timeout: Optional timeout in seconds.
        result: The execution result (set after completion).
        status: Current status — 'pending', 'running', 'completed', 'failed', 'cancelled'.
    """

    def __init__(
        self,
        task_id,  # type: str
        agent_name,  # type: str
        prompt,  # type: str
        session_id,  # type: str
        timeout=None,  # type: Optional[int]
    ):
        # type: (...) -> None
        self.task_id = task_id
        self.agent_name = agent_name
        self.prompt = prompt
        self.session_id = session_id
        self.timeout = timeout
        self.result = None  # type: Optional[Dict[str, Any]]
        self.status = "pending"  # type: str
        self._abort_event = threading.Event()

    def cancel(self):
        # type: () -> None
        """Signal cancellation for this task."""
        self._abort_event.set()
        if self.status in ("pending", "running"):
            self.status = "cancelled"
            logger.info("Task '%s' cancelled", self.task_id)

    def is_cancelled(self):
        # type: () -> bool
        """Check if this task has been cancelled."""
        return self._abort_event.is_set()

    def to_dict(self):
        # type: () -> Dict[str, Any]
        """Serialize task to dictionary."""
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "prompt": self.prompt,
            "session_id": self.session_id,
            "timeout": self.timeout,
            "result": self.result,
            "status": self.status,
        }

    def __repr__(self):
        # type: () -> str
        return "OrchestrationTask(id={!r}, agent={!r}, status={!r})".format(
            self.task_id, self.agent_name, self.status
        )


class Orchestrator(object):
    """Manages concurrent execution of agent tasks.

    Supports parallel, sequential, and fan-out/fan-in execution patterns
    with thread-safe task tracking and cancellation.

    Usage:
        orchestrator = Orchestrator(agent_manager, tool_registry)
        results = orchestrator.execute_parallel(tasks)
    """

    def __init__(self, agent_manager, tool_registry):
        # type: (Any, Any) -> None
        """Initialize the orchestrator.

        Args:
            agent_manager: AgentManager instance for executing agents.
            tool_registry: ToolRegistry instance for tool execution.
        """
        self._agent_manager = agent_manager
        self._tool_registry = tool_registry
        self._tasks = {}  # type: Dict[str, OrchestrationTask]
        self._lock = threading.Lock()
        self._threads = {}  # type: Dict[str, threading.Thread]

    def execute_parallel(self, tasks):
        # type: (List[OrchestrationTask]) -> List[Dict[str, Any]]
        """Execute multiple tasks concurrently in separate threads.

        All tasks start simultaneously and run in parallel. The method
        blocks until all tasks complete, fail, or are cancelled.

        Args:
            tasks: List of OrchestrationTask objects to execute.

        Returns:
            List of result dicts, one per task (in original order).
        """
        if not tasks:
            return []

        # Guard against unbounded growth
        if len(self._tasks) > 1000:
            self._cleanup_completed()

        # Register tasks
        with self._lock:
            for task in tasks:
                self._tasks[task.task_id] = task

        # Start all threads
        threads = []  # type: List[threading.Thread]
        for task in tasks:
            thread = threading.Thread(
                target=self._execute_single,
                args=(task,),
                name="orch-parallel-{}".format(task.task_id[:8]),
            )
            threads.append(thread)
            with self._lock:
                self._threads[task.task_id] = thread
            thread.start()

        # Wait for all to complete
        logger.info(
            "[ORCH_PARALLEL] Waiting for %d parallel tasks to complete...",
            len(threads),
        )
        for i, thread in enumerate(threads):
            thread.join()
            logger.info(
                "[ORCH_PARALLEL] Task %d/%d thread joined",
                i + 1, len(threads),
            )

        # Collect results in order
        results = []  # type: List[Dict[str, Any]]
        for task in tasks:
            results.append(task.to_dict())

        completed = sum(1 for r in results if r.get("status") == "completed")
        failed = sum(1 for r in results if r.get("status") == "failed")
        logger.info(
            "[ORCH_PARALLEL] All done: %d total, %d completed, %d failed",
            len(results), completed, failed,
        )

        self._cleanup_completed()
        return results

    def execute_sequential(self, tasks):
        # type: (List[OrchestrationTask]) -> List[Dict[str, Any]]
        """Execute tasks one after another in order.

        Each task runs to completion before the next begins.
        Later tasks can depend on earlier results if needed.

        Args:
            tasks: List of OrchestrationTask objects to execute.

        Returns:
            List of result dicts, in execution order.
        """
        if not tasks:
            return []

        results = []  # type: List[Dict[str, Any]]

        for task in tasks:
            # Check for cancellation before starting
            if task.is_cancelled():
                task.status = "cancelled"
                results.append(task.to_dict())
                continue

            with self._lock:
                self._tasks[task.task_id] = task

            self._execute_single(task)
            results.append(task.to_dict())

            # Check if we should stop (e.g., on failure)
            if task.status == "failed":
                logger.warning(
                    "Sequential execution stopped after task '%s' failed",
                    task.task_id,
                )
                # Mark remaining tasks as cancelled
                break

        self._cleanup_completed()
        return results

    def execute_fan_out_fan_in(
        self,
        fan_out_tasks,  # type: List[OrchestrationTask]
        fan_in_agent,  # type: str
        fan_in_prompt,  # type: str
        fan_in_session_id=None,  # type: Optional[str]
    ):
        # type: (...) -> Dict[str, Any]
        """Execute fan-out tasks in parallel, then aggregate with a fan-in agent.

        Phase 1 (Fan-Out): All fan_out_tasks run concurrently.
        Phase 2 (Fan-In): Results are collected and passed to a fan-in agent
                         for aggregation/synthesis.

        Args:
            fan_out_tasks: List of OrchestrationTask objects for fan-out phase.
            fan_in_agent: Agent name for the fan-in aggregation step.
            fan_in_prompt: Prompt for the fan-in agent, should include
                          instructions on how to aggregate results.
            fan_in_session_id: Optional session ID for fan-in execution.
                              Creates a new one if not provided.

        Returns:
            Dict with 'fan_out_results' (list of task dicts) and
            'fan_in_result' (the aggregation result).
        """
        if not fan_out_tasks:
            return {
                "fan_out_results": [],
                "fan_in_result": None,
                "error": "No fan-out tasks provided",
            }

        # Phase 1: Fan-out (parallel execution)
        fan_out_results = self.execute_parallel(fan_out_tasks)

        # Build aggregation prompt with results
        result_summaries = []  # type: List[str]
        for task_result in fan_out_results:
            task_id = task_result.get("task_id", "unknown")
            status = task_result.get("status", "unknown")
            result_data = task_result.get("result", {})
            content = ""
            if isinstance(result_data, dict):
                content = result_data.get("content", "")
            elif isinstance(result_data, str):
                content = result_data

            result_summaries.append(
                "Task '{}' (status: {}):\n{}".format(
                    task_id, status, content
                )
            )

        aggregation_context = "\n\n---\n\n".join(result_summaries)
        full_fan_in_prompt = "{}\n\n---\n\nAggregated results from fan-out tasks:\n\n{}".format(
            fan_in_prompt, aggregation_context
        )

        # Phase 2: Fan-in (sequential aggregation)
        fan_in_session = fan_in_session_id or "fan-in-{}".format(uuid.uuid4().hex[:8])
        fan_in_task = OrchestrationTask(
            task_id="fan-in-aggregation",
            agent_name=fan_in_agent,
            prompt=full_fan_in_prompt,
            session_id=fan_in_session,
        )

        with self._lock:
            self._tasks[fan_in_task.task_id] = fan_in_task

        self._execute_single(fan_in_task)

        self._cleanup_completed()
        return {
            "fan_out_results": fan_out_results,
            "fan_in_result": fan_in_task.to_dict(),
        }

    def cancel(self, task_id):
        # type: (str) -> bool
        """Cancel a running or pending task.

        Args:
            task_id: The task to cancel.

        Returns:
            True if the task was found and cancelled, False if not found.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.cancel()
            return True

    def get_status(self, task_id):
        # type: (str) -> Optional[Dict[str, Any]]
        """Get the current status of a task.

        Args:
            task_id: The task to query.

        Returns:
            Dict with task status info, or None if not found.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return task.to_dict()

    def get_all_statuses(self):
        # type: () -> Dict[str, Dict[str, Any]]
        """Get statuses for all tracked tasks.

        Returns:
            Dict mapping task_id to status dict.
        """
        with self._lock:
            return {
                task_id: task.to_dict()
                for task_id, task in self._tasks.items()
            }

    def _cleanup_completed(self):
        # type: () -> None
        """Remove completed/cancelled/failed tasks and their threads."""
        with self._lock:
            done = [tid for tid, task in self._tasks.items()
                    if task.status in ("completed", "failed", "cancelled")]
            for tid in done:
                self._tasks.pop(tid, None)
                self._threads.pop(tid, None)

    def _execute_single(self, task):
        # type: (OrchestrationTask) -> None
        """Execute a single task, handling status and errors.

        This is the internal worker method called by threads.

        Args:
            task: The OrchestrationTask to execute.
        """
        # Check cancellation before starting
        if task.is_cancelled():
            task.status = "cancelled"
            logger.info("Task '%s' cancelled before execution", task.task_id)
            return

        task.status = "running"
        logger.info(
            "Starting task '%s' (agent: %s)", task.task_id, task.agent_name
        )

        try:
            # Build messages for the agent
            messages = [
                ChatMessage(role="user", content=task.prompt),
            ]

            # Log prompt summary for debugging
            logger.info(
                "[SUBAGENT_START] task=%s, agent=%s, prompt_len=%d, prompt_preview=%s",
                task.task_id, task.agent_name,
                len(task.prompt), repr(task.prompt[:150]),
            )

            # Execute the agent
            result = self._agent_manager.execute(
                agent_name=task.agent_name,
                messages=messages,
                session_id=task.session_id,
                tool_registry=self._tool_registry,
                on_tool_call=None,
                abort_event=task._abort_event,
            )

            # Log result summary
            content = (result or {}).get("content", "")
            finish = (result or {}).get("finish_reason", "unknown")
            logger.info(
                "[SUBAGENT_END] task=%s, agent=%s, status=%s, finish=%s, content_len=%d",
                task.task_id, task.agent_name,
                "completed" if not task.is_cancelled() else "cancelled",
                finish, len(content or ""),
            )

            # Write the terminal state only while the task is still
            # 'running': cancel() (e.g. from the task tool's abort watchdog)
            # may already have set a terminal state, and a late worker must
            # not overwrite that verdict (cancellation marks the task; it
            # cannot abort the in-flight LLM call).
            if task.status != "running":
                logger.info(
                    "Task '%s' finished after its status was already set to '%s'; "
                    "keeping that terminal state",
                    task.task_id, task.status,
                )
                return

            # Check if execution was aborted
            if task.is_cancelled():
                task.status = "cancelled"
                task.result = None
                logger.info("Task '%s' cancelled during execution", task.task_id)
                return

            task.result = result
            task.status = "completed"
            logger.info("Task '%s' completed successfully", task.task_id)

        except Exception as exc:
            if task.status != "running":
                # Terminal state already set (e.g. external cancel) — keep it.
                logger.info(
                    "Task '%s' raised after its status was already set to '%s'; "
                    "keeping that terminal state (%s)",
                    task.task_id, task.status, str(exc),
                )
            elif task.is_cancelled():
                task.status = "cancelled"
                task.result = None
                logger.info("Task '%s' cancelled during execution", task.task_id)
            else:
                task.status = "failed"
                task.result = {"error": str(exc)}
                logger.error(
                    "Task '%s' failed: %s", task.task_id, str(exc)
                )


# Module-level singleton (lazy initialization)
_orchestrator = None  # type: Optional[Orchestrator]
_orchestrator_lock = threading.Lock()  # type: threading.Lock


def get_orchestrator(agent_manager=None, tool_registry=None):
    # type: (Optional[Any], Optional[Any]) -> Orchestrator
    """Get or create the orchestrator singleton.

    Args:
        agent_manager: AgentManager instance (required on first call).
        tool_registry: ToolRegistry instance (required on first call).

    Returns:
        Orchestrator singleton instance.

    Raises:
        ValueError: If singleton not initialized and no args provided.
    """
    global _orchestrator

    if _orchestrator is not None:
        return _orchestrator

    with _orchestrator_lock:
        if _orchestrator is not None:
            return _orchestrator

        if agent_manager is None or tool_registry is None:
            raise ValueError(
                "Orchestrator not initialized. Call get_orchestrator(agent_manager, tool_registry) first."
            )

        _orchestrator = Orchestrator(agent_manager, tool_registry)
        logger.info("Orchestrator singleton initialized")
        return _orchestrator
