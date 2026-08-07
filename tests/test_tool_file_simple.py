"""Tests for simple file tools: ReadTool, WriteTool, LsTool, GlobTool."""

import os
import pytest

from berserker.tool.base import ToolContext, ToolError, ToolResult
from berserker.tool.file_simple import (
    ReadTool,
    WriteTool,
    LsTool,
    GlobTool,
    _resolve_workspace,
    _secure_resolve,
)


# ---------------------------------------------------------------------------
# Helper: make a ToolContext with a given workspace
# ---------------------------------------------------------------------------


def _make_ctx(workspace, **extra_kwargs):
    # type: (str, **object) -> ToolContext
    import threading

    extra = {"workspace": workspace}
    extra.update(extra_kwargs)
    extra.setdefault(
        "context_info",
        {"current_tokens": 50000, "context_limit": 128000, "compaction_buffer": 20000},
    )
    return ToolContext(
        session_id="test-s",
        message_id="test-m",
        agent="build",
        abort=threading.Event(),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# _resolve_workspace Tests
# ---------------------------------------------------------------------------


class TestResolveWorkspace:
    """Test _resolve_workspace helper."""

    def test_returns_workspace_from_extra(self):
        """Should return workspace from ctx.extra['workspace']."""
        ctx = _make_ctx("/tmp/ws")
        assert _resolve_workspace(ctx) == os.path.abspath("/tmp/ws")

    def test_defaults_to_cwd_when_no_extra(self):
        """Should default to current dir when extra is None."""
        import threading

        ctx = ToolContext(
            session_id="s", message_id="m", agent="build", abort=threading.Event(), extra=None
        )
        assert _resolve_workspace(ctx) == os.path.abspath(".")


# ---------------------------------------------------------------------------
# _secure_resolve Tests
# ---------------------------------------------------------------------------


class TestSecureResolve:
    """Test _secure_resolve path security."""

    def test_relative_path_within_workspace(self):
        """Relative paths within workspace should resolve correctly."""
        ws = os.path.abspath("/tmp/ws")
        # _secure_resolve uses os.path.abspath which resolves relative to CWD,
        # so we pass a path that's already joined with workspace
        resolved = _secure_resolve(os.path.join(ws, "src", "main.py"), ws)
        assert resolved == os.path.join(ws, "src", "main.py")

    def test_path_traversal_raises(self):
        """Path traversal (..) that escapes workspace should raise ToolError."""
        ws = os.path.abspath("/tmp/ws")
        with pytest.raises(ToolError) as exc_info:
            _secure_resolve("../../etc/passwd", ws)
        assert "outside the workspace" in str(exc_info.value)

    def test_absolute_path_allowed(self):
        """Absolute paths should be allowed (no traversal check)."""
        ws = os.path.abspath("/tmp/ws")
        resolved = _secure_resolve("E:\\temp\\file.c", ws)
        assert resolved == "E:\\temp\\file.c"

    def test_workspace_root_itself(self):
        """Resolving workspace path itself should return workspace."""
        ws = os.path.abspath("/tmp/ws")
        resolved = _secure_resolve(ws, ws)
        assert resolved == ws


# ---------------------------------------------------------------------------
# ReadTool Tests
# ---------------------------------------------------------------------------


class TestReadTool:
    """Test ReadTool execution."""

    def test_read_small_file(self, temp_workspace):
        """Should read a small file completely."""
        test_file = os.path.join(temp_workspace, "hello.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Hello, World!\nLine 2\nLine 3")

        tool = ReadTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": test_file}, ctx)

        assert isinstance(result, ToolResult)
        assert "Hello, World!" in result.output
        assert "Line 2" in result.output
        assert result.metadata["truncated"] is False
        assert result.metadata["file_size"] > 0

    def test_read_file_not_found(self, temp_workspace):
        """Should raise ToolError for missing file."""
        tool = ReadTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"file_path": os.path.join(temp_workspace, "nonexistent.txt")}, ctx)
        assert "File not found" in str(exc_info.value)

    def test_read_line_range(self, temp_workspace):
        """Should return only the specified line range."""
        test_file = os.path.join(temp_workspace, "lines.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            for i in range(1, 11):
                f.write("Line {}\n".format(i))

        tool = ReadTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": test_file, "start_line": 3, "end_line": 5}, ctx)

        assert "Line 3" in result.output
        assert "Line 4" in result.output
        assert "Line 5" in result.output
        assert "Line 1" not in result.output
        assert "Line 6" not in result.output

    def test_read_large_file_truncated(self, temp_workspace):
        """Should truncate files larger than 100KB."""
        test_file = os.path.join(temp_workspace, "large.txt")
        large_content = "x" * (200 * 1024)  # 200KB
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(large_content)

        tool = ReadTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": test_file}, ctx)

        assert result.metadata["truncated"] is True
        assert "Truncated" in result.output
        assert len(result.output) < len(large_content)

    def test_read_file_header_shows_path(self, temp_workspace):
        """Output should include the resolved file path."""
        test_file = os.path.join(temp_workspace, "test.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("content")

        tool = ReadTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": test_file}, ctx)

        assert "File:" in result.output
        assert os.path.basename(test_file) in result.output or test_file in result.output


# ---------------------------------------------------------------------------
# WriteTool Tests
# ---------------------------------------------------------------------------


class TestWriteTool:
    """Test WriteTool execution."""

    def test_write_new_file(self, temp_workspace):
        """Should create a new file with the given content."""
        target = os.path.join(temp_workspace, "new.txt")
        tool = WriteTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "content": "Hello!"}, ctx)

        assert isinstance(result, ToolResult)
        assert "wrote" in result.output.lower() or "bytes" in result.output
        assert result.metadata["bytes_written"] == 6

        with open(target, "r", encoding="utf-8") as f:
            assert f.read() == "Hello!"

    def test_write_creates_parent_dirs(self, temp_workspace):
        """Should create parent directories automatically."""
        target = os.path.join(temp_workspace, "a", "b", "c", "deep.txt")
        tool = WriteTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "content": "deep content"}, ctx)

        assert os.path.isfile(target)
        with open(target, "r", encoding="utf-8") as f:
            assert f.read() == "deep content"

    def test_write_overwrites_existing(self, temp_workspace):
        """Should overwrite existing file content."""
        target = os.path.join(temp_workspace, "overwrite.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("old content")

        tool = WriteTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "content": "new content"}, ctx)

        with open(target, "r", encoding="utf-8") as f:
            assert f.read() == "new content"

    def test_write_utf8_encoding(self, temp_workspace):
        """Should write UTF-8 content correctly including unicode."""
        target = os.path.join(temp_workspace, "unicode.txt")
        tool = WriteTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "content": "你好世界 🌍"}, ctx)

        with open(target, "r", encoding="utf-8") as f:
            assert f.read() == "你好世界 🌍"

    def test_write_exceeds_size_limit(self, temp_workspace):
        """P0: Should raise ToolError when content exceeds 1MB limit."""
        from berserker.tool.file_simple import _MAX_WRITE_BYTES

        target = os.path.join(temp_workspace, "too_large.txt")
        tool = WriteTool()
        ctx = _make_ctx(temp_workspace)
        # Create content larger than 1MB
        large_content = "x" * (_MAX_WRITE_BYTES + 1024)

        with pytest.raises(ToolError) as exc_info:
            tool.execute({"file_path": target, "content": large_content}, ctx)
        assert "exceeds maximum" in str(exc_info.value)
        assert not os.path.exists(target)

    def test_write_existing_file_warning(self, temp_workspace):
        """P1: Should include warning when overwriting existing file."""
        target = os.path.join(temp_workspace, "existing.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("old content")

        tool = WriteTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "content": "new content"}, ctx)

        assert "already exists" in result.output.lower() or "Warning" in result.output
        with open(target, "r", encoding="utf-8") as f:
            assert f.read() == "new content"


