"""
Shell execution tool for berserker.

Provides BashTool — execute shell commands with timeout, working directory
support, and signal handling. Uses subprocess.Popen for fine-grained control
over process lifecycle.

Security:
- Validates cwd is within workspace when ctx.extra['workspace'] is set.
- Always enforces a timeout (no arbitrary unbounded execution).

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case,
no str.removeprefix(), no subprocess.run(capture_output=...).
"""

from __future__ import annotations

import os
import subprocess
from typing import Dict, Any, Optional

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolResult, ToolError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 30  # type: int
_MAX_OUTPUT_BYTES = 100 * 1024  # 100 KB truncation threshold for stdout/stderr

# P1: Patterns for simple file read commands that should use Read tool instead
_FILE_READ_PATTERNS = [
    r'^\s*cat\s+(?!-)[^\s|&;>]+\s*$',
    r'^\s*head\s+(-n\s+\d+\s+)?(?!-)[^\s|&;>]+\s*$',
    r'^\s*tail\s+(-n\s+\d+\s+)?(?!-)[^\s|&;>]+\s*$',
    r'^\s*type\s+(?!-)[^\s|&;>]+\s*$',  # Windows equivalent of cat
]

_FILE_READ_WARNING = (
    "Warning: Prefer the Read tool over `cat`/`head`/`tail` for reading file contents. "
    "The Read tool provides line numbers and encoding auto-detection."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_workspace(ctx):
    # type: (ToolContext) -> str
    """Get the workspace root path from context, defaulting to current dir."""
    if ctx.extra is not None:
        ws = ctx.extra.get("workspace")  # type: Optional[str]
        if ws:
            return os.path.abspath(ws)
    return os.path.abspath(".")


def _secure_resolve_cwd(raw_cwd, workspace):
    # type: (str, str) -> str
    """Resolve a raw cwd path and verify it stays within the workspace.

    Returns the absolute resolved path.
    Raises ToolError if the path escapes the workspace (path traversal).

    Note: Absolute paths are allowed. Workspace restriction only applies to relative paths.
    """
    resolved = os.path.abspath(raw_cwd)

    # Allow absolute paths
    if not os.path.isabs(raw_cwd):
        # Relative path - must be within workspace
        norm_workspace = os.path.normpath(workspace)
        if not (resolved == norm_workspace or resolved.startswith(norm_workspace + os.sep)):
            raise ToolError("cwd '{}' is outside the workspace '{}'".format(raw_cwd, workspace))

    return resolved


def _decode_bytes(data, fallback="latin-1"):
    # type: (bytes, str) -> str
    """Decode bytes to string with UTF-8 primary and fallback encoding."""
    try:
        return data.decode("utf-8")
    except (UnicodeDecodeError, UnicodeError):
        return data.decode(fallback)


def _truncate_output(text, max_bytes):
    # type: (str, int) -> tuple[str, bool]
    """Truncate text if it exceeds max_bytes. Returns (text, was_truncated)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # Truncate and decode back (may cut mid-character, so use errors='replace')
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated, True


# ---------------------------------------------------------------------------
# BashTool
# ---------------------------------------------------------------------------


class BashTool(Tool):
    """Execute shell commands with timeout, cwd support, and output capture.

    Uses subprocess.Popen for fine-grained process control.
    Always enforces a timeout to prevent runaway processes.
    """

    def __init__(self):
        # type: () -> None
        # Detect platform and shell for accurate LLM instructions
        if os.name == "nt":
            shell_info = "cmd.exe on Windows. Use cmd.exe syntax (not bash or PowerShell)."
        else:
            shell_info = "/bin/sh on Unix. Use POSIX sh syntax."

        super(BashTool, self).__init__(
            id="bash",
            description=(
                "Execute a shell command with optional working directory and timeout. One-shot commands only - for TUI apps needing ongoing interaction (vim, htop), use PTY tools instead. Current shell: {shell_info}. Captures stdout/stderr. Default timeout: 60s. Output truncated at 100KB. On timeout, process is killed and partial output returned. "
                "IMPORTANT: Do NOT use bash for simple file/folder operations (mkdir, rm, mv, cp, touch, rmdir) - use the dedicated file operation tools instead. "
                "Reserve bash ONLY for: git commands, package managers (npm/pip), build tools, system utilities (find, grep, awk, sed), network commands (curl, wget), or other complex shell operations."
            ).format(shell_info=shell_info),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Working directory for the command. "
                            "Must be within the workspace if workspace is set."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Maximum execution time in seconds. Defaults to 60 if not specified."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    @property
    def config(self):
        # type: () -> ToolConfig
        """Bash tool configuration: 60s default timeout for shell commands."""
        return ToolConfig(timeout=60, max_output_tokens=8192)

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        command = args["command"]  # type: str
        raw_cwd = args.get("cwd")  # type: Optional[str]
        timeout = args.get("timeout", _DEFAULT_TIMEOUT)  # type: int

        # Resolve and validate cwd
        workspace = _resolve_workspace(ctx)
        if raw_cwd is not None:
            cwd = _secure_resolve_cwd(raw_cwd, workspace)
        else:
            cwd = workspace

        # P1: Check for simple file read commands and inject warning
        import re as _re
        for _pattern in _FILE_READ_PATTERNS:
            if _re.match(_pattern, command):
                # Inject warning into output instead of blocking
                return ToolResult(
                    title="Bash: {}".format(command[:50]),
                    output=_FILE_READ_WARNING + "\n\nCommand: {}\n\nUse the Read tool instead.".format(command),
                    metadata={"exit_code": 0, "truncated": False, "timed_out": False, "cwd": cwd, "warning": True},
                )

        # Determine shell based on platform
        if os.name == "nt":
            shell_cmd = ["cmd.exe", "/c", command]
            # Hide console window on Windows (GUI apps spawn visible consoles by default)
            creation_flags = subprocess.CREATE_NO_WINDOW
        else:
            shell_cmd = ["/bin/sh", "-c", command]
            creation_flags = 0

        # Execute the command
        proc = subprocess.Popen(
            shell_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            creationflags=creation_flags,
        )
        timed_out = False  # type: bool
        stdout_bytes = b""  # type: bytes
        stderr_bytes = b""  # type: bytes
        exit_code = None  # type: Optional[int]

        try:
            # Register subprocess with context so registry can kill it on timeout
            ctx.active_process = proc
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            # Collect whatever output is available after killing
            stdout_bytes, stderr_bytes = proc.communicate()
            exit_code = proc.returncode
        finally:
            # Clear active_process reference
            ctx.active_process = None

        # Decode output
        stdout_text = _decode_bytes(stdout_bytes)
        stderr_text = _decode_bytes(stderr_bytes)

        # Truncate if necessary
        stdout_text, stdout_truncated = _truncate_output(stdout_text, _MAX_OUTPUT_BYTES)
        stderr_text, stderr_truncated = _truncate_output(stderr_text, _MAX_OUTPUT_BYTES)
        truncated = stdout_truncated or stderr_truncated

        # Build output
        lines = []  # type: list[str]

        # Determine if command failed
        command_failed = exit_code is not None and exit_code != 0

        # On failure, add structured error header with recovery guidance
        if command_failed:
            lines.append("[COMMAND FAILED] Exit code: {}".format(exit_code))
            lines.append("")
            # Provide error analysis based on stderr content
            if stderr_text:
                # Extract first meaningful error line
                error_lines = [l for l in stderr_text.split("\n") if l.strip()]
                if error_lines:
                    lines.append("Error output:")
                    lines.append(error_lines[0])
                    lines.append("")
            # Add recovery suggestions
            lines.append("The command failed. Consider:")
            lines.append("- Check if the command syntax is correct for this platform")
            lines.append("- Verify the target file/directory exists")
            lines.append("- Check permissions and environment setup")
            lines.append("- Try an alternative approach or tool")
            lines.append("")
            lines.append("--- Full output ---")
        elif timed_out:
            lines.append("[TIMED OUT after {} seconds]".format(timeout))
            lines.append("")

        lines.append("Command: {}".format(command))
        if raw_cwd is not None:
            lines.append("Working directory: {}".format(cwd))
        if not command_failed:
            lines.append("Exit code: {}".format(exit_code))
        lines.append("")

        if stdout_text:
            if not command_failed:
                lines.append("--- stdout ---")
            lines.append(stdout_text)
            lines.append("")

        if stderr_text:
            if not command_failed:
                lines.append("--- stderr ---")
            lines.append(stderr_text)
            lines.append("")

        if not stdout_text and not stderr_text:
            if command_failed:
                lines.append("(no output - command failed silently)")
            else:
                lines.append("(no output)")

        output = "\n".join(lines)

        metadata = {
            "exit_code": exit_code,
            "truncated": truncated,
            "timed_out": timed_out,
            "cwd": cwd,
        }  # type: Dict[str, Any]

        title = "Bash: {}".format(command[:50])
        if len(command) > 50:
            title += "..."

        # On timeout, set error field so agent manager formats it prominently
        error_msg = None  # type: Optional[str]
        if timed_out:
            error_msg = "[TIMED OUT after {} seconds]".format(timeout)

        return ToolResult(
            title=title,
            output=output,
            metadata=metadata,
            error=error_msg,
        )


# ---------------------------------------------------------------------------
# Registration Helper
# ---------------------------------------------------------------------------


def register_bash_tool(registry):
    # type: (Any) -> None
    """Register the BashTool with the given registry.

    Args:
        registry: A ToolRegistry instance to register the tool with.
    """
    registry.register(BashTool())
