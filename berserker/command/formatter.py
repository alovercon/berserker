"""Command template formatting for LLM injection.

Provides template resolution with parameter validation and substitution.
Custom commands like create-file.md use \$1, \$2, \$3 for positional args
and \$ARGUMENTS for the full argument string.

Python 3.8.10 compatible.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple


def count_required_args(template):
    # type: (str) -> int
    """Count the number of required positional arguments in a template.

    Scans for \$1, \$2, \$3... patterns and returns the highest index.

    Args:
        template: The command template string.

    Returns:
        The highest positional argument index found, or 0 if none.
    """
    matches = re.findall(r"\$(\d+)", template)
    if not matches:
        return 0
    return max(int(m) for m in matches)


def validate_command_args(cmd, args):
    # type: (object, str) -> Tuple[bool, Optional[str]]
    """Validate that the command has enough arguments.

    Args:
        cmd: CommandDef object with template field.
        args: User-provided arguments string.

    Returns:
        (is_valid, error_message): If invalid, error_message describes the issue.
    """
    needed = count_required_args(cmd.template)
    arg_count = len(args.split()) if args else 0
    if arg_count < needed:
        return (
            False,
            "Error: Command '/{}' requires {} argument(s), but got {}.".format(
                cmd.name, needed, arg_count
            ),
        )
    return (True, None)


def resolve_command_template(cmd, args=""):
    # type: (object, str) -> str
    """Resolve a command template with positional and full argument substitution.

    Substitutes \$1, \$2, \$3... with positional arguments,
    and \$ARGUMENTS with the full argument string.

    Returns only the template body — suitable for direct LLM injection.

    Args:
        cmd: CommandDef object with template field.
        args: User-provided arguments string.

    Returns:
        Template body with arguments substituted.
    """
    resolved = cmd.template
    if args:
        parts = args.split()
        # Positional substitution: $1, $2, $3, ...
        for i, part in enumerate(parts, 1):
            resolved = resolved.replace("${}".format(i), part)
        # Full arguments substitution
        resolved = resolved.replace("$ARGUMENTS", args)
    return resolved.strip()


def format_command_template(cmd, args=""):
    # type: (object, str) -> str
    """Format a command template with metadata headers for the LLM.

    Legacy function — prefer resolve_command_template() for direct injection.
    """
    sections = []  # type: List[str]
    sections.append(f"# /{cmd.name} Command\n")
    if cmd.description:
        sections.append(f"**Description**: {cmd.description}\n")
    if args:
        sections.append(f"**User Arguments**: {args}\n")
    sections.append(f"**Scope**: {cmd.scope}\n")
    sections.append("---\n")
    sections.append("## Command Instructions\n")
    sections.append(resolve_command_template(cmd, args))
    if args:
        sections.append("\n\n---\n")
        sections.append("## User Request\n")
        sections.append(args)
    return "\n".join(sections)
