"""
Command discovery and execution system for berserker.

This package provides the foundational data structures and parsing
utilities for the command system. Components are added incrementally:

- types:      CommandDef dataclass for command metadata.
- frontmatter: YAML frontmatter parser for Markdown-based command files.

Python 3.8.10 compatible.
"""

from __future__ import annotations

from berserker.command.builtin import register_builtin_commands
from berserker.command.frontmatter import parse_frontmatter
from berserker.command.processor import (
    CommandResult,
    is_special_command,
    lookup_command,
    parse_command,
)
from berserker.command.types import CommandDef

__all__ = [
    "CommandDef",
    "CommandResult",
    "is_special_command",
    "lookup_command",
    "parse_command",
    "parse_frontmatter",
    "register_builtin_commands",
]
