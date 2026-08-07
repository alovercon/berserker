"""
Tool Registry for berserker.

Manages tool registration, lookup, validation, execution with timeout,
output truncation, and error handling.

Features:
- Thread-safe registration with threading.Lock
- JSON Schema parameter validation via jsonschema
- Timeout control via concurrent.futures.ThreadPoolExecutor
- Output truncation when exceeding max_output_tokens
- Error handling: execute() never raises, returns error dict instead

Python 3.8.10 compatible: uses type comments, Optional, no match/case.
"""

from __future__ import annotations

import atexit
import threading
import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import List, Dict, Any, Optional

import jsonschema

from berserker.tool.base import (
    Tool,
    ToolConfig,
    ToolContext,
    ToolResult,
    ToolError,
    ToolTimeoutError,
    ToolValidationError,
    ToolExecutionError,
    format_validation_error,
)
from berserker.tool.truncate import (
    truncate_result,
    DEFAULT_MAX_TOKENS,
    DEFAULT_COMPACTION_BUFFER,
    calculate_dynamic_max_tokens,
)

logger = logging.getLogger(__name__)

# Shared thread pool for tool execution.
# Avoids creating/destroying a ThreadPoolExecutor per tool call.
# max_workers=4 allows concurrent tool calls without resource exhaustion.
_shared_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool-")

# Dedicated pool for the "task" tool. A task-tool call runs a whole
# subagent whose own tool calls are submitted back to this registry;
# running the task tool itself on the shared pool can starve it (all
# workers blocked inside task executions while their nested tool calls
# queue behind them — audit H6).
_task_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="task-tool-")