# ---------------------------------------------------------------------------
# LsTool Tests
# ---------------------------------------------------------------------------


class TestLsTool:
    """Test LsTool execution."""

    def test_list_directory(self, temp_workspace):
        """Should list directory contents."""
        # Create some files
        with open(os.path.join(temp_workspace, "a.txt"), "w") as f:
            f.write("a")
        with open(os.path.join(temp_workspace, "b.txt"), "w") as f:
            f.write("bb")
        os.makedirs(os.path.join(temp_workspace, "subdir"))

        tool = LsTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": temp_workspace}, ctx)

        assert isinstance(result, ToolResult)
        assert "a.txt" in result.output
        assert "b.txt" in result.output
        assert "subdir" in result.output
        assert result.metadata["count"] == 3
        assert "a.txt" in result.metadata["entries"]

    def test_list_empty_directory(self, temp_workspace):
        """Should report empty directory."""
        empty_dir = os.path.join(temp_workspace, "empty")
        os.makedirs(empty_dir)

        tool = LsTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": empty_dir}, ctx)

        assert "empty" in result.output.lower() or "0" in str(result.metadata["count"])
        assert result.metadata["count"] == 0

    def test_list_directory_not_found(self, temp_workspace):
        """Should raise ToolError for missing directory."""
        tool = LsTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"path": os.path.join(temp_workspace, "nonexistent")}, ctx)
        assert "Directory not found" in str(exc_info.value)

    def test_list_shows_type_and_size(self, temp_workspace):
        """Should show file type and size in output."""
        test_file = os.path.join(temp_workspace, "size_test.txt")
        with open(test_file, "w") as f:
            f.write("12345")

        tool = LsTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": temp_workspace}, ctx)

        assert "size_test.txt" in result.output
        assert "5 B" in result.output

    def test_list_large_directory_truncated(self, temp_workspace):
        """P0: Should truncate output for directories with many entries."""
        from berserker.tool.file_simple import _MAX_LS_ENTRIES

        # Create more files than the limit
        for i in range(_MAX_LS_ENTRIES + 50):
            with open(os.path.join(temp_workspace, "file_{:04d}.txt".format(i)), "w") as f:
                f.write("x")

        tool = LsTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": temp_workspace}, ctx)

        assert "Truncated" in result.output
        assert result.metadata["count"] == _MAX_LS_ENTRIES


