"""Tests for WorkspaceManager: workspace persistence and directory-based IDs."""

import os
import pytest
import tempfile
import shutil

from berserker.workspace.manager import WorkspaceManager


class TestWorkspaceManager:
    """Test WorkspaceManager functionality."""

    @pytest.fixture
    def temp_dir1(self):
        # type: () -> str
        """Create a temporary directory."""
        d = tempfile.mkdtemp(prefix="workspace-test-1-")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    @pytest.fixture
    def temp_dir2(self):
        # type: () -> str
        """Create another temporary directory."""
        d = tempfile.mkdtemp(prefix="workspace-test-2-")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    @pytest.fixture
    def workspace_manager(self, isolated_db):
        # type: (str) -> WorkspaceManager
        """Create a WorkspaceManager with isolated database."""
        return WorkspaceManager()

    def test_get_or_create_same_directory(self, workspace_manager, temp_dir1):
        """Calling get_or_create twice with same path should return same ID."""
        id1 = workspace_manager.get_or_create(temp_dir1)
        id2 = workspace_manager.get_or_create(temp_dir1)
        assert id1 == id2
        assert len(id1) == 16  # SHA256 hex digest truncated to 16 chars

    def test_get_or_create_different_directories(self, workspace_manager, temp_dir1, temp_dir2):
        """Different directories should produce different workspace IDs."""
        id1 = workspace_manager.get_or_create(temp_dir1)
        id2 = workspace_manager.get_or_create(temp_dir2)
        assert id1 != id2

    def test_list_workspaces(self, workspace_manager, temp_dir1, temp_dir2):
        """Should list all created workspaces."""
        # Create 3 workspaces
        workspace_manager.get_or_create(temp_dir1)
        workspace_manager.get_or_create(temp_dir2)
        workspace_manager.get_or_create(os.getcwd())  # Current directory
        
        workspaces = workspace_manager.list_workspaces()
        assert len(workspaces) >= 3
        
        # Verify each workspace has expected fields
        for ws in workspaces:
            assert "id" in ws
            assert "name" in ws
            assert "directory" in ws
            assert "created_at" in ws
            assert "updated_at" in ws
            assert isinstance(ws["id"], str)
            assert isinstance(ws["name"], str)
            assert isinstance(ws["directory"], str)

    def test_get_current_workspace(self, workspace_manager):
        """Should return workspace for current working directory."""
        current_ws = workspace_manager.get_current_workspace()
        assert current_ws is not None
        assert current_ws["directory"] == os.getcwd()
        assert current_ws["name"] == os.path.basename(os.getcwd())

    def test_workspace_id_is_deterministic(self, temp_dir1):
        """Same path across different WorkspaceManager instances should produce same ID."""
        # Create first manager and get ID
        manager1 = WorkspaceManager()
        id1 = manager1.get_or_create(temp_dir1)
        
        # Create second manager and get ID for same path
        manager2 = WorkspaceManager()
        id2 = manager2.get_or_create(temp_dir1)
        
        assert id1 == id2
        
        # Verify it's actually based on SHA256 hash
        import hashlib
        expected_id = hashlib.sha256(os.path.abspath(temp_dir1).encode()).hexdigest()[:16]
        assert id1 == expected_id

    def test_workspace_name_from_basename(self, workspace_manager, temp_dir1):
        """Workspace name should be the basename of the directory."""
        workspace_id = workspace_manager.get_or_create(temp_dir1)
        workspaces = workspace_manager.list_workspaces()
        
        # Find our workspace
        our_workspace = None
        for ws in workspaces:
            if ws["id"] == workspace_id:
                our_workspace = ws
                break
        
        assert our_workspace is not None
        assert our_workspace["name"] == os.path.basename(temp_dir1)
        assert our_workspace["directory"] == os.path.abspath(temp_dir1)