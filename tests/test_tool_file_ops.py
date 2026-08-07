"""Tests for file operation tools: MkdirTool, RmdirTool, MvTool, CpTool, RmTool, TouchTool."""

import os
import pytest
import threading

from berserker.tool.base import ToolContext, ToolError, ToolResult
from berserker.tool.file_ops import (
    MkdirTool,
    RmdirTool,
    MvTool,
    CpTool,
    RmTool,
    TouchTool,
)


def _make_ctx(workspace):
    # type: (str) -> ToolContext
    return ToolContext(
        session_id="test-s",
        message_id="test-m",
        agent="build",
        abort=threading.Event(),
        extra={
            "workspace": workspace,
            "context_info": {
                "current_tokens": 50000,
                "context_limit": 128000,
                "compaction_buffer": 20000,
            },
        },
    )


# ---------------------------------------------------------------------------
# MkdirTool Tests
# ---------------------------------------------------------------------------


class TestMkdirTool:
    """Test MkdirTool execution."""

    def test_create_directory(self, temp_workspace):
        """Should create a new directory."""
        target = os.path.join(temp_workspace, "newdir")
        tool = MkdirTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target}, ctx)

        assert isinstance(result, ToolResult)
        assert os.path.isdir(target)
        assert result.metadata.get("created") is True

    def test_create_nested_directories(self, temp_workspace):
        """Should create nested directories like mkdir -p."""
        target = os.path.join(temp_workspace, "a", "b", "c")
        tool = MkdirTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target}, ctx)

        assert os.path.isdir(target)
        assert result.metadata.get("created") is True

    def test_existing_directory_noop(self, temp_workspace):
        """Should return success without error if directory exists."""
        target = os.path.join(temp_workspace, "existing")
        os.makedirs(target)

        tool = MkdirTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target}, ctx)

        assert result.metadata.get("already_exists") is True
        assert "already exists" in result.output


# ---------------------------------------------------------------------------
# RmdirTool Tests
# ---------------------------------------------------------------------------


class TestRmdirTool:
    """Test RmdirTool execution."""

    def test_remove_empty_directory(self, temp_workspace):
        """Should remove an empty directory."""
        target = os.path.join(temp_workspace, "emptydir")
        os.makedirs(target)

        tool = RmdirTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target}, ctx)

        assert not os.path.exists(target)
        assert "Removed empty directory" in result.output

    def test_remove_non_empty_fails_without_recursive(self, temp_workspace):
        """Should fail to remove non-empty directory without recursive flag."""
        target = os.path.join(temp_workspace, "notempty")
        os.makedirs(target)
        with open(os.path.join(target, "file.txt"), "w") as f:
            f.write("content")

        tool = RmdirTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"path": target}, ctx)
        assert "not empty" in str(exc_info.value).lower()

    def test_remove_non_empty_with_recursive(self, temp_workspace):
        """Should remove non-empty directory with recursive=True."""
        target = os.path.join(temp_workspace, "notempty")
        os.makedirs(target)
        with open(os.path.join(target, "file.txt"), "w") as f:
            f.write("content")

        tool = RmdirTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target, "recursive": True}, ctx)

        assert not os.path.exists(target)
        assert result.metadata.get("recursive") is True

    def test_remove_directory_not_found(self, temp_workspace):
        """Should raise ToolError for missing directory."""
        tool = RmdirTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"path": os.path.join(temp_workspace, "nonexistent")}, ctx)
        assert "Directory not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# MvTool Tests
# ---------------------------------------------------------------------------


class TestMvTool:
    """Test MvTool execution."""

    def test_rename_file(self, temp_workspace):
        """Should rename a file."""
        src = os.path.join(temp_workspace, "old.txt")
        dst = os.path.join(temp_workspace, "new.txt")
        with open(src, "w") as f:
            f.write("content")

        tool = MvTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"source": src, "destination": dst}, ctx)

        assert not os.path.exists(src)
        assert os.path.exists(dst)
        with open(dst, "r") as f:
            assert f.read() == "content"

    def test_move_file_into_directory(self, temp_workspace):
        """Should move file into existing directory."""
        src = os.path.join(temp_workspace, "file.txt")
        dst_dir = os.path.join(temp_workspace, "dest")
        os.makedirs(dst_dir)
        with open(src, "w") as f:
            f.write("content")

        tool = MvTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"source": src, "destination": dst_dir}, ctx)

        assert not os.path.exists(src)
        assert os.path.exists(os.path.join(dst_dir, "file.txt"))

    def test_move_directory(self, temp_workspace):
        """Should move a directory."""
        src = os.path.join(temp_workspace, "olddir")
        dst = os.path.join(temp_workspace, "newdir")
        os.makedirs(src)
        with open(os.path.join(src, "file.txt"), "w") as f:
            f.write("content")

        tool = MvTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"source": src, "destination": dst}, ctx)

        assert not os.path.exists(src)
        assert os.path.isdir(dst)
        assert os.path.exists(os.path.join(dst, "file.txt"))

    def test_move_source_not_found(self, temp_workspace):
        """Should raise ToolError for missing source."""
        tool = MvTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute(
                {"source": os.path.join(temp_workspace, "missing"), "destination": "/tmp/dst"}, ctx
            )
        assert "Source not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# CpTool Tests
