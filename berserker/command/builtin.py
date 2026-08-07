"""
Built-in command definitions for berserker.

Registers all core slash commands (help, clear, agent, model, session,
compact, init, skills, agents, start-work) into the CommandRegistry
with scope="builtin".

Commands like /exit and /quit are intentionally excluded because they
bypass normal command dispatch (they return "__exit__" signal that
breaks the CLI main loop) and are handled directly in the CLI's
_process_slash_command().

Python 3.8.10 compatible.
"""

from __future__ import annotations

from berserker.command.types import CommandDef


def register_builtin_commands(registry):
    # type: (object) -> None
    """Register all built-in slash commands."""

    builtins = [
        CommandDef(
            name="help",
            description="Show available commands",
            argument_hint="",
            scope="builtin",
        ),
        CommandDef(
            name="clear",
            description="Clear conversation history (start new session)",
            argument_hint="",
            scope="builtin",
        ),
        CommandDef(
            name="agent",
            description="Switch agent or list available agents",
            argument_hint="[name]",
            scope="builtin",
        ),
        CommandDef(
            name="model",
            description="Switch model (e.g., /model openai/gpt-4o)",
            argument_hint="<provider/model>",
            scope="builtin",
        ),
        CommandDef(
            name="session",
            description="Switch to session by ID, or list sessions",
            argument_hint="<id>",
            scope="builtin",
        ),
        CommandDef(
            name="compact",
            description="Trigger context compression",
            argument_hint="",
            scope="builtin",
        ),
        CommandDef(
            name="init",
            description="Generate AGENTS.md for this workspace",
            argument_hint="[focus]",
            scope="builtin",
        ),
        CommandDef(
            name="skills",
            description="List available skills",
            argument_hint="",
            scope="builtin",
        ),
        CommandDef(
            name="agents",
            description="Show real-time agent status",
            scope="builtin",
        ),
        CommandDef(
            name="start-work",
            description="Execute a plan via executor agent",
            argument_hint="[plan-name]",
            scope="builtin",
        ),
    ]

    for cmd in builtins:
        registry.register(cmd)
