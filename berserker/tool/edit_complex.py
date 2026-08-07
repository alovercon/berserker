"""
Complex editing tools for berserker: edit, multiedit, apply_patch.

Provides 3 Tool subclasses:
- EditTool: Precise text replacement (find old_text, replace with new_text)
- MultiEditTool: Multiple edits on same file (applied back-to-front to avoid offset drift)
- ApplyPatchTool: Apply unified diff patches (parse diff, handle conflicts)

All tools enforce workspace-restricted path security via ctx.extra['workspace'].

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import difflib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolError, ToolResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_SUGGESTIONS = 5  # type: int

# Edit tool: block modifications to these file patterns
_BLOCKED_EDIT_PATTERNS = [
    re.compile(r'\.(?:exe|dll|pyd|so|dylib|bin)$', re.IGNORECASE),  # Binaries
    re.compile(r'\.(?:pyc|pyo)$'),  # Python bytecode
]


# ---------------------------------------------------------------------------
# Helpers (reused from file_simple pattern)
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
    if not os.path.isabs(raw_path):
        resolved = os.path.abspath(os.path.join(workspace, raw_path))
        norm_workspace = os.path.normpath(workspace)
        if not (resolved == norm_workspace or resolved.startswith(norm_workspace + os.sep)):
            raise ToolError("Path '{}' is outside the workspace '{}'".format(raw_path, workspace))
    else:
        resolved = os.path.abspath(raw_path)

    return resolved


def _read_file_text(file_path):
    # type: (str) -> str
    """Read a text file with encoding fallback."""
    encodings = ["utf-8", "cp1252", "gbk", "latin-1"]  # type: List[str]
    last_exc = None  # type: Optional[Exception]
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception as exc:
            last_exc = exc
            break
    if last_exc is not None:
        raise ToolError("Failed to read file '{}': {}".format(file_path, last_exc))
    return ""


def _write_file_text(file_path, content):
    # type: (str, str) -> None
    """Write text content to a file with UTF-8 encoding."""
    parent_dir = os.path.dirname(file_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(file_path, "wb") as f:
        f.write(content.encode("utf-8"))


def _check_edit_path(resolved, ctx):
    # type: (str, ToolContext) -> Optional[str]
    """Check if a file path is blocked for editing (binary files, etc).

    Returns an error message string if blocked, or None if safe.
    """
    if ctx.extra and ctx.extra.get("dangerous_override"):
        return None

    for pattern in _BLOCKED_EDIT_PATTERNS:
        if pattern.search(resolved):
            return (
                "SECURITY: Editing '{}' is not allowed. This file type is a binary or compiled "
                "format and cannot be safely edited as text. "
                "Use bash tool if you absolutely need to modify it."
            ).format(resolved)

    return None


def _find_similar_lines(old_text, file_content, max_suggestions):
    # type: (str, str, int) -> List[str]
    """Find lines in file_content similar to old_text using difflib.

    Returns a list of similar text snippets.
    """
    file_lines = file_content.splitlines()
    old_lines = old_text.splitlines()

    # If old_text is a single line, compare against individual lines
    if len(old_lines) == 1:
        matches = difflib.get_close_matches(old_lines[0], file_lines, n=max_suggestions, cutoff=0.4)
        if matches:
            return matches

    # For multi-line old_text, try to find similar line sequences
    # Slide a window of the same size over file lines
    window_size = len(old_lines)
    if window_size > len(file_lines):
        return []

    # Build candidate strings from windows
    candidates = []  # type: List[str]
    for i in range(len(file_lines) - window_size + 1):
        candidate = "\n".join(file_lines[i : i + window_size])
        candidates.append(candidate)

    matches = difflib.get_close_matches(old_text, candidates, n=max_suggestions, cutoff=0.4)
    return matches


# ---------------------------------------------------------------------------
# EditTool
# ---------------------------------------------------------------------------


class EditTool(Tool):
    """Precise text replacement: find old_text in file, replace with new_text.

    If old_text appears multiple times, ALL occurrences are replaced.
    If old_text is not found, returns error with similar text suggestions.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=20, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(EditTool, self).__init__(
            id="edit",
            description=(
                "Precisely replace text in a file. Finds ALL occurrences of old_text and replaces them with new_text. If old_text is not found, returns similar text suggestions. WARNING: When multiple matches exist, ALL are replaced - use multiedit for targeted single-location edits. Use for simple, unambiguous text replacements."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find and replace.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Text to replace old_text with.",
                    },
                },
                "required": ["file_path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args["file_path"]  # type: str
        old_text = args["old_text"]  # type: str
        new_text = args["new_text"]  # type: str

        workspace = _resolve_workspace(ctx)
        resolved = _secure_resolve(file_path, workspace)

        # -- Binary file check -- #
        blocked_msg = _check_edit_path(resolved, ctx)
        if blocked_msg:
            raise ToolError(blocked_msg)

        if not os.path.isfile(resolved):
            raise ToolError("File not found: {}".format(resolved))

        content = _read_file_text(resolved)

        # Count occurrences
        count = content.count(old_text)

        if count == 0:
            # old_text not found — provide similar text suggestions
            suggestions = _find_similar_lines(old_text, content, _MAX_SUGGESTIONS)
            error_msg = "old_text not found in file '{}'".format(resolved)
            if suggestions:
                error_msg += "\n\nSimilar text found in file:"
                for i, s in enumerate(suggestions, 1):
                    error_msg += "\n  {}. {}".format(i, s)
            raise ToolError(error_msg)

        # Replace ALL occurrences
        new_content = content.replace(old_text, new_text)
        _write_file_text(resolved, new_content)

        output = "Replaced {} occurrence(s) of old_text with new_text in {}".format(count, resolved)

        metadata = {
            "file_path": resolved,
            "occurrences_replaced": count,
        }  # type: Dict[str, Any]

        return ToolResult(
            title="Edit: {}".format(os.path.basename(resolved)),
            output=output,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# MultiEditTool
# ---------------------------------------------------------------------------


class MultiEditTool(Tool):
    """Apply multiple precise edits to the same file.

    Edits are applied from LAST to FIRST to avoid offset drift.
    Each edit is a precise text replacement (old_text -> new_text).
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=20, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(MultiEditTool, self).__init__(
            id="multiedit",
            description=(
                "Apply multiple text edits to a single file in one call. Each edit specifies old_text and new_text. Edits applied from LAST to FIRST to avoid offset drift. Use when making 2+ changes to the same file - more efficient than multiple edit calls. All old_text values must match exactly."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "edits": {
                        "type": "array",
                        "description": "List of edits to apply. Each edit is an object with 'old_text' and 'new_text'.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": "Exact text to find.",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "Text to replace old_text with.",
                                },
                            },
                            "required": ["old_text", "new_text"],
                            "additionalProperties": False,
                        },
                        "minItems": 1,
                    },
                },
                "required": ["file_path", "edits"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args["file_path"]  # type: str
        edits = args["edits"]  # type: List[Dict[str, str]]

        workspace = _resolve_workspace(ctx)
        resolved = _secure_resolve(file_path, workspace)

        # -- Binary file check -- #
        blocked_msg = _check_edit_path(resolved, ctx)
        if blocked_msg:
            raise ToolError(blocked_msg)

        if not os.path.isfile(resolved):
            raise ToolError("File not found: {}".format(resolved))

        content = _read_file_text(resolved)

        # Apply edits from LAST to FIRST to avoid offset drift
        total_replacements = 0  # type: int
        edit_results = []  # type: List[str]

        for idx in range(len(edits) - 1, -1, -1):
            edit = edits[idx]
            old_text = edit["old_text"]  # type: str
            new_text = edit["new_text"]  # type: str

            count = content.count(old_text)
            if count == 0:
                suggestions = _find_similar_lines(old_text, content, _MAX_SUGGESTIONS)
                error_msg = "Edit #{}: old_text not found in file '{}'".format(idx + 1, resolved)
                if suggestions:
                    error_msg += "\n\nSimilar text found:"
                    for i, s in enumerate(suggestions, 1):
                        error_msg += "\n  {}. {}".format(i, s)
                raise ToolError(error_msg)

            content = content.replace(old_text, new_text)
            total_replacements += count
            edit_results.append("Edit #{}: replaced {} occurrence(s)".format(idx + 1, count))

        # Write the final content
        _write_file_text(resolved, content)

        # Build output summary (in original order)
        lines = []  # type: List[str]
        lines.append("Applied {} edit(s) to {}".format(len(edits), resolved))
        lines.append("Total replacements: {}".format(total_replacements))
        lines.append("")
        # Reverse back to original order for display
        for line in reversed(edit_results):
            lines.append("  {}".format(line))

        output = "\n".join(lines)

        metadata = {
            "file_path": resolved,
            "edits_applied": len(edits),
            "total_replacements": total_replacements,
        }  # type: Dict[str, Any]

        return ToolResult(
            title="MultiEdit: {}".format(os.path.basename(resolved)),
            output=output,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# ApplyPatchTool
# ---------------------------------------------------------------------------


class ApplyPatchTool(Tool):
    """Apply a unified diff patch to a file.

    Parses unified diff format (--- a/file, +++ b/file, @@ -old,start +new,count @@).
    On conflict (hunk doesn't match), returns clear error with expected vs actual content.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=20, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(ApplyPatchTool, self).__init__(
            id="apply_patch",
            description=(
                "Apply a unified diff patch to a file. Parses standard unified diff format (--- /+++ headers, @@ hunk headers, +/- context lines). Returns clear error with expected vs actual content if hunk doesn't match. Use when you have a pre-generated patch from git diff or similar."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to patch.",
                    },
                    "patch": {
                        "type": "string",
                        "description": "Unified diff patch content to apply.",
                    },
                },
                "required": ["file_path", "patch"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args["file_path"]  # type: str
        patch = args["patch"]  # type: str

        workspace = _resolve_workspace(ctx)
        resolved = _secure_resolve(file_path, workspace)

        # -- Binary file check -- #
        blocked_msg = _check_edit_path(resolved, ctx)
        if blocked_msg:
            raise ToolError(blocked_msg)

        if not os.path.isfile(resolved):
            raise ToolError("File not found: {}".format(resolved))

        content = _read_file_text(resolved)
        file_lines = content.splitlines(True)  # keep line endings

        # Parse the patch into hunks
        hunks = self._parse_patch(patch)

        if not hunks:
            raise ToolError("No valid hunks found in patch")

        # Apply hunks sequentially
        total_hunks = len(hunks)
        applied_hunks = 0  # type: int
        hunk_details = []  # type: List[str]

        for hunk_idx, hunk in enumerate(hunks):
            old_start = hunk["old_start"]  # type: int
            old_count = hunk["old_count"]  # type: int
            old_lines = hunk["old_lines"]  # type: List[str]
            new_lines = hunk["new_lines"]  # type: List[str]

            # Convert 1-indexed old_start to 0-indexed
            expected_start = old_start - 1  # type: int

            # Verify the old_lines match the file content at expected position
            actual_segment = file_lines[expected_start : expected_start + old_count]

            # Normalize for comparison (strip trailing newlines for matching)
            normalized_old = [l.rstrip("\n\r") for l in old_lines]
            normalized_actual = [l.rstrip("\n\r") for l in actual_segment]

            if normalized_old != normalized_actual:
                # Conflict detected
                error_msg = (
                    "Patch conflict at hunk {}/{} (line {}):\n\nExpected {} line(s):\n".format(
                        hunk_idx + 1, total_hunks, old_start, len(old_lines)
                    )
                )
                for i, line in enumerate(normalized_old, 1):
                    error_msg += "  {}: {}\n".format(i, line)

                error_msg += "\nActual {} line(s) in file:\n".format(len(actual_segment))
                for i, line in enumerate(normalized_actual, 1):
                    error_msg += "  {}: {}\n".format(i, line)

                raise ToolError(error_msg)

            # Apply the hunk: replace old_lines segment with new_lines
            # new_lines already have their endings from parsing
            file_lines[expected_start : expected_start + old_count] = new_lines
            applied_hunks += 1
            hunk_details.append(
                "Hunk {}/{} applied at line {} ({} -> {} lines)".format(
                    hunk_idx + 1, total_hunks, old_start, old_count, len(new_lines)
                )
            )

        # Write patched content
        new_content = "".join(file_lines)
        _write_file_text(resolved, new_content)

        lines = []  # type: List[str]
        lines.append("Applied {}/{} hunk(s) to {}".format(applied_hunks, total_hunks, resolved))
        lines.append("")
        for detail in hunk_details:
            lines.append("  {}".format(detail))

        output = "\n".join(lines)

        metadata = {
            "file_path": resolved,
            "total_hunks": total_hunks,
            "applied_hunks": applied_hunks,
        }  # type: Dict[str, Any]

        return ToolResult(
            title="ApplyPatch: {}".format(os.path.basename(resolved)),
            output=output,
            metadata=metadata,
        )

    def _parse_patch(self, patch):
        # type: (str) -> List[Dict[str, Any]]
        """Parse unified diff patch text into a list of hunk dicts.

        Each hunk dict contains:
            old_start: int — starting line in old file (1-indexed)
            old_count: int — number of lines in old file
            new_start: int — starting line in new file (1-indexed)
            new_count: int — number of lines in new file
            old_lines: List[str] — context and removed lines
            new_lines: List[str] — context and added lines

        Returns an empty list if no valid hunks are found.
        """
        hunks = []  # type: List[Dict[str, Any]]
        lines = patch.splitlines(True)  # keep line endings

        i = 0  # type: int
        while i < len(lines):
            line = lines[i]

            # Look for @@ hunk header
            if line.startswith("@@"):
                # Parse @@ -old_start,old_count +new_start,new_count @@
                hunk_info = self._parse_hunk_header(line)
                if hunk_info is None:
                    i += 1
                    continue

                old_start, old_count, new_start, new_count = hunk_info

                old_lines = []  # type: List[str]
                new_lines = []  # type: List[str]

                i += 1
                old_consumed = 0  # type: int
                new_consumed = 0  # type: int

                while i < len(lines) and (old_consumed < old_count or new_consumed < new_count):
                    hunk_line = lines[i]

                    if hunk_line.startswith("+"):
                        # Added line (only in new) — keep raw with ending
                        new_lines.append(hunk_line[1:])
                        new_consumed += 1
                    elif hunk_line.startswith("-"):
                        # Removed line (only in old) — keep raw with ending
                        old_lines.append(hunk_line[1:])
                        old_consumed += 1
                    elif hunk_line.startswith(" ") or (
                        hunk_line == "\n" and old_consumed < old_count
                    ):
                        # Context line (in both old and new) — keep raw
                        ctx_line = hunk_line[1:] if hunk_line.startswith(" ") else "\n"
                        old_lines.append(ctx_line)
                        new_lines.append(ctx_line)
                        old_consumed += 1
                        new_consumed += 1
                    elif hunk_line.startswith("\\"):
                        # "\ No newline at end of file" — skip
                        i += 1
                        continue
                    elif hunk_line.startswith("@@"):
                        # Next hunk header — stop here
                        break
                    elif hunk_line.startswith("---") or hunk_line.startswith("+++"):
                        # File header — stop here
                        break
                    else:
                        # Unknown line, treat as end of hunk
                        break

                    i += 1

                hunks.append(
                    {
                        "old_start": old_start,
                        "old_count": old_count,
                        "new_start": new_start,
                        "new_count": new_count,
                        "old_lines": old_lines,
                        "new_lines": new_lines,
                    }
                )
            else:
                i += 1

        return hunks

    def _parse_hunk_header(self, header_line):
        # type: (str) -> Optional[Tuple[int, int, int, int]]
        """Parse a @@ hunk header line into (old_start, old_count, new_start, new_count).

        Returns None if the header cannot be parsed.
        """
        # Expected format: @@ -old_start,old_count +new_start,new_count @@
        # or: @@ -old_start +new_start @@ (count defaults to 1)
        import re

        m = re.match(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", header_line)
        if not m:
            return None

        old_start = int(m.group(1))
        old_count = int(m.group(2)) if m.group(2) else 1
        new_start = int(m.group(3))
        new_count = int(m.group(4)) if m.group(4) else 1

        return (old_start, old_count, new_start, new_count)


# ---------------------------------------------------------------------------
# Registration Helper
# ---------------------------------------------------------------------------


def register_edit_tools(registry):
    # type: (Any) -> None
    """Register all 3 complex editing tools with the given registry.

    Args:
        registry: A ToolRegistry instance to register tools with.
    """
    registry.register(EditTool())
    registry.register(MultiEditTool())
    registry.register(ApplyPatchTool())