# ---------------------------------------------------------------------------
# GlobTool Tests
# ---------------------------------------------------------------------------


class TestGlobTool:
    """Test GlobTool execution."""

    def test_glob_python_files(self, temp_workspace):
        """Should find all .py files."""
        # Create test structure
        with open(os.path.join(temp_workspace, "main.py"), "w") as f:
            f.write("# main")
        os.makedirs(os.path.join(temp_workspace, "src"))
        with open(os.path.join(temp_workspace, "src", "utils.py"), "w") as f:
            f.write("# utils")
        with open(os.path.join(temp_workspace, "README.md"), "w") as f:
            f.write("# README")

        tool = GlobTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"pattern": "**/*.py"}, ctx)

        assert isinstance(result, ToolResult)
        assert result.metadata["count"] == 2
        assert "main.py" in result.output
        assert "utils.py" in result.output

    def test_glob_no_matches(self, temp_workspace):
        """Should report when no files match."""
        tool = GlobTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"pattern": "**/*.xyz"}, ctx)

        assert result.metadata["count"] == 0
        assert "No files matched" in result.output

    def test_glob_root_not_found(self, temp_workspace):
        """Should raise ToolError if root directory doesn't exist."""
        tool = GlobTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute(
                {"pattern": "*.py", "root": os.path.join(temp_workspace, "nonexistent")}, ctx
            )
        assert "Root directory not found" in str(exc_info.value)

    def test_glob_with_custom_root(self, temp_workspace):
        """Should support custom root directory."""
        sub = os.path.join(temp_workspace, "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "test.py"), "w") as f:
            f.write("# test")

        tool = GlobTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"pattern": "*.py", "root": sub}, ctx)

        assert result.metadata["count"] == 1
        assert "test.py" in result.output

    def test_glob_large_result_set_truncated(self, temp_workspace):
        """P0: Should truncate results when too many files match."""
        from berserker.tool.file_simple import _MAX_GLOB_RESULTS

        # Create more files than the limit
        for i in range(_MAX_GLOB_RESULTS + 50):
            with open(os.path.join(temp_workspace, "file_{:04d}.py".format(i)), "w") as f:
                f.write("# file {}".format(i))

        tool = GlobTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"pattern": "*.py"}, ctx)

        assert "Truncated" in result.output
        assert result.metadata["count"] == _MAX_GLOB_RESULTS
        assert result.metadata["truncated"] is True
