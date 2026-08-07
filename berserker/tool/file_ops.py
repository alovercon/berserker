"""
File operation tools for berserker: mkdir, rmdir, mv, cp, rm, touch.

Provides 6 Tool subclasses for common file/folder operations:
- MkdirTool: Create directories (with -p equivalent for nested paths)
- RmdirTool: Remove empty directories
- MvTool: Move/rename files and directories
- CpTool: Copy files and directories
- RmTool: Remove files
- TouchTool: Create empty files or update timestamps

All tools enforce workspace-restricted path security.
Designed to reduce bash usage for common file operations.

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolError, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 30  # type: int

# -- Dangerous path protection -- #

# Path patterns that are ALWAYS blocked (system-critical)
_BLOCKED_PATH_PATTERNS = [
    # Windows system directories (case-insensitive)
    re.compile(r'[\\/]Windows[\\/]?(?:$|System32|SysWOW64|System)',
               re.IGNORECASE),
    re.compile(r'[\\/]Program Files(?: \(x86\))?[\\/]?(?:$|Common Files)',
               re.IGNORECASE),
    re.compile(r'[\\/]ProgramData[\\/]?', re.IGNORECASE),
    re.compile(r'[\\/]Recovery[\\/]?', re.IGNORECASE),
    re.compile(r'^[A-Za-z]:\\$'),  # Drive roots (C:\, D:\, etc.)
    # Unix system directories
    re.compile(r'^/(?:etc|usr|var|bin|sbin|boot|lib(?:64)?|dev|proc|sys)(?:$|/)'),
    # Hidden VCS/metadata directories (block recursive delete by default)
    re.compile(r'[\\/]\.git$'),
    re.compile(r'[\\/]\.svn$'),
    re.compile(r'[\\/]\.hg$'),
]

# Path patterns that require a confirmation override flag
_DANGEROUS_DELETE_PATTERNS = [
    re.compile(r'[\\/]node_modules(?:$|[/\\])'),
    re.compile(r'[\\/]__pycache__(?:$|[/\\])'),
    re.compile(r'[\\/]\.(?:venv|tox|mypy_cache|pytest_cache)(?:$|[/\\])'),
    re.compile(r'[\\/]venv(?:$|[/\\])'),
    re.compile(r'[\\/]dist(?:$|[/\\])'),
    re.compile(r'[\\/]build(?:$|[/\\])'),
    re.compile(r'[\\/]\.next(?:$|[/\\])'),
    re.compile(r'[\\/]\.nuxt(?:$|[/\\])'),
]

# Size threshold for rmdir recursive warning (bytes) — dirs larger than this get a warning
_RMDIR_RECURSIVE_SIZE_WARN = 50 * 1024 * 1024  # 50 MB

# Edit tool: block modifications to these file patterns
_BLOCKED_EDIT_PATTERNS = [
    re.compile(r'\.(?:exe|dll|pyd|so|dylib|bin)$', re.IGNORECASE),  # Binaries
    re.compile(r'\.(?:pyc|pyo)$'),  # Python bytecode
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_dangerous_path(resolved, patterns, tool_id, ctx):
    # type: (str, List[Pattern], str, ToolContext) -> Optional[str]
    """Check if a resolved path matches any dangerous pattern.

    Returns an error message string if blocked, or None if safe.
    Checks permission first: if ctx already has dangerous_override=true, skip check.
    """
    # Allow override via context permission (for power users / batch operations)
    if ctx.extra and ctx.extra.get("dangerous_override"):
        return None

    for pattern in patterns:
        if pattern.search(resolved):
            return (
                "SECURITY: Target path '{}' is protected. "
                "This path matched blocked pattern '{}'. "
                "Use bash tool with explicit confirmation if you absolutely need to proceed."
            ).format(resolved, pattern.pattern)

    return None


def _resolve_workspace(ctx):
    # type: (ToolContext) -> str
    """Get the workspace root path from context, defaulting to current dir."""
    if ctx.extra is not None:
        ws = ctx.extra.get("workspace")  # type: Optional[str]
        if ws:
            return os.path.abspath(ws)
    return os.path.abspath(".")


def _secure_resolve(raw_path, workspace):
    # type: (str, str) -> str
    """Resolve a raw path and verify it stays within the workspace.

    Returns the absolute resolved path.
    Raises ToolError if the path escapes the workspace (path traversal).
    """
    if not os.path.isabs(raw_path):
        resolved = os.path.abspath(os.path.join(workspace, raw_path))
        norm_workspace = os.path.normpath(workspace)
        if not (resolved == norm_workspace or resolved.startswith(norm_workspace + os.sep)):
            logger.warning("Relative path '%s' escapes workspace '%s'", raw_path, workspace)
            raise ToolError("Path '{}' is outside the workspace '{}'".format(raw_path, workspace))
    else:
        resolved = os.path.abspath(raw_path)

    return resolved


def _format_path(path):
    # type: (str) -> str
    """Format a path for display."""
    return path


def _check_delete_path(resolved, ctx):
    # type: (str, ToolContext) -> Optional[str]
    """Check if a path is blocked or dangerously large for deletion.

    Checks blocked patterns first (always rejected), then dangerous patterns
    (rejected with stronger warning). Returns error message or None.
    """
    # Allow override via context permission
    if ctx.extra and ctx.extra.get("dangerous_override"):
        return None

    # Check blocked patterns (system-critical paths)
    blocked_msg = _check_dangerous_path(resolved, _BLOCKED_PATH_PATTERNS, "delete", ctx)
    if blocked_msg:
        return blocked_msg

    # Check dangerous patterns (large generated directories)
    dangerous_msg = _check_dangerous_path(resolved, _DANGEROUS_DELETE_PATTERNS, "delete", ctx)
    if dangerous_msg:
        return dangerous_msg

    return None


def _estimate_dir_size(path):
    # type: (str) -> int
    """Quickly estimate total size of a directory tree in bytes.

    Walks the first 1000 entries for a rough estimate (avoids hanging on
    extremely large directories).
    """
    total = 0  # type: int
    count = 0  # type: int
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                try:
                    fp = os.path.join(dirpath, f)
                    total += os.path.getsize(fp)
                except (OSError, IOError):
                    pass
                count += 1
                if count >= 1000:
                    return total
    except (OSError, IOError):
        pass
    return total


# ---------------------------------------------------------------------------
# MkdirTool
# ---------------------------------------------------------------------------


class MkdirTool(Tool):
    """Create directories, including nested paths (like mkdir -p)."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(MkdirTool, self).__init__(
            id="mkdir",
            description=(
                "Create a directory, including any necessary parent directories (like mkdir -p). "
                "Does nothing if the directory already exists. "
                "PREFER THIS TOOL over 'bash mkdir' - always use this for creating directories. "
                "Only use bash for complex shell operations, not simple file/folder creation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the directory to create.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        dir_path = args["path"]  # type: str

        workspace = _resolve_workspace(ctx)
        resolved = _secure_resolve(dir_path, workspace)

        if os.path.isdir(resolved):
            output = "Directory already exists: {}".format(resolved)
            return ToolResult(
                title="Mkdir: {}".format(os.path.basename(resolved)),
                output=output,
                metadata={"path": resolved, "already_exists": True},
            )

        if os.path.exists(resolved):
            raise ToolError("Path exists but is not a directory: {}".format(resolved))

        os.makedirs(resolved, exist_ok=True)

        output = "Created directory: {}".format(resolved)
        return ToolResult(
            title="Mkdir: {}".format(os.path.basename(resolved)),
            output=output,
            metadata={"path": resolved, "created": True},
        )


# ---------------------------------------------------------------------------
# RmdirTool
# ---------------------------------------------------------------------------


class RmdirTool(Tool):
    """Remove directories. Supports empty and non-empty directories."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(RmdirTool, self).__init__(
            id="rmdir",
            description=(
                "Remove a directory. Use recursive=True to remove non-empty directories (like rm -rf). "
                "Without recursive flag, only removes empty directories (like rmdir). "
                "PREFER THIS TOOL over 'bash rmdir' or 'bash rm -rf' - always use this for removing directories. "
                "Only use bash for complex shell operations, not simple folder deletion."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the directory to remove.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, remove directory and all its contents (like rm -rf). Default: false.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        dir_path = args["path"]  # type: str
        recursive = args.get("recursive", False)  # type: bool

        workspace = _resolve_workspace(ctx)
        resolved = _secure_resolve(dir_path, workspace)

        # -- Dangerous path check -- #
        blocked_msg = _check_delete_path(resolved, ctx)
        if blocked_msg:
            raise ToolError(blocked_msg)

        if not os.path.isdir(resolved):
            raise ToolError("Directory not found: {}".format(resolved))

        if recursive:
            # Size warning for large recursive deletes
            if not (ctx.extra and ctx.extra.get("dangerous_override")):
                est_size = _estimate_dir_size(resolved)
                if est_size > _RMDIR_RECURSIVE_SIZE_WARN:
                    size_mb = est_size / (1024 * 1024)
                    raise ToolError(
                        "SECURITY: Recursive delete of '{}' would remove ~{:.1f} MB of data. "
                        "Use bash tool with explicit confirmation if this is intended, "
                        "or set dangerous_override=true to bypass this guard.".format(
                            resolved, size_mb
                        )
                    )

            shutil.rmtree(resolved)
            output = "Removed directory and all contents: {}".format(resolved)
        else:
            # Check if directory is empty
            entries = os.listdir(resolved)
            if entries:
                raise ToolError(
                    "Directory is not empty: {} (contains {} item(s)). Use recursive=true to remove anyway.".format(
                        resolved, len(entries)
                    )
                )
            os.rmdir(resolved)
            output = "Removed empty directory: {}".format(resolved)

        return ToolResult(
            title="Rmdir: {}".format(os.path.basename(resolved)),
            output=output,
            metadata={"path": resolved, "recursive": recursive},
        )


# ---------------------------------------------------------------------------
# MvTool
# ---------------------------------------------------------------------------


class MvTool(Tool):
    """Move or rename files and directories."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(MvTool, self).__init__(
            id="mv",
            description=(
                "Move or rename a file or directory. If destination is an existing directory, "
                "moves the source into that directory. Otherwise, renames/moves to the destination path. "
                "PREFER THIS TOOL over 'bash mv' - always use this for moving or renaming files/folders. "
                "Only use bash for complex shell operations, not simple file/folder moves."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source file or directory path to move.",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination path. If source is moved into an existing directory, the filename is preserved.",
                    },
                },
                "required": ["source", "destination"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        source = args["source"]  # type: str
        destination = args["destination"]  # type: str

        workspace = _resolve_workspace(ctx)
        resolved_src = _secure_resolve(source, workspace)
        resolved_dst = _secure_resolve(destination, workspace)

        if not os.path.exists(resolved_src):
            raise ToolError("Source not found: {}".format(resolved_src))

        # If destination is an existing directory, move source into it
        if os.path.isdir(resolved_dst):
            final_dst = os.path.join(resolved_dst, os.path.basename(resolved_src))
        else:
            final_dst = resolved_dst
            # Ensure parent directory exists
            parent = os.path.dirname(final_dst)
            if parent:
                os.makedirs(parent, exist_ok=True)

        shutil.move(resolved_src, final_dst)

        output = "Moved: {} -> {}".format(resolved_src, final_dst)
        return ToolResult(
            title="Mv: {} -> {}".format(
                os.path.basename(resolved_src), os.path.basename(final_dst)
            ),
            output=output,
            metadata={"source": resolved_src, "destination": final_dst},
        )


# ---------------------------------------------------------------------------
# CpTool
# ---------------------------------------------------------------------------


class CpTool(Tool):
    """Copy files and directories."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(CpTool, self).__init__(
            id="cp",
            description=(
                "Copy a file or directory. Use recursive=True to copy directories with all contents (like cp -r). "
                "For files, copies the file to the destination path. "
                "PREFER THIS TOOL over 'bash cp' - always use this for copying files/folders. "
                "Only use bash for complex shell operations, not simple file/folder copies."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source file or directory path to copy.",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination path.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, copy directories recursively (like cp -r). Default: false.",
                    },
                },
                "required": ["source", "destination"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        source = args["source"]  # type: str
        destination = args["destination"]  # type: str
        recursive = args.get("recursive", False)  # type: bool

        workspace = _resolve_workspace(ctx)
        resolved_src = _secure_resolve(source, workspace)
        resolved_dst = _secure_resolve(destination, workspace)

        if not os.path.exists(resolved_src):
            raise ToolError("Source not found: {}".format(resolved_src))

        if os.path.isdir(resolved_src) and not recursive:
            raise ToolError(
                "'{}' is a directory. Use recursive=true to copy directories.".format(resolved_src)
            )

        # If destination is an existing directory, copy into it
        if os.path.isdir(resolved_dst):
            final_dst = os.path.join(resolved_dst, os.path.basename(resolved_src))
        else:
            final_dst = resolved_dst
            # Ensure parent directory exists
            parent = os.path.dirname(final_dst)
            if parent:
                os.makedirs(parent, exist_ok=True)

        if os.path.isdir(resolved_src):
            shutil.copytree(resolved_src, final_dst)
        else:
            shutil.copy2(resolved_src, final_dst)

        output = "Copied: {} -> {}".format(resolved_src, final_dst)
        return ToolResult(
            title="Cp: {} -> {}".format(
                os.path.basename(resolved_src), os.path.basename(final_dst)
            ),
            output=output,
            metadata={"source": resolved_src, "destination": final_dst, "recursive": recursive},
        )


# ---------------------------------------------------------------------------
# RmTool
# ---------------------------------------------------------------------------


class RmTool(Tool):
    """Remove files."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(RmTool, self).__init__(
            id="rm",
            description=(
                "Remove a file. Does NOT remove directories (use rmdir for that). "
                "PREFER THIS TOOL over 'bash rm' - always use this for deleting files. "
                "Only use bash for complex shell operations, not simple file deletion."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to remove.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args["path"]  # type: str

        workspace = _resolve_workspace(ctx)
        resolved = _secure_resolve(file_path, workspace)

        # -- Dangerous path check -- #
        blocked_msg = _check_delete_path(resolved, ctx)
        if blocked_msg:
            raise ToolError(blocked_msg)

        if not os.path.exists(resolved):
            raise ToolError("File not found: {}".format(resolved))

        if os.path.isdir(resolved):
            raise ToolError(
                "'{}' is a directory. Use rmdir tool to remove directories.".format(resolved)
            )

        os.remove(resolved)

        output = "Removed file: {}".format(resolved)
        return ToolResult(
            title="Rm: {}".format(os.path.basename(resolved)),
            output=output,
            metadata={"path": resolved},
        )


# ---------------------------------------------------------------------------
# TouchTool
# ---------------------------------------------------------------------------


class TouchTool(Tool):
    """Create an empty file or update file timestamps."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(TouchTool, self).__init__(
            id="touch",
            description=(
                "Create an empty file if it doesn't exist, or update its modification time if it does. "
                "Automatically creates parent directories if needed. "
                "PREFER THIS TOOL over 'bash touch' - always use this for creating empty files. "
                "Only use bash for complex shell operations, not simple file creation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to create or update.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args["path"]  # type: str

        workspace = _resolve_workspace(ctx)
        resolved = _secure_resolve(file_path, workspace)

        already_existed = os.path.exists(resolved)

        if already_existed and os.path.isdir(resolved):
            raise ToolError("Path is a directory, not a file: {}".format(resolved))

        # Ensure parent directory exists
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Create or update the file
        with open(resolved, "a"):
            pass  # Just open and close to create/touch

        if already_existed:
            output = "Updated timestamp: {}".format(resolved)
        else:
            output = "Created empty file: {}".format(resolved)

        return ToolResult(
            title="Touch: {}".format(os.path.basename(resolved)),
            output=output,
            metadata={"path": resolved, "created": not already_existed},
        )


# ---------------------------------------------------------------------------
# Registration Helper
# ---------------------------------------------------------------------------


def register_file_ops_tools(registry):
    # type: (Any) -> None
    """Register all file operation tools with the given registry.

    Args:
        registry: A ToolRegistry instance to register tools with.
    """
    registry.register(MkdirTool())
    registry.register(RmdirTool())
    registry.register(MvTool())
    registry.register(CpTool())
    registry.register(RmTool())
    registry.register(TouchTool())
