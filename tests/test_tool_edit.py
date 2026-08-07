"""Tests for complex edit tools: EditTool, MultiEditTool, ApplyPatchTool."""

import os
import pytest
import threading

from berserker.tool.base import ToolContext, ToolError, ToolResult
from berserker.tool.edit_complex import EditTool, MultiEditTool, ApplyPatchTool


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


def _write_file(path, content):
    # type: (str, str) -> None
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _read_file(path):
    # type: (str) -> str
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# EditTool Tests
# ---------------------------------------------------------------------------


class TestEditTool:
    """Test EditTool execution."""

    def test_simple_replacement(self, temp_workspace):
        """Should replace all occurrences of old_text."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "hello world\nhello again\n")

        tool = EditTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute(
            {"file_path": target, "old_text": "hello", "new_text": "goodbye"}, ctx
        )

        assert isinstance(result, ToolResult)
        assert result.metadata["occurrences_replaced"] == 2
        content = _read_file(target)
        assert content == "goodbye world\ngoodbye again\n"

    def test_single_replacement(self, temp_workspace):
        """Should replace a single occurrence."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "foo bar baz\n")

        tool = EditTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "old_text": "bar", "new_text": "QUX"}, ctx)

        assert result.metadata["occurrences_replaced"] == 1
        assert _read_file(target) == "foo QUX baz\n"

    def test_old_text_not_found(self, temp_workspace):
        """Should raise ToolError with suggestions when old_text not found."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "hello world\n")

        tool = EditTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"file_path": target, "old_text": "goodbye", "new_text": "hello"}, ctx)
        assert "not found" in str(exc_info.value)

    def test_file_not_found(self, temp_workspace):
        """Should raise ToolError for missing file."""
        tool = EditTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute(
                {
                    "file_path": os.path.join(temp_workspace, "missing.py"),
                    "old_text": "a",
                    "new_text": "b",
                },
                ctx,
            )
        assert "File not found" in str(exc_info.value)

    def test_multiline_replacement(self, temp_workspace):
        """Should replace multi-line old_text."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "def foo():\n    pass\n\ndef bar():\n    pass\n")

        tool = EditTool()
        ctx = _make_ctx(temp_workspace)
        old = "def foo():\n    pass\n"
        new = "def foo():\n    return 42\n"
        result = tool.execute({"file_path": target, "old_text": old, "new_text": new}, ctx)

        assert result.metadata["occurrences_replaced"] == 1
        assert "return 42" in _read_file(target)


# ---------------------------------------------------------------------------
# MultiEditTool Tests
# ---------------------------------------------------------------------------


class TestMultiEditTool:
    """Test MultiEditTool execution."""

    def test_multiple_edits(self, temp_workspace):
        """Should apply multiple edits to the same file."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "a = 1\nb = 2\nc = 3\n")

        tool = MultiEditTool()
        ctx = _make_ctx(temp_workspace)
        edits = [
            {"old_text": "a = 1", "new_text": "a = 10"},
            {"old_text": "c = 3", "new_text": "c = 30"},
        ]
        result = tool.execute({"file_path": target, "edits": edits}, ctx)

        assert isinstance(result, ToolResult)
        assert result.metadata["edits_applied"] == 2
        assert result.metadata["total_replacements"] == 2
        content = _read_file(target)
        assert "a = 10" in content
        assert "b = 2" in content
        assert "c = 30" in content

    def test_edit_not_found(self, temp_workspace):
        """Should raise ToolError when one edit's old_text not found."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "a = 1\n")

        tool = MultiEditTool()
        ctx = _make_ctx(temp_workspace)
        edits = [
            {"old_text": "a = 1", "new_text": "a = 10"},
            {"old_text": "b = 2", "new_text": "b = 20"},  # doesn't exist
        ]
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"file_path": target, "edits": edits}, ctx)
        assert "not found" in str(exc_info.value)

    def test_edits_applied_back_to_front(self, temp_workspace):
        """Should apply edits from last to first to avoid offset drift."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "line1\nline2\nline3\n")

        tool = MultiEditTool()
        ctx = _make_ctx(temp_workspace)
        # Both edits reference original content — order shouldn't matter
        edits = [
            {"old_text": "line1", "new_text": "LINE1"},
            {"old_text": "line3", "new_text": "LINE3"},
        ]
        result = tool.execute({"file_path": target, "edits": edits}, ctx)

        content = _read_file(target)
        assert content == "LINE1\nline2\nLINE3\n"


# ---------------------------------------------------------------------------
# ApplyPatchTool Tests
# ---------------------------------------------------------------------------


class TestApplyPatchTool:
    """Test ApplyPatchTool execution."""

    def test_apply_simple_patch(self, temp_workspace):
        """Should apply a simple unified diff patch."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "line1\nline2\nline3\n")

        patch = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2_modified
 line3
"""

        tool = ApplyPatchTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "patch": patch}, ctx)

        assert isinstance(result, ToolResult)
        assert result.metadata["applied_hunks"] == 1
        content = _read_file(target)
        assert "line2_modified" in content
        assert "line2\n" not in content

    def test_patch_add_lines(self, temp_workspace):
        """Should apply a patch that adds lines."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "line1\nline3\n")

        patch = """--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 line1
+line2
 line3
"""

        tool = ApplyPatchTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "patch": patch}, ctx)

        content = _read_file(target)
        assert "line2" in content

    def test_patch_remove_lines(self, temp_workspace):
        """Should apply a patch that removes lines."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "line1\nline2\nline3\n")

        patch = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,2 @@
 line1
-line2
 line3
"""

        tool = ApplyPatchTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "patch": patch}, ctx)

        content = _read_file(target)
        assert "line2" not in content

    def test_patch_conflict(self, temp_workspace):
        """Should raise ToolError on patch conflict."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "line1\nDIFFERENT\nline3\n")

        patch = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2_modified
 line3
"""

        tool = ApplyPatchTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"file_path": target, "patch": patch}, ctx)
        assert "conflict" in str(exc_info.value).lower() or "Expected" in str(exc_info.value)

    def test_patch_file_not_found(self, temp_workspace):
        """Should raise ToolError for missing file."""
        tool = ApplyPatchTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute(
                {
                    "file_path": os.path.join(temp_workspace, "missing.py"),
                    "patch": "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n",
                },
                ctx,
            )
        assert "File not found" in str(exc_info.value)

    def test_patch_no_valid_hunks(self, temp_workspace):
        """Should raise ToolError when patch has no valid hunks."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "content\n")

        tool = ApplyPatchTool()
        ctx = _make_ctx(temp_workspace)
        with pytest.raises(ToolError) as exc_info:
            tool.execute({"file_path": target, "patch": "this is not a patch\n"}, ctx)
        assert "No valid hunks" in str(exc_info.value)

    def test_patch_multiple_hunks(self, temp_workspace):
        """Should apply a patch with multiple hunks."""
        target = os.path.join(temp_workspace, "test.py")
        _write_file(target, "a\nb\nc\nd\ne\n")

        patch = """--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
-a
+A
 b
@@ -4,2 +4,2 @@
 d
-e
+E
"""

        tool = ApplyPatchTool()
        ctx = _make_ctx(temp_workspace)
        result = tool.execute({"file_path": target, "patch": patch}, ctx)

        assert result.metadata["applied_hunks"] == 2
        content = _read_file(target)
        assert content.startswith("A\n")
        assert "E\n" in content
