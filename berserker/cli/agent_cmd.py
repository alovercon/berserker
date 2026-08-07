"""
Agent CLI commands for berserker.

Provides:
- cmd_agent_list(): List all agents in a formatted table
- cmd_agent_show(name): Show detailed information for a specific agent
"""

from __future__ import annotations

import sys
from typing import List, Optional

from berserker.agent.manager import agent_manager


def _truncate_text(text, max_length=200):
    # type: (str, int) -> str
    """Truncate text to max_length characters with ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def _get_mode_priority(mode):
    # type: (str) -> int
    """Get priority for sorting modes: primary=0, subagent=1, hidden=2."""
    if mode == "primary":
        return 0
    elif mode == "subagent":
        return 1
    else:
        return 2


def cmd_agent_list():
    # type: () -> None
    """List all agents from agent manager.

    Output table format: NAME | MODE | MODEL | TOOLS | DESCRIPTION
    Grouped by mode (primary first, then subagent, then hidden).
    """
    agents = agent_manager.list()

    # Sort agents by mode priority, then by name
    sorted_agents = sorted(agents, key=lambda a: (_get_mode_priority(a.mode), a.name))

    # Print header
    print(
        "{:<15} | {:<10} | {:<15} | {:<20} | {}".format(
            "NAME", "MODE", "MODEL", "TOOLS", "DESCRIPTION"
        )
    )
    print("-" * 80)

    for agent in sorted_agents:
        tools_str = ",".join(agent.tools) if agent.tools else ""
        # Truncate tools string if too long
        if len(tools_str) > 20:
            tools_str = tools_str[:17] + "..."
        desc = agent.description if agent.description else ""
        print(
            "{:<15} | {:<10} | {:<15} | {:<20} | {}".format(
                agent.name, agent.mode, agent.model, tools_str, desc
            )
        )


def cmd_agent_show(name):
    # type: (str) -> None
    """Show detailed info for a specific agent.

    Output includes: name, mode, model, system_prompt (truncated to 200 chars),
    tools list, description.

    Args:
        name: The agent name to show details for.
    """
    try:
        agent = agent_manager.get(name)
    except KeyError as e:
        print("Error: {}".format(str(e)), file=sys.stderr)
        return

    print("Agent: {}".format(agent.name))
    print("Mode: {}".format(agent.mode))
    print("Model: {}".format(agent.model))
    print("System Prompt: {}".format(_truncate_text(agent.system_prompt, 200)))
    print("Tools: {}".format(", ".join(agent.tools) if agent.tools else "None"))
    print("Description: {}".format(agent.description if agent.description else "None"))
