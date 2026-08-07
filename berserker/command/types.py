"""
Core data types for the command system.

Provides the CommandDef dataclass which represents a single command definition
with its metadata and scope.

Python 3.8.10 compatible: uses type comments, typing module imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class CommandDef(object):
    """Definition of a single command in berserker.

    Attributes:
        name: Command name (e.g., "help", "my-command").
        description: Human-readable description of what the command does.
        argument_hint: Usage hint shown in help text (e.g., "[command]").
        template: Prompt template string, may contain $ARGUMENTS placeholders.
        scope: Command visibility scope: "builtin", "user", "project", or "plugin".
        handler: Optional Python callable (used only for plugin scope commands).
    """

    name: str
    description: str = ""
    argument_hint: str = ""
    template: str = ""
    scope: str = "builtin"  # "builtin" | "user" | "project" | "plugin"
    # Plugin Python handler (only for plugin scope)
    handler = None  # type: Optional[Callable]


# List of valid scope values
SCOPES = frozenset(
    {
        "builtin",
        "user",
        "project",
        "plugin",
    }
)
