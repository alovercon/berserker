"""
Tool base class, error hierarchy, and data structures for berserker.

Matches the reference TypeScript tool interface with:
- Error hierarchy (ToolError and subclasses)
- ToolContext dataclass (session_id, message_id, agent, abort, etc.)
- ToolResult dataclass (title, output, metadata, attachments)
- ToolConfig dataclass (timeout, max_output_tokens, retry_on_timeout)
- Tool base class (dataclass) with id, description, parameters, execute()
- format_validation_error() for human-readable jsonschema error messages

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable

# ---------------------------------------------------------------------------
# Error Hierarchy
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Base exception for all tool-related errors."""

    pass


class ToolTimeoutError(ToolError):
    """Raised when tool execution exceeds the allowed time limit."""

    pass


class ToolValidationError(ToolError):
    """Raised when tool arguments fail JSON Schema validation."""

    pass


class ToolExecutionError(ToolError):
    """Raised when tool execution fails due to runtime errors.

    Attributes:
        tool_id: The ID of the tool that failed.
        original_error: The original exception that caused the failure.
    """

    def __init__(self, message, tool_id=None, original_error=None):
        # type: (str, Optional[str], Optional[Exception]) -> None
        super(ToolExecutionError, self).__init__(message)
        self.tool_id = tool_id
        self.original_error = original_error


# ---------------------------------------------------------------------------
# Tool Configuration
# ---------------------------------------------------------------------------


@dataclass
class ToolConfig(object):
    """Configuration for tool execution behavior.

    Attributes:
        timeout: Execution timeout in seconds (default: None = use registry default).
        max_output_tokens: Maximum tokens in tool output (default: 4096).
        retry_on_timeout: Whether to retry on timeout (default: False).
    """

    timeout: Optional[int] = None
    max_output_tokens: int = 4096
    retry_on_timeout: bool = False

    _KNOWN_KEYS = frozenset({"timeout", "max_output_tokens", "retry_on_timeout"})

    @classmethod
    def from_dict(cls, data):
        # type: (Dict[str, Any]) -> ToolConfig
        """Create a ToolConfig from a dict, using defaults for missing keys.

        Only accepts known keys. Unknown keys are silently ignored.

        Args:
            data: Dict with config values.

        Returns:
            A new ToolConfig instance.
        """
        if not isinstance(data, dict):
            return cls()

        filtered = {k: v for k, v in data.items() if k in cls._KNOWN_KEYS}
        return cls(**filtered)

    def to_dict(self):
        # type: () -> Dict[str, Any]
        """Return the config as a dict.

        Returns:
            Dict representation of this config.
        """
        return {
            "timeout": self.timeout,
            "max_output_tokens": self.max_output_tokens,
            "retry_on_timeout": self.retry_on_timeout,
        }


# ---------------------------------------------------------------------------
# Tool Context
# ---------------------------------------------------------------------------


@dataclass
class ToolContext(object):
    """Context passed to tool execute() methods.

    Mirrors the TypeScript Tool.Context interface.

    Attributes:
        session_id: The current session identifier.
        message_id: The message that triggered this tool call.
        agent: The name of the agent executing the tool.
        abort: Threading Event used to signal cancellation.
        call_id: Optional unique ID for this specific tool call.
        extra: Optional dict of additional context data (includes context_info for dynamic truncation).
        messages: List of message dicts forming the conversation history.
        active_process: Optional subprocess.Popen tracked for timeout cleanup.
    """

    session_id: str
    message_id: str
    agent: str
    abort: threading.Event
    call_id: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    active_process: Optional["subprocess.Popen"] = None


# ---------------------------------------------------------------------------
# Tool Result
# ---------------------------------------------------------------------------


@dataclass
class ToolResult(object):
    """Result returned from tool execute() methods.

    Mirrors the TypeScript Tool.ExecuteResult interface.

    Attributes:
        title: Human-readable title for the result.
        output: The text output from the tool.
        metadata: Dict of metadata (e.g. truncated, outputPath).
        attachments: Optional list of file attachment dicts.
        error: Optional error string. When set, agent manager formats it prominently as "Error: {error}".
    """

    title: str
    output: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    attachments: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Tool Base Class
# ---------------------------------------------------------------------------


class Tool(object):
    """Base class for all tools.

    Concrete tools subclass this and override execute().

    Attributes:
        id: Unique identifier for the tool (e.g. "read", "write").
        description: Human-readable description of what the tool does.
        parameters: JSON Schema dict defining the tool's input parameters.
        timeout: Optional custom timeout in seconds (legacy, use config instead).
    """

    def __init__(self, id, description, parameters=None, timeout=None):
        # type: (str, str, Optional[Dict[str, Any]], Optional[int]) -> None
        self.id = id
        self.description = description
        self.timeout = timeout  # Legacy: use config.timeout instead
        self.parameters = (
            parameters
            if parameters is not None
            else {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
        )

    @property
    def config(self):
        # type: () -> ToolConfig
        """Tool execution configuration.

        Subclasses can override this property to provide custom timeout,
        output limits, or retry behavior.

        Returns:
            ToolConfig instance for this tool.
        """
        # If legacy timeout is set, use it; otherwise return default config
        # (timeout=None signals registry to use its default_timeout)
        if self.timeout is not None:
            return ToolConfig(timeout=self.timeout)
        return ToolConfig()

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute the tool with the given arguments and context.

        Args:
            args: Dict of arguments matching the parameters JSON Schema.
            ctx: ToolContext with session, agent, abort signal, etc.

        Returns:
            ToolResult with title, output, metadata, and optional attachments.

        Raises:
            ToolError: If execution fails.
        """
        raise NotImplementedError("Subclasses must implement execute()")

    def format_validation_error(self, error):
        # type: (Exception) -> str
        """Format a jsonschema.ValidationError into a human-readable string.

        Override this method in subclasses to provide tool-specific
        validation error messages.

        Args:
            error: The jsonschema.ValidationError instance.

        Returns:
            Human-readable error message string.
        """
        return format_validation_error(error)

    def __repr__(self):
        # type: () -> str
        return "<Tool id={} description={!r}>".format(self.id, self.description)


# ---------------------------------------------------------------------------
# Validation Error Formatter
# ---------------------------------------------------------------------------


def format_validation_error(error):
    # type: (Exception) -> str
    """Format a jsonschema.ValidationError into a human-readable string.

    Produces a clear message showing which field failed and why.

    Args:
        error: The jsonschema.ValidationError instance.

    Returns:
        Human-readable error message string.
    """
    # Build a path string like "arguments.file_path" or just "arguments"
    # jsonschema.ValidationError has absolute_path and message attributes
    path_parts = list(getattr(error, "absolute_path", []))
    if path_parts:
        field_name = ".".join(str(p) for p in path_parts)
    else:
        field_name = "input"

    message = str(getattr(error, "message", str(error)))
    return "Validation error for '{}': {}".format(field_name, message)
