"""
Command discovery engine that scans Markdown files from user-level
and project-level directories to build command definitions.

Python 3.8.10 compatible.
"""

from __future__ import annotations

import logging
import os

from berserker.command.frontmatter import parse_frontmatter
from berserker.command.types import CommandDef
from berserker.paths.resolver import get_config_dir

logger = logging.getLogger(__name__)

# Directories to exclude during recursive scanning
EXCLUDED_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "dist",
        "build",
        ".next",
        ".omo",
        ".sisyphus",
        ".turbo",
        "coverage",
        "out",
        ".cache",
        "__pycache__",
        ".vscode-test",
        "target",
    }
)

# Project-level command paths to scan (relative to workspace root)
PROJECT_COMMAND_PATHS = [
    (".berserker", "commands"),
    (".berserker", "command"),
]


def _should_skip_dir(dirname):
    # type: (str) -> bool
    """Check if a directory should be skipped during recursive scanning.

    Skips hidden directories (dot-prefixed), the excluded dirs set,
    and the special entries '.' and '..'.
    """
    return dirname == "." or dirname == ".." or dirname.startswith(".") or dirname in EXCLUDED_DIRS


def discover_commands_from_dir(commands_dir, scope):
    # type: (str, str) -> List[CommandDef]
    """Recursively scan a directory for Markdown command files.

    Walks the given directory tree (respecting exclusion rules),
    parses frontmatter from each ``.md`` file, and builds a
    :class:`CommandDef` for each one.

    Args:
        commands_dir: Absolute path to the directory to scan.
        scope: Scope string to attach to each discovered command
            (e.g. ``"user"``, ``"project"``).

    Returns:
        List of discovered :class:`CommandDef` instances. Returns an
        empty list if *commands_dir* does not exist or contains no
        valid command files.
    """
    if not os.path.isdir(commands_dir):
        logger.debug("Commands directory does not exist: %s", commands_dir)
        return []

    commands = []  # type: List[CommandDef]
    # Track resolved real paths to prevent infinite loops from symlinks
    visited = set()  # type: set[str]

    # Use os.walk for portable recursive directory traversal
    for dirpath, dirnames, filenames in os.walk(commands_dir, topdown=True):
        # Prune excluded directories in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        # Resolve real path — skip if we've already visited this dir
        real_dirpath = os.path.realpath(dirpath)
        if real_dirpath in visited:
            # Prevent symlink loops; also prune children from traversal
            dirnames[:] = []
            continue
        visited.add(real_dirpath)

        # Compute the relative subdir path (empty string for root)
        rel_dir = os.path.relpath(dirpath, commands_dir)
        if rel_dir == ".":
            rel_dir = ""

        for filename in filenames:
            if not filename.endswith(".md"):
                continue

            filepath = os.path.join(dirpath, filename)

            # Build the command name
            stem = filename[:-3]  # strip ".md"
            name = rel_dir.replace(os.sep, "/") + "/" + stem if rel_dir else stem

            # Read and parse the file
            try:
                with open(filepath, encoding="utf-8") as fh:
                    content = fh.read()
            except OSError as exc:
                logger.warning("Failed to read command file %s: %s", filepath, exc)
                continue

            frontmatter, body = parse_frontmatter(content)

            # Handle bad frontmatter: parse_frontmatter returns {} on error
            # and logs its own warning.  We just use defaults.
            description = str(frontmatter.get("description", "")) if frontmatter else ""

            # Template is the body content after frontmatter (with $ARGUMENTS placeholder)
            template = frontmatter.get("template", "")
            if not template and body:
                template = body.strip()

            cmd = CommandDef(
                name=name,
                description=description,
                argument_hint=str(frontmatter.get("argument_hint", "")),
                template=template,
                scope=scope,
            )
            commands.append(cmd)

    return commands


def discover_user_commands():
    # type: () -> List[CommandDef]
    """Discover user-level commands from the config directory.

    Looks for a ``commands/`` subdirectory under the user configuration
    directory returned by :func:`get_config_dir`.

    Returns:
        List of user-scoped :class:`CommandDef` instances.
    """
    config_dir = get_config_dir()
    user_commands_dir = os.path.join(config_dir, "commands")
    return discover_commands_from_dir(user_commands_dir, "user")


def discover_project_commands(workspace):
    # type: (str) -> List[CommandDef]
    """Discover project-level commands from project-specific directories.

    Scans the predefined ``PROJECT_COMMAND_PATHS`` under the given
    *workspace* root.

    Args:
        workspace: Absolute path to the project workspace root.

    Returns:
        List of project-scoped :class:`CommandDef` instances.
    """
    commands = []  # type: List[CommandDef]
    for parts in PROJECT_COMMAND_PATHS:
        proj_dir = os.path.join(workspace, *parts)
        commands.extend(discover_commands_from_dir(proj_dir, "project"))
    return commands


def discover_all_commands(workspace):
    # type: (str) -> Dict[str, CommandDef]
    """Discover all commands from user and project sources and merge them.

    User commands are discovered first, then project commands are
    layered on top.  When both define a command with the same name,
    the **project** definition takes priority.

    Args:
        workspace: Absolute path to the project workspace root.

    Returns:
        A dictionary mapping command names to their
        :class:`CommandDef` instances.
    """
    result = {}  # type: Dict[str, CommandDef]

    # User commands — lower priority
    for cmd in discover_user_commands():
        result[cmd.name] = cmd

    # Project commands — higher priority (override user)
    for cmd in discover_project_commands(workspace):
        result[cmd.name] = cmd

    return result
