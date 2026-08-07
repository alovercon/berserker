"""Tests for ToolRegistry: registration, lookup, execution, validation, timeout, truncation."""

import pytest
import threading
import time

from berserker.tool.base import (
    Tool,
    ToolContext,
    ToolResult,
    ToolError,
    ToolTimeoutError,
    ToolValidationError,
    ToolExecutionError,
)
from berserker.tool.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helper: Simple concrete tool for testing
# ---------------------------------------------------------------------------


class _EchoTool(Tool):
    """A minimal tool that echoes back an argument."""

    def __init__(self, id="echo", description="Echo tool"):
        super(_EchoTool, self).__init__(
            id=id,
            description=description,
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (dict, ToolContext) -> ToolResult
        return ToolResult(title="Echo", output=args.get("text", ""))


class _SlowTool(Tool):
    """A tool that sleeps to test timeout."""

    def execute(self, args, ctx):
        # type: (dict, ToolContext) -> ToolResult
        time.sleep(10)
        return ToolResult(title="Slow", output="done")


class _FailingTool(Tool):
    """A tool that always raises."""

    def execute(self, args, ctx):
        # type: (dict, ToolContext) -> ToolResult
        raise RuntimeError("intentional failure")


class _ValidatingTool(Tool):
    """A tool with a required parameter schema."""

    def __init__(self):
        super(_ValidatingTool, self).__init__(
            id="validate-me",
            description="Requires a 'name' field",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (dict, ToolContext) -> ToolResult
        return ToolResult(title="OK", output="Hello, {}".format(args["name"]))


# ---------------------------------------------------------------------------
# Registration Tests
# ---------------------------------------------------------------------------


class TestRegistration:
    """Test tool registration and lookup."""

    def test_register_and_get(self):
        """Should register a tool and retrieve it by ID."""
        reg = ToolRegistry()
        tool = _EchoTool(id="echo", description="Echo tool")
        reg.register(tool)
        assert reg.get("echo") is tool

    def test_register_non_tool_raises(self):
        """Registering a non-Tool should raise ToolError."""
        reg = ToolRegistry()
        with pytest.raises(ToolError) as exc_info:
            reg.register("not a tool")
        assert "must be an instance of Tool" in str(exc_info.value)

    def test_register_empty_id_raises(self):
        """Registering a tool with empty ID should raise ToolError."""
        reg = ToolRegistry()
        tool = _EchoTool(id="", description="No ID")
        with pytest.raises(ToolError) as exc_info:
            reg.register(tool)
        assert "non-empty id" in str(exc_info.value)

    def test_unregister(self):
        """Should remove a tool from the registry."""
        reg = ToolRegistry()
        reg.register(_EchoTool(id="echo", description="Echo"))
        reg.unregister("echo")
        with pytest.raises(ToolError):
            reg.get("echo")

    def test_unregister_not_found_raises(self):
        """Unregistering a missing tool should raise ToolError."""
        reg = ToolRegistry()
        with pytest.raises(ToolError) as exc_info:
            reg.unregister("nonexistent")
        assert "not found" in str(exc_info.value)

    def test_list(self):
        """Should list all registered tool IDs."""
        reg = ToolRegistry()
        reg.register(_EchoTool(id="a", description="A"))
        reg.register(_EchoTool(id="b", description="B"))
        assert sorted(reg.list()) == ["a", "b"]

    def test_list_all(self):
        """Should list all registered Tool objects."""
        reg = ToolRegistry()
        tool_a = _EchoTool(id="a", description="A")
        tool_b = _EchoTool(id="b", description="B")
        reg.register(tool_a)
        reg.register(tool_b)
        tools = reg.list_all()
        assert len(tools) == 2
        assert tool_a in tools
        assert tool_b in tools

    def test_get_not_found_raises(self):
        """Getting a missing tool should raise ToolError."""
        reg = ToolRegistry()
        with pytest.raises(ToolError) as exc_info:
            reg.get("missing")
        assert "not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Execution Tests
# ---------------------------------------------------------------------------


class TestExecute:
    """Test ToolRegistry.execute() — the main entry point."""

    def _make_ctx(self, workspace="/tmp"):
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

    def test_successful_execution(self):
        """Should return a result dict on success."""
        reg = ToolRegistry()
        reg.register(_EchoTool(id="echo", description="Echo"))
        result = reg.execute("echo", {"text": "hello"}, self._make_ctx())
        assert result["title"] == "Echo"
        assert result["output"] == "hello"

    def test_tool_not_found_returns_error(self):
        """Should return error dict, NOT raise, for missing tool."""
        reg = ToolRegistry()
        result = reg.execute("nonexistent", {}, self._make_ctx())
        assert "error" in result
        assert "error_type" in result
        assert "not found" in result["error"]

    def test_validation_error_returns_error(self):
        """Should return error dict for validation failures."""
        reg = ToolRegistry()
        reg.register(_ValidatingTool())
        result = reg.execute("validate-me", {}, self._make_ctx())  # Missing required 'name'
        assert "error" in result
        assert "error_type" in result
        assert "Validation" in result["error"]

    def test_execution_error_returns_error(self):
        """Should return error dict when tool raises."""
        reg = ToolRegistry()
        reg.register(_FailingTool(id="fail", description="Fails"))
        result = reg.execute("fail", {}, self._make_ctx())
        assert "error" in result
        assert "error_type" in result
        assert "intentional failure" in result["error"]

    def test_timeout_returns_error(self):
        """Should return error dict on timeout."""
        reg = ToolRegistry(default_timeout=1)
        reg.register(_SlowTool(id="slow", description="Slow"))
        result = reg.execute("slow", {}, self._make_ctx())
        assert "error" in result
        assert "error_type" in result
        assert "timed out" in result["error"]

    def test_custom_timeout(self):
        """Should use per-call timeout over registry default."""
        reg = ToolRegistry(default_timeout=60)
        reg.register(_SlowTool(id="slow", description="Slow"))
        result = reg.execute("slow", {}, self._make_ctx(), timeout=1)
        assert "error" in result
        assert "timed out" in result["error"]

    def test_abort_before_start(self):
        """Should return error if abort is set before execution."""
        reg = ToolRegistry()
        reg.register(_EchoTool(id="echo", description="Echo"))
        ctx = self._make_ctx()
        ctx.abort.set()
        result = reg.execute("echo", {}, ctx)
        assert "error" in result
        assert "aborted" in result["error"]

    def test_result_is_dict_not_tool_result(self):
        """execute() should return a dict, not a ToolResult object."""
        reg = ToolRegistry()
        reg.register(_EchoTool(id="echo", description="Echo"))
        result = reg.execute("echo", {"text": "hi"}, self._make_ctx())
        assert isinstance(result, dict)
        assert not isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------


class TestValidation:
    """Test JSON Schema parameter validation."""

    def _make_ctx(self):
        # type: () -> ToolContext
        return ToolContext(
            session_id="test-s",
            message_id="test-m",
            agent="build",
            abort=threading.Event(),
            extra={
                "workspace": "/tmp",
                "context_info": {
                    "current_tokens": 50000,
                    "context_limit": 128000,
                    "compaction_buffer": 20000,
                },
            },
        )

    def test_required_field_missing(self):
        """Should fail when a required field is missing."""
        reg = ToolRegistry()
        reg.register(_ValidatingTool())
        result = reg.execute("validate-me", {}, self._make_ctx())
        assert "error" in result

    def test_wrong_type(self):
        """Should fail when a field has wrong type."""
        reg = ToolRegistry()
        reg.register(_ValidatingTool())
        result = reg.execute("validate-me", {"name": 123}, self._make_ctx())
        assert "error" in result

    def test_additional_properties(self):
        """Should fail when additionalProperties=False and extra fields given."""
        reg = ToolRegistry()
        reg.register(_ValidatingTool())
        result = reg.execute("validate-me", {"name": "test", "extra": "bad"}, self._make_ctx())
        assert "error" in result

    def test_valid_input(self):
        """Should succeed when input matches schema."""
        reg = ToolRegistry()
        reg.register(_ValidatingTool())
        result = reg.execute("validate-me", {"name": "Alice"}, self._make_ctx())
        assert "error" not in result
        assert "Hello, Alice" in result["output"]


# ---------------------------------------------------------------------------
# Truncation Tests
# ---------------------------------------------------------------------------


class TestTruncation:
    """Test output truncation via truncate_result."""

    def _make_ctx(self, current_tokens=50000, context_limit=128000):
        # type: (int, int) -> ToolContext
        return ToolContext(
            session_id="test-s",
            message_id="test-m",
            agent="build",
            abort=threading.Event(),
            extra={
                "workspace": "/tmp",
                "context_info": {
                    "current_tokens": current_tokens,
                    "context_limit": context_limit,
                    "compaction_buffer": 20000,
                },
            },
        )

    def test_short_output_not_truncated(self):
        """Short output should pass through without truncation."""
        reg = ToolRegistry(max_output_tokens=4096)

        class _ShortTool(Tool):
            def execute(self, args, ctx):
                return ToolResult(title="Short", output="hello")

        reg.register(_ShortTool(id="short", description="Short"))
        result = reg.execute("short", {}, self._make_ctx())
        assert result["output"] == "hello"
        assert result["metadata"].get("truncated") is not True

    def test_long_output_truncated(self):
        """Very long output should be truncated."""
        # Use context that forces a very low token budget
        reg = ToolRegistry(max_output_tokens=100)

        class _LongTool(Tool):
            def execute(self, args, ctx):
                return ToolResult(title="Long", output="x" * 100000)

        reg.register(_LongTool(id="long", description="Long"))
        # Set context nearly full so dynamic max is very low
        result = reg.execute(
            "long", {}, self._make_ctx(current_tokens=127800, context_limit=128000)
        )
        assert len(result["output"]) < 100000
        assert result["metadata"].get("truncated") is True