@atexit.register
def _shutdown_executor():
    _shared_executor.shutdown(wait=False)
    _task_executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry(object):
    """Registry for managing tool instances.

    Provides thread-safe registration, lookup, and execution of tools
    with parameter validation, timeout control, and output truncation.

    Usage:
        registry = ToolRegistry()
        registry.register(MyTool())
        result = registry.execute("my-tool", {"arg": "value"}, ctx)
    """

    def __init__(self, default_timeout=None, max_output_tokens=None):
        # type: (Optional[int], Optional[int]) -> None
        """Initialize the tool registry.

        Args:
            default_timeout: Default execution timeout in seconds (default: 30).
            max_output_tokens: Maximum tokens in tool output (default: 4096).
        """
        self._tools = {}  # type: Dict[str, Tool]
        self._lock = threading.Lock()
        self.default_timeout = default_timeout if default_timeout is not None else 30
        self.max_output_tokens = (
            max_output_tokens if max_output_tokens is not None else DEFAULT_MAX_TOKENS
        )

    def register(self, tool):
        # type: (Tool) -> None
        """Register a tool instance.

        Args:
            tool: A Tool instance to register.

        Raises:
            ToolError: If tool is not a Tool instance or has no ID.
        """
        if not isinstance(tool, Tool):
            raise ToolError("Tool must be an instance of Tool, got {}".format(type(tool).__name__))
        if not tool.id:
            raise ToolError("Tool must have a non-empty id")

        with self._lock:
            self._tools[tool.id] = tool

    def unregister(self, tool_id):
        # type: (str) -> None
        """Remove a tool from the registry.

        Args:
            tool_id: The ID of the tool to remove.

        Raises:
            ToolError: If the tool is not found.
        """
        with self._lock:
            if tool_id not in self._tools:
                raise ToolError("Tool '{}' not found".format(tool_id))
            del self._tools[tool_id]

    def get(self, tool_id):
        # type: (str) -> Tool
        """Get a tool by ID.

        Args:
            tool_id: The tool identifier.

        Returns:
            The registered Tool instance.

        Raises:
            ToolError: If the tool is not found.
        """
        with self._lock:
            if tool_id not in self._tools:
                raise ToolError("Tool '{}' not found".format(tool_id))
            return self._tools[tool_id]

    def list(self):
        # type: () -> List[str]
        """List all registered tool IDs.

        Returns:
            List of tool ID strings.
        """
        with self._lock:
            return list(self._tools.keys())

    def list_all(self):
        # type: () -> List[Tool]
        """List all registered Tool objects.

        Returns:
            List of Tool instances.
        """
        with self._lock:
            return list(self._tools.values())

    def execute(self, tool_id, args, ctx, timeout=None):
        # type: (str, Dict[str, Any], ToolContext, Optional[int]) -> Dict[str, Any]
        """Execute a tool with validation, timeout, and truncation.

        This method NEVER raises an exception. All errors are returned
        as error dictionaries.

        Timeout priority:
        1. Explicit timeout parameter (highest)
        2. Tool's config.timeout
        3. Tool's legacy timeout attribute
        4. Registry default_timeout (lowest)

        Args:
            tool_id: The ID of the tool to execute.
            args: Arguments dict to pass to the tool.
            ctx: ToolContext with session, agent, abort signal, etc.
            timeout: Execution timeout in seconds (overrides tool config).

        Returns:
            Dict with tool result or error information.
            On success: {"title": ..., "output": ..., "metadata": ..., "attachments": ...}
            On error: {"error": str, "error_type": str}
        """
        # Step 0: Resolve timeout with priority chain
        if timeout is None:
            try:
                tool_for_timeout = self.get(tool_id)
                # Use tool's config property (supports both new config and legacy timeout)
                config_timeout = tool_for_timeout.config.timeout
                if config_timeout is not None:
                    timeout = config_timeout
            except ToolError:
                pass  # Tool not found, will be caught in Step 1
            if timeout is None:
                timeout = self.default_timeout

        # Step 1: Lookup tool
        try:
            tool = self.get(tool_id)
        except ToolError as e:
            logger.error("Tool '%s' not found in registry", tool_id)
            return {"error": str(e), "error_type": type(e).__name__}

        # Step 2: Validate parameters
        try:
            self._validate(tool, args)
        except ToolValidationError as e:
            logger.warning("Tool '%s' validation failed: %s", tool_id, str(e))
            return {"error": str(e), "error_type": type(e).__name__}

        # Step 3: Execute with timeout
        try:
            result = self._execute_with_timeout(tool, args, ctx, timeout)
        except ToolTimeoutError as e:
            logger.error("Tool '%s' timed out after %d seconds", tool_id, timeout)
            return {"error": str(e), "error_type": type(e).__name__}
        except ToolError as e:
            logger.error("Tool '%s' execution error: %s", tool_id, str(e))
            return {"error": str(e), "error_type": type(e).__name__}
        except Exception as e:
            logger.error("Tool '%s' unexpected error: %s", tool_id, str(e))
            return {"error": str(e), "error_type": type(e).__name__}

        # Step 4: Convert ToolResult to dict and truncate
        result_dict = self._result_to_dict(result)
        # Calculate dynamic max tokens — prefer tool's config, fallback to registry default
        tool_max = getattr(tool, 'config', None)
        tool_max_tokens = tool_max.max_output_tokens if tool_max is not None else None
        base_max = tool_max_tokens if tool_max_tokens is not None else self.max_output_tokens
        context_info = ctx.extra.get("context_info") if ctx.extra else None
        dynamic_max = calculate_dynamic_max_tokens(
            context_info, base_max, DEFAULT_COMPACTION_BUFFER
        )
        logger.debug(
            "[TRUNCATE_RESULT] tool=%s, tool_max=%s, base_max=%s, dynamic_max=%s, output_len=%d",
            tool_id, tool_max_tokens, base_max, dynamic_max, len(result_dict.get("output", "") or ""),
        )
        result_dict = truncate_result(result_dict, dynamic_max)

        return result_dict

    def _validate(self, tool, args):
        # type: (Tool, Dict[str, Any]) -> None
        """Validate tool arguments against the tool's JSON Schema.

        Args:
            tool: The Tool instance.
            args: Arguments to validate.

        Raises:
            ToolValidationError: If validation fails.
        """
        schema = tool.parameters
        if schema is None:
            return

        try:
            jsonschema.validate(instance=args, schema=schema)
        except jsonschema.ValidationError as e:
            formatted = tool.format_validation_error(e)
            raise ToolValidationError(formatted)

    def _execute_with_timeout(self, tool, args, ctx, timeout):
        # type: (Tool, Dict[str, Any], ToolContext, int) -> ToolResult
        """Execute a tool with a timeout using ThreadPoolExecutor.

        Args:
            tool: The Tool instance to execute.
            args: Validated arguments dict.
            ctx: ToolContext for the execution.
            timeout: Timeout in seconds.

        Returns:
            ToolResult from the tool execution.

        Raises:
            ToolTimeoutError: If execution exceeds the timeout.
            ToolExecutionError: If the tool raises an exception.
        """
        # Check abort signal before starting
        if ctx.abort.is_set():
            raise ToolExecutionError(
                "Tool execution aborted before starting",
                tool_id=tool.id,
            )

        result = None  # type: Optional[ToolResult]
        # The task tool nests further tool executions through this registry;
        # route it to the dedicated pool so those nested submissions can
        # never starve the shared pool (audit H6).
        pool = _task_executor if tool.id == "task" else _shared_executor
        future = pool.submit(tool.execute, args, ctx)
        try:
            result = future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            # Kill orphaned subprocess if any
            if hasattr(ctx, 'active_process') and ctx.active_process is not None:
                try:
                    ctx.active_process.kill()
                    ctx.active_process.communicate(timeout=5)
                except Exception:
                    pass
                ctx.active_process = None
            raise ToolTimeoutError("Tool execution timed out after {} seconds".format(timeout))
        finally:
            # Safety cleanup: kill any remaining subprocess
            if hasattr(ctx, 'active_process') and ctx.active_process is not None:
                try:
                    ctx.active_process.kill()
                    ctx.active_process.communicate(timeout=5)
                except Exception:
                    pass
                ctx.active_process = None

        # Check abort signal after completion
        if ctx.abort.is_set():
            raise ToolExecutionError(
                "Tool execution aborted after completion",
                tool_id=tool.id,
            )

        if result is None:
            raise ToolExecutionError(
                "Tool '{}' returned None result".format(tool.id),
                tool_id=tool.id,
            )

        if not isinstance(result, ToolResult):
            raise ToolExecutionError(
                "Tool '{}' returned invalid result type: {}".format(tool.id, type(result).__name__),
                tool_id=tool.id,
            )

        return result

    def _result_to_dict(self, result):
        # type: (ToolResult) -> Dict[str, Any]
        """Convert a ToolResult to a dictionary.

        Args:
            result: The ToolResult to convert.

        Returns:
            Dict representation of the result.
        """
        d = {
            "title": result.title,
            "output": result.output,
            "metadata": result.metadata,
        }
        if result.attachments is not None:
            d["attachments"] = result.attachments
        if result.error is not None:
            d["error"] = result.error
        return d


# ---------------------------------------------------------------------------
# Singleton Instance
# ---------------------------------------------------------------------------

registry = ToolRegistry()
