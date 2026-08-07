"""Tests for tool base classes: Tool, ToolContext, ToolResult, and error hierarchy."""

import pytest
import threading

from berserker.tool.base import (
    Tool,
    ToolContext,
    ToolResult,
    ToolError,
    ToolTimeoutError,
    ToolValidationError,
    ToolExecutionError,
    format_validation_error,
)


# ---------------------------------------------------------------------------
# Error Hierarchy Tests
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    """Test the tool error class hierarchy."""

    def test_tool_error_is_exception(self):
        """ToolError should be a subclass of Exception."""
        assert issubclass(ToolError, Exception)

    def test_tool_timeout_error_is_tool_error(self):
        """ToolTimeoutError should be a subclass of ToolError."""
        assert issubclass(ToolTimeoutError, ToolError)

    def test_tool_validation_error_is_tool_error(self):
        """ToolValidationError should be a subclass of ToolError."""
        assert issubclass(ToolValidationError, ToolError)

    def test_tool_execution_error_is_tool_error(self):
        """ToolExecutionError should be a subclass of ToolError."""
        assert issubclass(ToolExecutionError, ToolError)

    def test_tool_execution_error_attributes(self):
        """ToolExecutionError should store tool_id and original_error."""
        original = ValueError("something went wrong")
        err = ToolExecutionError("Failed", tool_id="read", original_error=original)
        assert str(err) == "Failed"
        assert err.tool_id == "read"
        assert err.original_error is original

    def test_catch_tool_error_catches_subclasses(self):
        """Catching ToolError should catch all subclasses."""
        caught = []
        for exc_class in [ToolTimeoutError, ToolValidationError, ToolExecutionError]:
            try:
                raise exc_class("test")
            except ToolError as e:
                caught.append(type(e))
        assert set(caught) == {ToolTimeoutError, ToolValidationError, ToolExecutionError}


# ---------------------------------------------------------------------------
# ToolContext Tests
# ---------------------------------------------------------------------------


class TestToolContext:
    """Test ToolContext dataclass."""

    def test_basic_creation(self):
        """ToolContext should be creatable with required fields."""
        ctx = ToolContext(
            session_id="sess-1",
            message_id="msg-1",
            agent="build",
            abort=threading.Event(),
        )
        assert ctx.session_id == "sess-1"
        assert ctx.message_id == "msg-1"
        assert ctx.agent == "build"
        assert isinstance(ctx.abort, threading.Event)
        assert ctx.call_id is None
        assert ctx.extra is None
        assert ctx.messages == []

    def test_with_optional_fields(self):
        """ToolContext should accept optional fields."""
        ctx = ToolContext(
            session_id="sess-1",
            message_id="msg-1",
            agent="build",
            abort=threading.Event(),
            call_id="call-123",
            extra={"workspace": "/tmp", "context_info": {"current_tokens": 50000}},
            messages=[{"role": "user", "content": "hello"}],
        )
        assert ctx.call_id == "call-123"
        assert ctx.extra["workspace"] == "/tmp"
        assert len(ctx.messages) == 1

    def test_abort_event(self):
        """ToolContext.abort should be a threading.Event that can be set."""
        ctx = ToolContext(
            session_id="sess-1",
            message_id="msg-1",
            agent="build",
            abort=threading.Event(),
        )
        assert not ctx.abort.is_set()
        ctx.abort.set()
        assert ctx.abort.is_set()


# ---------------------------------------------------------------------------
# ToolResult Tests
# ---------------------------------------------------------------------------


class TestToolResult:
    """Test ToolResult dataclass."""

    def test_basic_creation(self):
        """ToolResult should be creatable with title and output."""
        result = ToolResult(title="Success", output="file contents here")
        assert result.title == "Success"
        assert result.output == "file contents here"
        assert result.metadata == {}
        assert result.attachments is None

    def test_with_metadata(self):
        """ToolResult should accept metadata dict."""
        result = ToolResult(
            title="Read",
            output="content",
            metadata={"truncated": True, "outputPath": "/tmp/test.py"},
        )
        assert result.metadata["truncated"] is True
        assert result.metadata["outputPath"] == "/tmp/test.py"

    def test_with_attachments(self):
        """ToolResult should accept attachments list."""
        result = ToolResult(
            title="Screenshot",
            output="Image captured",
            attachments=[{"path": "/tmp/img.png", "type": "image/png"}],
        )
        assert len(result.attachments) == 1
        assert result.attachments[0]["path"] == "/tmp/img.png"


# ---------------------------------------------------------------------------
# Tool Base Class Tests
# ---------------------------------------------------------------------------


class TestToolBase:
    """Test the Tool base class."""

    def test_tool_creation_minimal(self):
        """Tool should be creatable with just id and description."""
        tool = Tool(id="test", description="A test tool")
        assert tool.id == "test"
        assert tool.description == "A test tool"
        assert tool.timeout is None
        assert tool.parameters == {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def test_tool_with_parameters(self):
        """Tool should accept custom parameters schema."""
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        tool = Tool(id="read", description="Read a file", parameters=schema, timeout=30)
        assert tool.parameters == schema
        assert tool.timeout == 30

    def test_tool_execute_raises_not_implemented(self):
        """Base Tool.execute() should raise NotImplementedError."""
        tool = Tool(id="test", description="A test tool")
        with pytest.raises(NotImplementedError):
            tool.execute(
                {},
                ToolContext(session_id="s", message_id="m", agent="build", abort=threading.Event()),
            )

    def test_tool_repr(self):
        """Tool.__repr__ should show id and description."""
        tool = Tool(id="read", description="Read a file")
        assert "read" in repr(tool)
        assert "Read a file" in repr(tool)


# ---------------------------------------------------------------------------
# Concrete Tool Implementation Test
# ---------------------------------------------------------------------------


class TestConcreteTool:
    """Test a minimal concrete tool implementation."""

    def test_concrete_tool_execute(self):
        """A subclass implementing execute() should work."""

        class EchoTool(Tool):
            def execute(self, args, ctx):
                # type: (dict, ToolContext) -> ToolResult
                return ToolResult(title="Echo", output=args.get("text", ""))

        tool = EchoTool(id="echo", description="Echo text")
        ctx = ToolContext(session_id="s", message_id="m", agent="build", abort=threading.Event())
        result = tool.execute({"text": "hello"}, ctx)
        assert result.title == "Echo"
        assert result.output == "hello"


# ---------------------------------------------------------------------------
# format_validation_error Tests
# ---------------------------------------------------------------------------


class TestFormatValidationError:
    """Test format_validation_error function."""

    def test_with_path(self):
        """Should format error with absolute_path."""

        class FakeError(Exception):
            def __init__(self):
                super(FakeError, self).__init__("Expected string")
                self.absolute_path = ["file_path"]
                self.message = "Expected string"

        err = FakeError()
        formatted = format_validation_error(err)
        assert "file_path" in formatted
        assert "Expected string" in formatted

    def test_without_path(self):
        """Should use 'input' as default field name."""

        class FakeError(Exception):
            def __init__(self):
                super(FakeError, self).__init__("Invalid type")
                self.absolute_path = []
                self.message = "Invalid type"

        err = FakeError()
        formatted = format_validation_error(err)
        assert "input" in formatted
        assert "Invalid type" in formatted

    def test_with_nested_path(self):
        """Should join nested path parts with dots."""

        class FakeError(Exception):
            def __init__(self):
                super(FakeError, self).__init__("Required")
                self.absolute_path = ["arguments", "file_path"]
                self.message = "Required"

        err = FakeError()
        formatted = format_validation_error(err)
        assert "arguments.file_path" in formatted
