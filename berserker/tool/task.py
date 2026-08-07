"""
Task tool for berserker — subagent delegation with isolated sessions.

Enables primary agents to spawn specialized subagents (general, explore)
in isolated sub-sessions with parent-child linking and controlled permissions.

Supports orchestration modes:
- Single task (backward compatible): launches one subagent synchronously.
- Parallel mode: launches multiple subagents concurrently.
- Sequential mode: launches subagents one after another.

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Dict, List, Optional

from berserker.provider.base import ChatMessage
from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolExecutionError, ToolResult

logger = logging.getLogger(__name__)


def _format_execution_stats(result):
    # type: (Dict[str, Any]) -> str
    """One-line execution summary for a subagent run.

    Zero tool calls means the reply is unverified prose — flag it loudly so
    the delegating agent checks the claims (e.g. files actually written)
    before trusting them."""
    count = result.get("tool_calls_count", 0) or 0
    tools_used = result.get("tools_used") or {}
    if count:
        detail = ", ".join(
            "{}×{}".format(name, n) for name, n in sorted(tools_used.items()))
        return "执行统计: {} 次工具调用 ({})".format(count, detail)
    return ("[注意] 子代理本次执行未调用任何工具 — 其回复未经过任何实际操作验证; "
            "涉及文件读写/命令执行的结论请先核实（如用 ls/read 抽查落盘）再采信。")


def _get_subagent_timeout():
    # type: () -> int
    """Read subagent timeout from config, falling back to 1200s (20 minutes)."""
    try:
        from berserker.config import load_config

        config = load_config()
        timeout = config.get("subagent_timeout")
        if timeout is not None:
            return int(timeout)
    except Exception as e:
        logger.warning("Failed to load subagent_timeout from config: %s", e)
        pass
    return 1200


def _notify_subagent_start(ctx, description, subagent_type, mode="single"):
    # type: (ToolContext, str, str, str) -> None
    """Send a UI notification that a subagent execution is starting.

    Uses the on_tool_call callback from ctx.extra (if available) to display
    a 'running' status bubble in the UI, preventing the user from thinking
    the session has stalled during long subagent executions.
    """
    try:
        extra = ctx.extra or {}
        on_tool_call = extra.get("on_tool_call")
        if on_tool_call is not None:
            title = u"Running subagent: {} (@{})...".format(description, subagent_type)
            on_tool_call("task", {"description": description, "subagent_type": subagent_type, "mode": mode}, {
                "output": "",
                "title": title,
            })
    except Exception as e:
        logger.debug("Failed to send subagent start notification: %s", e)
        pass  # Notification failure should not break execution


class TaskTool(Tool):
    """Tool for launching specialized subagents in isolated sessions.

    Creates a new sub-session with parent-child linking to the caller's session,
    executes the requested subagent with its defined tool set, and returns
    the result wrapped in <task_result> tags with a task_id for resumption.
    """

    # Subagents allowed to be spawned via task tool.
    # Hidden agents (compaction, title, summary) are explicitly blocked.
    ALLOWED_SUBAGENTS = frozenset(["general", "explore", "consultant", "critic", "executor"])
    # Hidden agents derived from factory schemas (automatically kept in sync).
    _HIDDEN_AGENTS = frozenset()

    @staticmethod
    def _init_hidden_set():
        """Lazily populate _HIDDEN_AGENTS from factory schemas."""
        if TaskTool._HIDDEN_AGENTS:
            return
        try:
            from berserker.agent.factory import _BUILTIN_AGENT_SCHEMAS as _sch
            TaskTool._HIDDEN_AGENTS = frozenset(
                name for name, s in _sch.items() if s.hidden
            )
        except Exception:
            TaskTool._HIDDEN_AGENTS = frozenset(["compaction", "title", "summary"])

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=_get_subagent_timeout(), max_output_tokens=16384)

    def __init__(self):
        # type: () -> None
        super(TaskTool, self).__init__(
            id="task",
            description="Launch a new agent to handle complex, multistep tasks autonomously. "
            "In 'single' mode (default), provide description, prompt, and subagent_type. "
            "In 'parallel'/'sequential' mode, provide a tasks array (each with description, "
            "prompt, subagent_type). "
            "The tool supports: general (complex multi-step), explore (codebase search), "
            "consultant (pre-planning analysis), critic (plan review), executor (orchestration).",
            parameters={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "A short (3-5 words) description of the task.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The task for the agent to perform. Be specific and include "
                        "verification instructions so the agent can validate its own work.",
                    },
                    "subagent_type": {
                        "type": "string",
                        "enum": ["general", "explore", "consultant", "critic", "executor"],
                        "description": "The type of specialized agent to use for this task. "
                        "Use 'explore' for codebase exploration and pattern discovery. "
                        "Use 'general' for complex multi-step tasks requiring full tool access. "
                        "Use 'consultant' for pre-planning consultation to identify hidden intentions and ambiguities. "
                        "Use 'critic' for plan review and verification. "
                        "Use 'executor' for master orchestration of todo lists.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Resume an existing task by providing its session ID. "
                        "If omitted, a new sub-session is created.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["single", "parallel", "sequential"],
                        "description": "Execution mode. 'single' (default) runs one subagent. "
                        "'parallel' runs multiple subagents concurrently. "
                        "'sequential' runs subagents one after another.",
                    },
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "prompt": {"type": "string"},
                                "subagent_type": {"type": "string", "enum": ["general", "explore", "consultant", "critic", "executor"]},
                            },
                            "required": ["description", "prompt", "subagent_type"],
                        },
                        "description": "List of tasks for parallel/sequential mode. "
                        "Each task has description, prompt, and subagent_type.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute the task tool by spawning a subagent session.

        Supports three modes:
        - 'single' (default): Launch one subagent synchronously (backward compatible).
        - 'parallel': Launch multiple subagents concurrently.
        - 'sequential': Launch subagents one after another.

        Args:
            args: Dict with keys: description, prompt, subagent_type, task_id (optional),
                  mode (optional), tasks (optional for parallel/sequential).
            ctx: ToolContext with session_id, agent, abort signal, etc.

        Returns:
            ToolResult with task_id and wrapped <task_result> output.

        Raises:
            ToolExecutionError: If subagent is unknown, hidden, or execution fails.
        """
        # Ensure subagent sets are populated from schemas
        self._init_hidden_set()
        # Import here to avoid circular imports
        from berserker.agent.manager import agent_manager
        from berserker.session.manager import session_manager
        from berserker.tool.registry import registry as tool_registry

        description = args.get("description", "")  # type: str
        prompt = args.get("prompt", "")  # type: str
        subagent_type = args.get("subagent_type", "")  # type: str
        task_id = args.get("task_id")  # type: Optional[str]
        mode = args.get("mode", "single")  # type: str
        tasks = args.get("tasks")  # type: Optional[List[Dict[str, Any]]]

        # Check abort signal before starting
        if ctx.abort.is_set():
            raise ToolExecutionError("Task cancelled", tool_id=self.id)

        # Route to appropriate execution mode
        if mode == "parallel" and tasks:
            # subagent_type from each task definition, not top-level
            return self._execute_parallel(tasks, ctx, session_manager, agent_manager, tool_registry)
        elif mode == "sequential" and tasks:
            return self._execute_sequential(tasks, ctx, session_manager, agent_manager, tool_registry)
        else:
            # Single mode: subagent_type is required
            if not subagent_type:
                raise ToolExecutionError(
                    "subagent_type is required for single task mode. "
                    "Choose from: {}".format(", ".join(sorted(self.ALLOWED_SUBAGENTS))),
                    tool_id=self.id,
                )
            if subagent_type not in self.ALLOWED_SUBAGENTS:
                if subagent_type in self._HIDDEN_AGENTS:
                    raise ToolExecutionError(
                        "Cannot spawn hidden agent '{}'. Hidden agents (compaction, title, summary) "
                        "are for internal use only. Available subagents: {}".format(
                            subagent_type, ", ".join(sorted(self.ALLOWED_SUBAGENTS))
                        ),
                        tool_id=self.id,
                    )
                raise ToolExecutionError(
                    "Unknown agent type: '{}'. Available subagents: {}".format(
                        subagent_type, ", ".join(sorted(self.ALLOWED_SUBAGENTS))
                    ),
                    tool_id=self.id,
                )
            return self._execute_single(
                description, prompt, subagent_type, task_id, ctx,
                session_manager, agent_manager, tool_registry
            )

    def _execute_single(
        self,
        description,  # type: str
        prompt,  # type: str
        subagent_type,  # type: str
        task_id,  # type: Optional[str]
        ctx,  # type: ToolContext
        session_manager,  # type: Any
        agent_manager,  # type: Any
        tool_registry,  # type: Any
    ):
        # type: (...) -> ToolResult
        """Execute a single subagent (backward compatible path)."""
        # Resolve or create sub-session
        sub_session_id = None  # type: Optional[str]

        if task_id:
            # Attempt to resume existing session
            session_data = session_manager.load(task_id)
            if session_data is not None:
                sub_session_id = task_id
                logger.info("Resuming existing task session: %s", task_id)
            else:
                logger.warning("Task ID '%s' not found, creating new session", task_id)

        if sub_session_id is None:
            # Create new sub-session with parent-child linking
            title = "{} (@{} subagent)".format(description, subagent_type)
            sub_session_id = session_manager.create(
                title=title,
                parent_id=ctx.session_id,
            )
            logger.info(
                "Created sub-session %s (parent: %s, agent: %s)",
                sub_session_id,
                ctx.session_id,
                subagent_type,
            )

        # Notify UI about subagent launch via on_tool_call callback
        _notify_subagent_start(ctx, description, subagent_type)

        # Resume path: rebuild conversation history from the persisted session
        # BEFORE appending the new prompt below, so the rebuilt list does not
        # contain the new prompt twice.
        history_messages = []  # type: List[ChatMessage]
        if task_id and sub_session_id == task_id:
            from berserker.session.message_utils import chat_messages_from_session
            history_messages = chat_messages_from_session(
                session_manager.get_messages(task_id)
            )

        # Persist the delegation prompt as a user message in the sub-session
        # so the child session is fully traceable (the executor only persists
        # its own assistant/tool outputs, not the incoming turn).
        try:
            session_manager.append_message(sub_session_id, "user", prompt)
        except Exception as e:
            logger.warning("Failed to persist sub-session user message: %s", e)

        # Build messages: resumed sessions prepend the rebuilt history, new
        # sessions start with just the delegation prompt.
        sub_messages = history_messages + [
            ChatMessage(role="user", content=prompt),
        ]  # type: List[ChatMessage]

        # Execute the subagent (on_tool_call=None: subagent tool calls are not streamed to UI)
        try:
            result = agent_manager.execute(
                agent_name=subagent_type,
                messages=sub_messages,
                session_id=sub_session_id,
                tool_registry=tool_registry,
                on_tool_call=None,
                abort_event=ctx.abort,
            )
        except Exception as exc:
            raise ToolExecutionError(
                "Subagent execution failed: {}".format(str(exc)),
                tool_id=self.id,
                original_error=exc,
            )

        # Format result with task_id for resumption
        content = result.get("content", "")  # type: str
        output_lines = [
            "task_id: {} (for resuming to continue this task if needed)".format(sub_session_id),
            _format_execution_stats(result),
            "",
            "<task_result>",
            content if content else "(subagent completed with no output)",
            "</task_result>",
        ]

        return ToolResult(
            title="Task completed: {} (@{})".format(description, subagent_type),
            output="\n".join(output_lines),
            metadata={
                "task_id": sub_session_id,
                "subagent_type": subagent_type,
                "parent_session_id": ctx.session_id,
            },
        )

    def _execute_parallel(
        self,
        tasks,  # type: List[Dict[str, Any]]
        ctx,  # type: ToolContext
        session_manager,  # type: Any
        agent_manager,  # type: Any
        tool_registry,  # type: Any
    ):
        # type: (...) -> ToolResult
        """Execute multiple subagents in parallel using the orchestrator."""
        from berserker.agent.orchestrator import OrchestrationTask, get_orchestrator

        # Build orchestration tasks
        orch_tasks = []  # type: List[OrchestrationTask]
        for task_def in tasks:
            task_desc = task_def.get("description", "parallel-task")
            task_prompt = task_def.get("prompt", "")
            task_agent = task_def.get("subagent_type", "general")

            # Validate agent type
            if task_agent not in self.ALLOWED_SUBAGENTS:
                raise ToolExecutionError(
                    "Invalid subagent_type in parallel task: '{}'".format(task_agent),
                    tool_id=self.id,
                )

            # Create sub-session
            sub_session_id = session_manager.create(
                title="{} (@{} subagent)".format(task_desc, task_agent),
                parent_id=ctx.session_id,
            )
            # Persist the task prompt for traceability (see _execute_single)
            try:
                session_manager.append_message(sub_session_id, "user", task_prompt)
            except Exception as e:
                logger.warning("Failed to persist sub-session user message: %s", e)

            orch_tasks.append(
                OrchestrationTask(
                    task_id="parallel-{}".format(uuid.uuid4().hex[:8]),
                    agent_name=task_agent,
                    prompt=task_prompt,
                    session_id=sub_session_id,
                )
            )

        # Execute via orchestrator
        orchestrator = get_orchestrator(agent_manager, tool_registry)

        # Link parent abort to sub-agent tasks: start a watchdog that cancels
        # all tasks when the parent's abort signal is set. Poll with a timeout
        # and exit once the orchestrator returns, so the thread never leaks
        # when the parent abort is never signaled.
        tasks_done = threading.Event()

        def _abort_watchdog():
            while not tasks_done.is_set():
                if ctx.abort.wait(timeout=2):  # True means abort signaled
                    for t in orch_tasks:
                        t.cancel()
                    return

        watchdog = threading.Thread(target=_abort_watchdog, daemon=True)
        watchdog.start()

        results = orchestrator.execute_parallel(orch_tasks)
        tasks_done.set()

        # Format results
        output_lines = []  # type: List[str]
        output_lines.append("Parallel execution completed: {} tasks".format(len(results)))
        output_lines.append("")

        for result in results:
            task_id = result.get("task_id", "unknown")
            status = result.get("status", "unknown")
            task_result = result.get("result", {})
            content = ""
            if isinstance(task_result, dict):
                content = task_result.get("content", "")

            output_lines.append("<task_result task_id='{}' status='{}'>".format(task_id, status))
            if isinstance(task_result, dict) and not task_result.get("tool_calls_count", 0):
                output_lines.append(
                    "[注意] 该子任务未调用任何工具 — 结论未经过实际操作验证，请核实后再采信。")
            output_lines.append(content if content else "(no output)")
            output_lines.append("</task_result>")
            output_lines.append("")

        return ToolResult(
            title="Parallel tasks completed: {} tasks".format(len(results)),
            output="\n".join(output_lines),
            metadata={
                "mode": "parallel",
                "task_count": len(results),
                "results": results,
                "parent_session_id": ctx.session_id,
            },
        )

    def _execute_sequential(
        self,
        tasks,  # type: List[Dict[str, Any]]
        ctx,  # type: ToolContext
        session_manager,  # type: Any
        agent_manager,  # type: Any
        tool_registry,  # type: Any
    ):
        # type: (...) -> ToolResult
        """Execute multiple subagents sequentially using the orchestrator."""
        from berserker.agent.orchestrator import OrchestrationTask, get_orchestrator

        # Build orchestration tasks
        orch_tasks = []  # type: List[OrchestrationTask]
        task_descs = []  # type: List[str]
        for task_def in tasks:
            task_desc = task_def.get("description", "sequential-task")
            task_descs.append(task_desc)
            task_prompt = task_def.get("prompt", "")
            task_agent = task_def.get("subagent_type", "general")

            # Validate agent type
            if task_agent not in self.ALLOWED_SUBAGENTS:
                raise ToolExecutionError(
                    "Invalid subagent_type in sequential task: '{}'".format(task_agent),
                    tool_id=self.id,
                )

            # Create sub-session
            sub_session_id = session_manager.create(
                title="{} (@{} subagent)".format(task_desc, task_agent),
                parent_id=ctx.session_id,
            )
            # Persist the task prompt for traceability (see _execute_single)
            try:
                session_manager.append_message(sub_session_id, "user", task_prompt)
            except Exception as e:
                logger.warning("Failed to persist sub-session user message: %s", e)

            orch_tasks.append(
                OrchestrationTask(
                    task_id="sequential-{}".format(uuid.uuid4().hex[:8]),
                    agent_name=task_agent,
                    prompt=task_prompt,
                    session_id=sub_session_id,
                )
            )

        # Notify UI about sequential subagent launch
        _notify_subagent_start(ctx, ", ".join(task_descs), "sequential", mode="sequential")

        # Execute via orchestrator
        orchestrator = get_orchestrator(agent_manager, tool_registry)

        # Link parent abort to sub-agent tasks. Poll with a timeout and exit
        # once the orchestrator returns, so the thread never leaks when the
        # parent abort is never signaled.
        tasks_done = threading.Event()

        def _abort_watchdog():
            while not tasks_done.is_set():
                if ctx.abort.wait(timeout=2):  # True means abort signaled
                    for t in orch_tasks:
                        t.cancel()
                    return

        watchdog = threading.Thread(target=_abort_watchdog, daemon=True)
        watchdog.start()

        results = orchestrator.execute_sequential(orch_tasks)
        tasks_done.set()

        # Format results
        output_lines = []  # type: List[str]
        output_lines.append("Sequential execution completed: {} tasks".format(len(results)))
        output_lines.append("")

        for result in results:
            task_id = result.get("task_id", "unknown")
            status = result.get("status", "unknown")
            task_result = result.get("result", {})
            content = ""
            if isinstance(task_result, dict):
                content = task_result.get("content", "")

            output_lines.append("<task_result task_id='{}' status='{}'>".format(task_id, status))
            if isinstance(task_result, dict) and not task_result.get("tool_calls_count", 0):
                output_lines.append(
                    "[注意] 该子任务未调用任何工具 — 结论未经过实际操作验证，请核实后再采信。")
            output_lines.append(content if content else "(no output)")
            output_lines.append("</task_result>")
            output_lines.append("")

        return ToolResult(
            title="Sequential tasks completed: {} tasks".format(len(results)),
            output="\n".join(output_lines),
            metadata={
                "mode": "sequential",
                "task_count": len(results),
                "results": results,
                "parent_session_id": ctx.session_id,
            },
        )


def register_task_tool(registry):
    # type: (Any) -> None
    """Register the TaskTool with the given tool registry.

    Args:
        registry: ToolRegistry instance to register with.
    """
    registry.register(TaskTool())
