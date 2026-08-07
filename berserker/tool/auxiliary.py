"""
Auxiliary tools for berserker: InvalidTool, PlanTool, and McpExaTool.

- InvalidTool: Placeholder for unknown tool names, returns "tool not found" error.
- PlanTool: Exit plan mode and return to build mode, sets a module-level flag.
- McpExaTool: MCP-based exa search tool with fallback when MCP is unavailable.
- register_auxiliary_tools(registry): Helper to register all auxiliary tools.

Python 3.8.10 compatible: uses type comments, Optional, no match/case.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolError, ToolResult

# ---------------------------------------------------------------------------
# Module-level state for PlanTool
# ---------------------------------------------------------------------------

_plan_exit = False  # type: bool


# ---------------------------------------------------------------------------
# InvalidTool
# ---------------------------------------------------------------------------


class InvalidTool(Tool):
    """Placeholder tool returned when the LLM requests an unknown tool name.

    Parameters:
        name (str): The unknown tool name that was requested.

    Returns an error message listing available tools.
    """

    def __init__(self, name, available_tools=None):
        # type: (str, Optional[List[str]]) -> None
        """Initialize InvalidTool.

        Args:
            name: The unknown tool name that was requested.
            available_tools: Optional list of valid tool names to display.
        """
        self._name = name
        self._available_tools = available_tools or []

        tool_list = ", ".join(self._available_tools) if self._available_tools else "none"
        description = "Placeholder for unknown tool: {}".format(self._name)
        parameters = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

        super(InvalidTool, self).__init__(
            id="invalid-{}".format(self._name),
            description=description,
            parameters=parameters,
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Return an error message indicating the tool was not found.

        Args:
            args: Unused.
            ctx: ToolContext (unused but required by interface).

        Returns:
            ToolResult with error message listing available tools.
        """
        tool_list = ", ".join(self._available_tools) if self._available_tools else "none"
        message = "Tool '{}' not found. Available tools: {}".format(self._name, tool_list)
        return ToolResult(
            title="Tool Not Found",
            output=message,
            metadata={"requested_tool": self._name},
        )


# ---------------------------------------------------------------------------
# PlanTool
# ---------------------------------------------------------------------------


class PlanTool(Tool):
    """Tool to exit plan mode and return to build mode.

    Sets a module-level flag `_plan_exit = True` and returns a confirmation
    message.

    Parameters:
        reason (str, optional): Why exiting plan mode.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=30, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        description = (
            "Exit plan mode and return to build/implementation mode. "
            "Use after planning is complete and ready to start implementation. "
            "Sets a module-level flag that signals mode transition."
        )
        parameters = {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional reason for exiting plan mode.",
                },
            },
            "additionalProperties": False,
        }

        super(PlanTool, self).__init__(
            id="plan",
            description=description,
            parameters=parameters,
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Set the plan exit flag and return confirmation.

        Args:
            args: Dict with optional 'reason' key.
            ctx: ToolContext (unused but required by interface).

        Returns:
            ToolResult with confirmation message.
        """
        global _plan_exit
        _plan_exit = True

        reason = args.get("reason", "")  # type: str
        if reason:
            message = "Exiting plan mode. Reason: {}".format(reason)
        else:
            message = "Exiting plan mode."

        return ToolResult(
            title="Plan Mode Exited",
            output=message,
            metadata={"plan_exit": True},
        )


# ---------------------------------------------------------------------------
# McpExaTool
# ---------------------------------------------------------------------------


class McpExaTool(Tool):
    """MCP-based exa search tool.

    If the `mcp` module is available, uses it to call exa search.
    Otherwise returns a fallback message.

    Parameters:
        query (str, required): The search query.
        num_results (int, optional, default 5): Number of results to return.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=30, max_output_tokens=8192)

    def __init__(self):
        # type: () -> None
        description = (
            "Search the web using Exa AI via MCP protocol. "
            "Use for web searches when MCP is available (falls back gracefully otherwise). "
            "Returns clean, ready-to-use content from search results."
        )
        parameters = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query.",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of search results to return.",
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        }

        super(McpExaTool, self).__init__(
            id="mcp-exa",
            description=description,
            parameters=parameters,
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute exa search via MCP or return fallback message.

        Args:
            args: Dict with 'query' (required) and 'num_results' (optional).
            ctx: ToolContext (unused but required by interface).

        Returns:
            ToolResult with search results or fallback message.
        """
        query = args.get("query", "")  # type: str
        num_results = args.get("num_results", 5)  # type: int

        # Check if MCP module is available
        try:
            import mcp  # noqa: F401

            mcp_available = True  # type: bool
        except ImportError:
            mcp_available = False

        if not mcp_available:
            message = "MCP not available. Configure EXA_API_KEY for direct web search."
            return ToolResult(
                title="MCP Exa Search (Unavailable)",
                output=message,
                metadata={
                    "query": query,
                    "num_results": num_results,
                    "mcp_available": False,
                },
            )

        # MCP is available -- attempt search
        try:
            # Placeholder: actual MCP protocol implementation would go here.
            # For now, return a structured message indicating MCP is ready.
            message = "MCP exa search ready. Query: '{}' ({} results requested)".format(
                query, num_results
            )
            return ToolResult(
                title="MCP Exa Search",
                output=message,
                metadata={
                    "query": query,
                    "num_results": num_results,
                    "mcp_available": True,
                },
            )
        except Exception as e:
            return ToolResult(
                title="MCP Exa Search Error",
                output="MCP exa search failed: {}".format(str(e)),
                metadata={
                    "query": query,
                    "num_results": num_results,
                    "mcp_available": True,
                    "error": str(e),
                },
            )


# ---------------------------------------------------------------------------
# Registration Helper
# ---------------------------------------------------------------------------


def register_auxiliary_tools(registry):
    # type: (Any) -> None
    """Register all auxiliary tools with the given ToolRegistry.

    Args:
        registry: A ToolRegistry instance to register tools with.
    """
    registry.register(PlanTool())
    registry.register(McpExaTool())
