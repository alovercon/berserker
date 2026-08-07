"""
berserker.workspace.manager — Workspace persistence and directory-based workspace IDs.

Provides a WorkspaceManager class that handles creating, listing, and retrieving
workspaces from the database based on directory paths.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, List, Optional

from berserker.storage import get_db, insert


class WorkspaceManager:
    """Manages workspace persistence and directory-based workspace IDs."""

    def __init__(self):
        # type: () -> None
        """Initialize WorkspaceManager."""
        pass

    def get_or_create(self, directory):
        # type: (str) -> str
        """Get or create a workspace for the given directory.

        Args:
            directory: Directory path (relative or absolute).

        Returns:
            Workspace ID derived from the absolute directory path.
        """
        abs_path = os.path.abspath(directory)
        workspace_id = hashlib.sha256(abs_path.encode()).hexdigest()[:16]
        name = os.path.basename(abs_path)

        db = get_db()
        
        # Check if workspace already exists
        row = db.fetchone(
            "SELECT id FROM workspace WHERE id = ?", 
            (workspace_id,)
        )
        
        if row is None:
            # Create new workspace
            now = int(time.time())
            insert(
                db,
                "workspace",
                {
                    "id": workspace_id,
                    "name": name,
                    "directory": abs_path,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        
        return workspace_id

    def list_workspaces(self):
        # type: () -> List[Dict[str, Any]]
        """List all workspaces ordered by updated_at DESC.

        Returns:
            List of workspace dictionaries with id, name, directory, created_at, updated_at.
        """
        db = get_db()
        rows = db.fetchall(
            "SELECT id, name, directory, created_at, updated_at "
            "FROM workspace ORDER BY updated_at DESC"
        )
        
        workspaces = []
        for row in rows:
            workspaces.append({
                "id": row["id"],
                "name": row["name"],
                "directory": row["directory"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        
        return workspaces

    def get_by_id(self, workspace_id):
        # type: (str) -> Optional[Dict[str, Any]]
        """Get a workspace by its ID.

        Args:
            workspace_id: The workspace ID to look up.

        Returns:
            Workspace dictionary or None if not found.
        """
        db = get_db()
        row = db.fetchone(
            "SELECT id, name, directory, created_at, updated_at "
            "FROM workspace WHERE id = ?",
            (workspace_id,)
        )

        if row is None:
            return None

        return {
            "id": row["id"],
            "name": row["name"],
            "directory": row["directory"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_current_workspace(self):
        # type: () -> Optional[Dict[str, Any]]
        """Get the workspace for the current working directory.

        Returns:
            Workspace dictionary or None if not found (should not happen as
            get_or_create ensures it exists).
        """
        current_dir = os.getcwd()
        workspace_id = self.get_or_create(current_dir)
        
        db = get_db()
        row = db.fetchone(
            "SELECT id, name, directory, created_at, updated_at "
            "FROM workspace WHERE id = ?",
            (workspace_id,)
        )
        
        if row is None:
            return None
        
        return {
            "id": row["id"],
            "name": row["name"],
            "directory": row["directory"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }