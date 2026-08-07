"""Shared tool registration for berserker.

Registers all default tools. Called by CLI and GUI startup.
Python 3.8.10 compatible.
"""

from __future__ import annotations

import logging

from berserker.tool.auxiliary import register_auxiliary_tools
from berserker.tool.bash import register_bash_tool
from berserker.tool.edit_complex import register_edit_tools
from berserker.tool.file_ops import register_file_ops_tools
from berserker.tool.file_simple import register_file_simple_tools
from berserker.tool.git import GitTool
from berserker.tool.lsp import register_lsp_tool
from berserker.tool.search import register_search_tools
from berserker.tool.selection import SelectionTool
from berserker.tool.skill import register_skill_tool
from berserker.tool.task import register_task_tool
from berserker.tool.todo import TodoTool

logger = logging.getLogger(__name__)


def register_default_tools(tool_registry):
    # type: (object) -> None
    """Register all default tools used by agents.

    This is the single shared function called by both CLI and GUI startup
    to ensure identical tool registration across all entry points.

    Args:
        tool_registry: A ToolRegistry instance to register tools into.
    """
    register_file_simple_tools(tool_registry)
    register_file_ops_tools(tool_registry)
    register_bash_tool(tool_registry)
    register_edit_tools(tool_registry)
    register_search_tools(tool_registry)
    register_lsp_tool(tool_registry)
    register_auxiliary_tools(tool_registry)
    register_skill_tool(tool_registry)
    tool_registry.register(GitTool())
    tool_registry.register(TodoTool())
    tool_registry.register(SelectionTool())
    register_task_tool(tool_registry)
