"""
berserker.workspace — Global workspace directory management.

Provides a module-level workspace registry that captures the initial
working directory at startup and makes it available throughout the
application lifecycle.

Usage:
    from berserker.workspace import set_workspace, get_workspace

    # At startup (CLI entry point)
    set_workspace("/path/to/project")

    # Anywhere in the application
    workspace = get_workspace()  # Returns absolute path string

Also provides WorkspaceManager for persistent workspace management with IDs.

Python 3.8.10 compatible: uses type comments, no match/case.
"""

from __future__ import annotations

import os
from typing import Optional

from berserker.workspace.manager import WorkspaceManager

# Module-level workspace storage
_workspace = None  # type: Optional[str]

# Lazy-initialized workspace manager singleton
_workspace_manager = None  # type: Optional[WorkspaceManager]


def set_workspace(path):
    # type: (str) -> None
    """Set the global workspace directory.

    Converts relative paths to absolute paths. Should be called once
    at application startup before any tools or agents are initialized.

    Args:
        path: Workspace directory path (relative or absolute).
    """
    global _workspace
    _workspace = os.path.abspath(path)


def get_workspace():
    # type: () -> str
    """Get the current workspace directory.

    Returns:
        Absolute path to the workspace directory. If no workspace has
        been explicitly set, falls back to os.getcwd().
    """
    if _workspace is not None:
        return _workspace
    return os.getcwd()


def get_workspace_id():
    # type: () -> str
    """Get the persistent workspace ID for the current workspace.

    Uses WorkspaceManager to get or create a workspace entry in the database
    based on the current workspace directory.

    Returns:
        Workspace ID (16-character hex string derived from directory path hash).
    """
    global _workspace_manager
    if _workspace_manager is None:
        _workspace_manager = WorkspaceManager()
    
    workspace_dir = get_workspace()
    return _workspace_manager.get_or_create(workspace_dir)


__all__ = [
    "WorkspaceManager",
    "set_workspace",
    "get_workspace",
    "get_workspace_id",
]