# ---------------------------------------------------------------------------


class TestCpTool:
    """Test CpTool execution."""

    def test_copy_file(self, temp_workspace):
        """Should copy a file."""
        src = os.path.join(temp_workspace, "original.txt")
        dst = os.path.join(temp_workspace, "copy.txt")
        with open(src, "w") as f:
            f.write("original content")

        tool = CpTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"source": src, "destination": dst}, ctx)

        assert os.path.exists(src)  # Original still exists
        assert os.path.exists(dst)
        with open(dst, "r") as f:
            assert f.read() == "original content"

    def test_copy_directory_recursive(self, temp_workspace):
        """Should copy directory with recursive=True."""
        src = os.path.join(temp_workspace, "srcdir")
        dst = os.path.join(temp_workspace, "dstdir")
        os.makedirs(src)
        with open(os.path.join(src, "file.txt"), "w") as f:
            f.write("content")

        tool = CpTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"source": src, "destination": dst, "recursive": True}, ctx)

        assert os.path.isdir(dst)
        assert os.path.exists(os.path.join(dst, "file.txt"))

    def test_copy_directory_without_recursive_fails(self, temp_workspace):
        """Should fail to copy directory without recursive flag."""
        src = os.path.join(temp_workspace, "srcdir")
        dst = os.path.join(temp_workspace, "dstdir")
        os.makedirs(src)

        tool = CpTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"source": src, "destination": dst}, ctx)
        assert "directory" in str(exc_info.value).lower()
        assert "recursive" in str(exc_info.value).lower()

    def test_copy_source_not_found(self, temp_workspace):
        """Should raise ToolError for missing source."""
        tool = CpTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute(
                {"source": os.path.join(temp_workspace, "missing"), "destination": "/tmp/dst"}, ctx
            )
        assert "Source not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# RmTool Tests
# ---------------------------------------------------------------------------


class TestRmTool:
    """Test RmTool execution."""

    def test_remove_file(self, temp_workspace):
        """Should remove a file."""
        target = os.path.join(temp_workspace, "toremove.txt")
        with open(target, "w") as f:
            f.write("delete me")

        tool = RmTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target}, ctx)

        assert not os.path.exists(target)
        assert "Removed file" in result.output

    def test_remove_file_not_found(self, temp_workspace):
        """Should raise ToolError for missing file."""
        tool = RmTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"path": os.path.join(temp_workspace, "missing.txt")}, ctx)
        assert "File not found" in str(exc_info.value)

    def test_remove_directory_fails(self, temp_workspace):
        """Should fail to remove a directory (use rmdir instead)."""
        target = os.path.join(temp_workspace, "adir")
        os.makedirs(target)

        tool = RmTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"path": target}, ctx)
        assert "directory" in str(exc_info.value).lower()
        assert "rmdir" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# TouchTool Tests
# ---------------------------------------------------------------------------


class TestTouchTool:
    """Test TouchTool execution."""

    def test_create_empty_file(self, temp_workspace):
        """Should create a new empty file."""
        target = os.path.join(temp_workspace, "new.txt")

        tool = TouchTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target}, ctx)

        assert os.path.isfile(target)
        assert os.path.getsize(target) == 0
        assert result.metadata.get("created") is True
        assert "Created empty file" in result.output

    def test_update_existing_file(self, temp_workspace):
        """Should update timestamp of existing file without changing content."""
        target = os.path.join(temp_workspace, "existing.txt")
        with open(target, "w") as f:
            f.write("original content")

        original_mtime = os.path.getmtime(target)

        tool = TouchTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target}, ctx)

        assert os.path.isfile(target)
        with open(target, "r") as f:
            assert f.read() == "original content"
        assert result.metadata.get("created") is False
        assert "Updated timestamp" in result.output

    def test_creates_parent_directories(self, temp_workspace):
        """Should create parent directories if needed."""
        target = os.path.join(temp_workspace, "a", "b", "c", "touch.txt")

        tool = TouchTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"path": target}, ctx)

        assert os.path.isfile(target)

    def test_touch_directory_fails(self, temp_workspace):
        """Should fail to touch a directory."""
        target = os.path.join(temp_workspace, "adir")
        os.makedirs(target)

        tool = TouchTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"path": target}, ctx)
        assert "directory" in str(exc_info.value).lower()
