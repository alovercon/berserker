"""Tests for BashTool: shell execution, timeout, cwd, output capture."""

import os
import pytest
import time
import threading

from berserker.tool.base import ToolContext, ToolError, ToolResult
from berserker.tool.bash import BashTool, _decode_bytes, _truncate_output


def _make_ctx(workspace=None, cwd_override=None):
    # type: (str, str) -> ToolContext
    extra = {}  # type: dict
    if workspace:
        extra["workspace"] = workspace
    extra["context_info"] = {
        "current_tokens": 50000,
        "context_limit": 128000,
        "compaction_buffer": 20000,
    }
    return ToolContext(
        session_id="test-s",
        message_id="test-m",
        agent="build",
        abort=threading.Event(),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# BashTool Tests
# ---------------------------------------------------------------------------


class TestBashTool:
    """Test BashTool execution."""

    def test_simple_command(self, temp_workspace):
        """Should execute a simple command and capture output."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        # Use cross-platform command
        if os.name == "nt":
            cmd = "echo Hello World"
        else:
            cmd = "echo Hello World"

        result = tool.execute({"command": cmd}, ctx)

        assert isinstance(result, ToolResult)
        assert "Hello World" in result.output
        assert result.metadata["exit_code"] == 0
        assert result.metadata["timed_out"] is False

    def test_command_with_cwd(self, temp_workspace):
        """Should execute command in specified working directory."""
        # Create a file in workspace
        with open(os.path.join(temp_workspace, "testfile.txt"), "w") as f:
            f.write("exists")

        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        if os.name == "nt":
            cmd = "dir testfile.txt"
        else:
            cmd = "ls testfile.txt"

        result = tool.execute({"command": cmd, "cwd": temp_workspace}, ctx)

        assert "testfile.txt" in result.output
        assert result.metadata["cwd"] == temp_workspace

    def test_command_failure(self, temp_workspace):
        """Should capture failed command output with exit code."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        if os.name == "nt":
            cmd = "dir nonexistent_file_xyz.txt"
        else:
            cmd = "ls nonexistent_file_xyz.txt"

        result = tool.execute({"command": cmd}, ctx)

        assert result.metadata["exit_code"] != 0
        assert "FAILED" in result.output or "Exit code" in result.output

    def test_command_timeout(self, temp_workspace):
        """Should kill process on timeout and return partial output."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        if os.name == "nt":
            # ping is a reliable way to sleep on Windows
            cmd = "ping -n 10 127.0.0.1 >nul"
        else:
            cmd = "sleep 10"

        result = tool.execute({"command": cmd, "timeout": 1}, ctx)

        assert result.metadata["timed_out"] is True
        # Execution should complete within ~timeout seconds (not 10 seconds)
        assert result.metadata["exit_code"] is not None

    def test_stderr_capture(self, temp_workspace):
        """Should capture stderr output."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        if os.name == "nt":
            cmd = "cmd /c echo error message 1>&2"
        else:
            cmd = "echo error message >&2"

        result = tool.execute({"command": cmd}, ctx)

        assert "error message" in result.output

    def test_no_output_command(self, temp_workspace):
        """Should handle commands with no output."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        if os.name == "nt":
            cmd = "cd ."
        else:
            cmd = "true"

        result = tool.execute({"command": cmd}, ctx)

        assert result.metadata["exit_code"] == 0
        assert "no output" in result.output.lower() or result.metadata["exit_code"] == 0

    def test_title_truncated_for_long_command(self, temp_workspace):
        """Should truncate title for very long commands."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        long_cmd = "echo " + "x" * 100
        result = tool.execute({"command": long_cmd}, ctx)

        assert result.title.endswith("...")
        assert len(result.title) <= 60  # 50 chars + "Bash: " + "..."

    def test_file_read_guard_cat(self, temp_workspace):
        """P1: Should warn when using cat to read files."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"command": "cat test.txt"}, ctx)

        assert "Warning" in result.output or "Read tool" in result.output
        assert result.metadata.get("warning") is True

    def test_file_read_guard_head(self, temp_workspace):
        """P1: Should warn when using head to read files."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"command": "head -n 10 test.txt"}, ctx)

        assert "Warning" in result.output or "Read tool" in result.output

    def test_file_read_guard_tail(self, temp_workspace):
        """P1: Should warn when using tail to read files."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"command": "tail -n 5 test.txt"}, ctx)

        assert "Warning" in result.output or "Read tool" in result.output

    def test_file_read_guard_allows_piped_commands(self, temp_workspace):
        """P1: Should allow cat when used in pipes (not simple file read)."""
        tool = BashTool()
        ctx = _make_ctx(temp_workspace)
        # This is a piped command, not a simple file read
        if os.name == "nt":
            cmd = "echo hello | findstr hello"
        else:
            cmd = "echo hello | grep hello"
        result = tool.execute({"command": cmd}, ctx)

        # Should execute normally, not trigger the guard
        assert result.metadata.get("warning") is not True


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestDecodeBytes:
    """Test _decode_bytes helper."""

    def test_utf8_decode(self):
        """Should decode UTF-8 bytes."""
        assert _decode_bytes(b"hello") == "hello"

    def test_utf8_with_unicode(self):
        """Should decode UTF-8 with unicode."""
        assert _decode_bytes(b"\xe4\xbd\xa0\xe5\xa5\xbd") == "你好"

    def test_fallback_encoding(self):
        """Should use fallback encoding for non-UTF-8."""
        # Latin-1 encoded bytes
        data = b"\xe9\xe0\xe8"  # "éàè" in latin-1
        result = _decode_bytes(data, fallback="latin-1")
        assert len(result) == 3


class TestTruncateOutput:
    """Test _truncate_output helper."""

    def test_short_text_not_truncated(self):
        """Short text should pass through unchanged."""
        text, truncated = _truncate_output("hello", max_bytes=100)
        assert text == "hello"
        assert truncated is False

    def test_long_text_truncated(self):
        """Long text should be truncated."""
        text = "x" * 1000
        result, truncated = _truncate_output(text, max_bytes=100)
        assert truncated is True
        assert len(result.encode("utf-8")) <= 100

    def test_unicode_truncation_safe(self):
        """Truncation should not break multi-byte characters."""
        text = "你好" * 100  # Multi-byte characters
        result, truncated = _truncate_output(text, max_bytes=50)
        assert truncated is True
        # Should not raise when decoding
        assert isinstance(result, str)
