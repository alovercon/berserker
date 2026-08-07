"""
Simple file operation tools for berserker: read, write, ls, glob.

Provides 4 Tool subclasses:
- ReadTool: Read file content with line range, large file truncation, encoding fallback
- WriteTool: Write file content with auto-creation of parent directories
- LsTool: List directory contents with type, size, mtime
- GlobTool: Recursive file pattern matching

All tools enforce workspace-restricted path security via ctx.extra['workspace'].

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import glob as glob_module
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolError, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_READ_BYTES = 100 * 1024  # 100 KB truncation threshold
_MAX_WRITE_BYTES = 1024 * 1024  # 1 MB max write size (P0 improvement)
_MAX_GLOB_RESULTS = 100  # Max glob results (P0 improvement)
_MAX_LS_ENTRIES = 500  # Max ls directory entries (P0 improvement)

_ENCODING_FALLBACKS = ["utf-8", "cp1252", "gbk", "latin-1"]  # type: List[str]


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


def _secure_resolve(raw_path, workspace):
    # type: (str, str) -> str
    """Resolve a raw path and verify it stays within the workspace.

    Returns the absolute resolved path.
    Raises ToolError if the path escapes the workspace (path traversal).
    """
    # For relative paths, resolve against workspace, NOT current directory
    if not os.path.isabs(raw_path):
        resolved = os.path.abspath(os.path.join(workspace, raw_path))
        norm_workspace = os.path.normpath(workspace)
        if not (resolved == norm_workspace or resolved.startswith(norm_workspace + os.sep)):
            raise ToolError("Path '{}' is outside the workspace '{}'".format(raw_path, workspace))
    else:
        resolved = os.path.abspath(raw_path)

    logger.debug("Path resolved: %s -> %s (workspace: %s)", raw_path, resolved, workspace)
    return resolved


def _read_with_encoding_fallback(file_path):
    # type: (str) -> str
    """Read a text file trying multiple encodings in order.

    Tries: UTF-8 -> cp1252 -> GBK -> latin-1 (always succeeds).
    """
    last_exc = None  # type: Optional[Exception]
    for enc in _ENCODING_FALLBACKS:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception as exc:
            last_exc = exc
            break
    # latin-1 should never fail, but just in case
    if last_exc is not None:
        raise ToolError("Failed to read file '{}': {}".format(file_path, last_exc))
    return ""  # unreachable


def _format_size(size_bytes):
    # type: (int) -> str
    """Format byte size into human-readable string."""
    if size_bytes < 1024:
        return "{} B".format(size_bytes)
    elif size_bytes < 1024 * 1024:
        return "{:.1f} KB".format(size_bytes / 1024.0)
    elif size_bytes < 1024 * 1024 * 1024:
        return "{:.1f} MB".format(size_bytes / (1024.0 * 1024.0))
    else:
        return "{:.1f} GB".format(size_bytes / (1024.0 * 1024.0 * 1024.0))


def _format_mtime(mtime_ts):
    # type: (float) -> str
    """Format a modification timestamp into a readable datetime string."""
    dt = datetime.fromtimestamp(mtime_ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# ReadTool
# ---------------------------------------------------------------------------


class ReadTool(Tool):
    """Read file content with optional line range and encoding auto-detection."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=8192)

    def __init__(self):
        # type: () -> None
        super(ReadTool, self).__init__(
            id="read",
            description=(
                "Read file content with optional line range (1-indexed, inclusive). "
                "FILES LARGER THAN 100KB ARE TRUNCATED silently (only first 100KB returned). "
                "Output is further limited to ~8000 tokens; beyond that content is cut off."
                "\n"
                "STRATEGIES for reading large files:"
                "\n1. Use grep/glob first to find the exact lines/functions you need"
                "\n2. Read specific line ranges with start_line/end_line instead of the whole file"
                "\n3. Read the file header (first 50 lines) to understand structure, then target sections"
                "\n4. For logs or long outputs, read the last N lines (tail) via end_line"
                "\n5. Break large reads into multiple smaller reads of different sections"
                "\n"
                "Encoding auto-detected with fallback: UTF-8 -> cp1252 -> GBK -> latin-1."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to read.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Start line number (1-indexed, inclusive).",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "End line number (1-indexed, inclusive).",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args["file_path"]  # type: str
        start_line = args.get("start_line")  # type: Optional[int]
        end_line = args.get("end_line")  # type: Optional[int]

        workspace = _resolve_workspace(ctx)
        resolved = _secure_resolve(file_path, workspace)

        if not os.path.isfile(resolved):
            raise ToolError("File not found: {}".format(resolved))

        file_size = os.path.getsize(resolved)
        truncated = False  # type: bool

        if file_size > _MAX_READ_BYTES:
            # Read only first 100KB
            with open(resolved, "rb") as f:
                raw = f.read(_MAX_READ_BYTES)
            # Try to decode the truncated bytes
            content = ""  # type: str
            for enc in _ENCODING_FALLBACKS:
                try:
                    content = raw.decode(enc)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            truncated = True
        else:
            content = _read_with_encoding_fallback(resolved)

        # Apply line range if specified
        if start_line is not None or end_line is not None:
            lines = content.splitlines()
            # Convert 1-indexed to 0-indexed
            s = (start_line - 1) if start_line is not None else 0
            e = end_line if end_line is not None else len(lines)
            # Clamp to valid range
            s = max(0, s)
            e = min(len(lines), e)
            content = "\n".join(lines[s:e])

        # Build output
        header = "File: {}\n".format(resolved)
        if truncated:
            header += "[Truncated: file is {}, showing first {} bytes]\n".format(
                _format_size(file_size), _MAX_READ_BYTES
            )
        if start_line is not None or end_line is not None:
            header += "Lines: {}-{}\n".format(
                start_line if start_line is not None else 1,
                end_line if end_line is not None else len(content.splitlines()),
            )

        output = header + "\n" + content

        metadata = {
            "file_path": resolved,
            "file_size": file_size,
            "truncated": truncated,
        }  # type: Dict[str, Any]

        return ToolResult(
            title="Read: {}".format(os.path.basename(resolved)),
            output=output,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# WriteTool
# ---------------------------------------------------------------------------


class WriteTool(Tool):
    """Write content to a file, creating parent directories as needed."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(WriteTool, self).__init__(
            id="write",
            description=(
                "Write content to a file, creating parent directories automatically. OVERWRITES existing files without warning. Use when creating new files or completely replacing file content. Returns bytes written confirmation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args["file_path"]  # type: str
        content = args["content"]  # type: str

        workspace = _resolve_workspace(ctx)
        logger.debug("WriteTool: workspace=%s, target=%s", workspace, file_path)

        resolved = _secure_resolve(file_path, workspace)

        # Create parent directories
        parent_dir = os.path.dirname(resolved)
        if parent_dir:
            logger.debug("WriteTool: creating directories: %s", parent_dir)
            os.makedirs(parent_dir, exist_ok=True)

        # P0: Check content size limit
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > _MAX_WRITE_BYTES:
            raise ToolError(
                "Content size {} exceeds maximum {} ({} MB). "
                "Consider splitting into smaller files.".format(
                    _format_size(len(content_bytes)),
                    _format_size(_MAX_WRITE_BYTES),
                    _MAX_WRITE_BYTES // (1024 * 1024),
                )
            )

        logger.debug("WriteTool: writing %d bytes to %s", len(content_bytes), resolved)
        with open(resolved, "wb") as f:
            f.write(content_bytes)
        # P1: Write-existing-file-guard - warn if file already exists
        if os.path.exists(resolved):
            logger.warning("[write-existing-file-guard] File already exists: %s", resolved)
            # Allow the write but add warning to output
            output = "Warning: File already exists. Consider using the edit tool instead.\n\nSuccessfully wrote {} bytes to {}".format(len(content_bytes), resolved)
        else:
            logger.info("WriteTool: successfully wrote %d bytes to %s", len(content_bytes), resolved)
            output = "Successfully wrote {} bytes to {}".format(len(content_bytes), resolved)
        metadata = {
            "file_path": resolved,
            "bytes_written": len(content_bytes),
        }  # type: Dict[str, Any]

        return ToolResult(
            title="Write: {}".format(os.path.basename(resolved)),
            output=output,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# LsTool
# ---------------------------------------------------------------------------


class LsTool(Tool):
    """List directory contents with type, size, and modification time."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=16384)

    def __init__(self):
        # type: () -> None
        super(LsTool, self).__init__(
            id="ls",
            description=(
                "List directory contents. Shows type (file/dir), size, "
                "and modification time in a formatted table. "
                "Supports optional recursive subdirectory listing. "
                "Use this tool to explore directory structure. "
                "NOTE: To read file contents, use the read tool."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the directory to list.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, list contents recursively (default: false). "
                        "Each file/subdirectory is shown with its relative path from the root.",
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

        if not os.path.isdir(resolved):
            # Check if the path looks truncated or incomplete
            hint = ""
            if not resolved.endswith(("\\", "/")):
                # Try to find the longest existing parent
                parent = os.path.dirname(resolved)
                while parent and len(parent) > 3:
                    if os.path.isdir(parent):
                        hint = "\n\nDid you mean: {} ? The path might be incomplete.".format(parent)
                        break
                    parent = os.path.dirname(parent)
            raise ToolError("Directory not found: {}{}".format(resolved, hint))

        all_entries = []  # type: List[Tuple[str, str, int, float]]  # (rel_path, type, size, mtime)
        if recursive:
            for root, dirs, files in os.walk(resolved):
                for name in dirs:
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, resolved)
                    try:
                        stat = os.stat(full)
                        all_entries.append((rel, "dir", 0, stat.st_mtime))
                    except OSError:
                        all_entries.append((rel, "dir", 0, 0))
                for name in files:
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, resolved)
                    try:
                        stat = os.stat(full)
                        all_entries.append((rel, "file", stat.st_size, stat.st_mtime))
                    except OSError:
                        all_entries.append((rel, "file", 0, 0))
        else:
            for name in sorted(os.listdir(resolved)):
                full = os.path.join(resolved, name)
                try:
                    stat = os.stat(full)
                    is_dir = os.path.isdir(full)
                    all_entries.append((name, "dir" if is_dir else "file",
                                       0 if is_dir else stat.st_size, stat.st_mtime))
                except OSError:
                    all_entries.append((name, "?", 0, 0))

        all_entries.sort(key=lambda e: e[0].lower())

        if not all_entries:
            output = "Directory '{}' is empty.".format(resolved)
            return ToolResult(
                title="Ls: {}".format(os.path.basename(resolved)),
                output=output,
                metadata={"path": resolved, "count": 0},
            )

        # Limit entries
        ls_truncated = len(all_entries) > _MAX_LS_ENTRIES
        if ls_truncated:
            all_entries = all_entries[:_MAX_LS_ENTRIES]

        lines = []  # type: List[str]
        label = "Directory" if not recursive else "Recursive"
        lines.append("{}: {}  ({} entries)".format(label, resolved, len(all_entries)))
        if ls_truncated:
            lines.append("[Truncated: showing first {} of {} entries]".format(_MAX_LS_ENTRIES, len(all_entries)))
        lines.append("")

        for rel, entry_type, size, mtime in all_entries:
            marker = "/" if entry_type == "dir" else ""
            size_str = _format_size(size) if entry_type == "file" else "-"
            mtime_str = _format_mtime(mtime) if mtime else "?"
            lines.append("  {}{}  {}  {}".format(rel, marker, size_str, mtime_str))

        output = "\n".join(lines)

        metadata = {
            "path": resolved,
                "count": len(all_entries),
                "entries": [e[0] for e in all_entries],
        }  # type: Dict[str, Any]

        return ToolResult(
            title="Ls: {}".format(os.path.basename(resolved)),
            output=output,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# GlobTool
# ---------------------------------------------------------------------------


class GlobTool(Tool):
    """Recursive file pattern matching within workspace."""

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=8192)

    def __init__(self):
        # type: () -> None
        super(GlobTool, self).__init__(
            id="glob",
            description=(
                "Find files matching glob patterns like '**/*.py' or 'src/**/*.ts'. Supports recursive ** patterns. Returns matching file paths sorted by modification time. Use when you need to find files by name patterns. 60s timeout, 100 file limit. "
                "IMPORTANT: Use this tool instead of bash 'find' or 'ls -R' commands. "
                "This tool provides structured output with workspace security validation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match (e.g. '**/*.py').",
                    },
                    "root": {
                        "type": "string",
                        "description": "Root directory for the glob search. Defaults to workspace.",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        pattern = args["pattern"]  # type: str
        root = args.get("root")  # type: Optional[str]

        workspace = _resolve_workspace(ctx)

        if root is not None:
            resolved_root = _secure_resolve(root, workspace)
        else:
            resolved_root = workspace

        if not os.path.isdir(resolved_root):
            raise ToolError("Root directory not found: {}".format(resolved_root))

        # Build full pattern
        full_pattern = os.path.join(resolved_root, pattern)

        matches = glob_module.glob(full_pattern, recursive=True)

        # Security: filter out any matches outside workspace
        safe_matches = []  # type: List[str]
        for m in matches:
            abs_m = os.path.abspath(m)
            norm_ws = os.path.normpath(workspace)
            if abs_m == norm_ws or abs_m.startswith(norm_ws + os.sep):
                safe_matches.append(abs_m)

        if not safe_matches:
            output = "No files matched pattern '{}' in {}".format(pattern, resolved_root)
            return ToolResult(
                title="Glob: {}".format(pattern),
                output=output,
                metadata={"pattern": pattern, "root": resolved_root, "count": 0},
            )

        # P0: Limit results to prevent memory exhaustion
        truncated = len(safe_matches) > _MAX_GLOB_RESULTS
        if truncated:
            safe_matches = safe_matches[:_MAX_GLOB_RESULTS]

        lines = []  # type: List[str]
        lines.append("Pattern: {}".format(pattern))
        lines.append("Root: {}".format(resolved_root))
        lines.append("Found {} match(es):".format(len(safe_matches)))
        if truncated:
            lines.append("[Truncated: showing first {} of {} results]".format(_MAX_GLOB_RESULTS, len(safe_matches)))
        lines.append("")
        for m in sorted(safe_matches):
            lines.append("  {}".format(m))

        output = "\n".join(lines)

        metadata = {
            "pattern": pattern,
            "root": resolved_root,
            "count": len(safe_matches),
            "truncated": truncated,
            "matches": sorted(safe_matches),
        }  # type: Dict[str, Any]
        return ToolResult(
            title="Glob: {}".format(pattern),
            output=output,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Registration Helper
# ---------------------------------------------------------------------------


def register_file_simple_tools(registry):
    # type: (Any) -> None
    """Register all 4 file simple tools with the given registry.

    Args:
        registry: A ToolRegistry instance to register tools with.
    """
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(LsTool())
    registry.register(GlobTool())
