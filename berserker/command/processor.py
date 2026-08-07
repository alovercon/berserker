"""Shared command dispatch for CLI and GUI.

Provides parsing, lookup, and result types to eliminate duplicated
if/elif chains between CLI and GUI command processors.

Python 3.8.10 compatible.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from berserker.command.registry import command_registry

logger = logging.getLogger(__name__)


class CommandResult(object):
    """Structured result from command processing.

    Constants represent action types that dispatch code can switch on.
    """

    # Action constants
    EXIT = "exit"  # Exit conversation
    SHOW_TEXT = "show"  # Show text result (help, skills, etc.)
    SWITCH_SESSION = "switch"  # Switch to different session
    CLEAR = "clear"  # Clear conversation
    TRIGGER_INIT = "init"  # Trigger AGENTS.md generation
    START_WORK = "work"  # Start work plan

    def __init__(self, action, text="", new_session_id=None, arg=""):
        # type: (str, str, Optional[str], str) -> None
        self.action = action
        self.text = text
        self.new_session_id = new_session_id
        self.arg = arg


def parse_command(text):
    # type: (str) -> Tuple[str, str, str]
    """Parse a slash command into (command_name, arg, raw).

    Args:
        text: User input text.

    Returns:
        (command_lower, arg_string, raw_command) or ("", "", "") if not a command.
    """
    if not text.startswith("/"):
        return ("", "", "")
    parts = text.split(None, 1)
    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    return (command, arg, command)


def lookup_command(command_name):
    # type: (str) -> Optional[Any]
    """Look up a command in the registry.

    Args:
        command_name: Command name with or without leading slash
                      (e.g. "/help" or "help").

    Returns:
        CommandDef from registry, or None.
    """
    return command_registry.get(command_name.lstrip("/"))


def is_special_command(command_name):
    # type: (str) -> Optional[str]
    """Check if command is a special builtin (handled differently).

    These commands bypass the normal registry lookup because they
    have side effects (exit, start-work) that need special handling
    in both CLI and GUI.

    Args:
        command_name: Full command with leading slash (e.g. "/exit").

    Returns:
        Action string for special commands, None otherwise.
    """
    if command_name in ("/exit", "/quit"):
        return CommandResult.EXIT
    if command_name == "/start-work":
        return CommandResult.START_WORK
    return None
