"""Tests for SessionManager extended methods: title update, workspace association, project ID update."""

import pytest

from berserker.session.manager import SessionManager


class TestSessionExtensions:
    """Test extended session operations: title update, workspace filtering, etc."""

    def test_update_title_success(self, session_manager):
        """Should update title for existing session and persist it."""
        sid = session_manager.create(title="Original Title")
        result = session_manager.update_title(sid, "Updated Title")
        assert result is True
        
        # Verify the title was actually updated
        info = session_manager.load(sid)
        assert info is not None
        assert info["title"] == "Updated Title"

    def test_update_title_nonexistent(self, session_manager):
        """Should return False when updating title for non-existent session."""
        result = session_manager.update_title("nonexistent-session", "New Title")
        assert result is False

    def test_list_sessions_by_workspace(self, session_manager):
        """Should list sessions filtered by workspace_id."""
        # Create sessions with different workspace IDs
        sid1 = session_manager.create(title="Session 1")
        sid2 = session_manager.create(title="Session 2")
        sid3 = session_manager.create(title="Session 3")
        
        # Set workspace IDs
        session_manager.set_workspace(sid1, "workspace-1")
        session_manager.set_workspace(sid2, "workspace-1")
        session_manager.set_workspace(sid3, "workspace-2")
        
        # List sessions for workspace-1
        sessions = session_manager.list_sessions_by_workspace("workspace-1")
        assert len(sessions) == 2
        
        # Verify the sessions have correct workspace_id and contain expected titles
        workspace_ids = [s["workspace_id"] for s in sessions]
        titles = [s["title"] for s in sessions]
        assert all(wid == "workspace-1" for wid in workspace_ids)
        assert "Session 1" in titles
        assert "Session 2" in titles

    def test_set_workspace_success(self, session_manager):
        """Should set workspace_id for existing session and persist it."""
        sid = session_manager.create()
        result = session_manager.set_workspace(sid, "test-workspace")
        assert result is True
        
        # Verify the workspace_id was actually set
        sessions = session_manager.list_sessions_by_workspace("test-workspace")
        assert len(sessions) == 1
        assert sessions[0]["id"] == sid
        assert sessions[0]["workspace_id"] == "test-workspace"

    def test_set_workspace_nonexistent(self, session_manager):
        """Should return False when setting workspace for non-existent session."""
        result = session_manager.set_workspace("nonexistent-session", "test-workspace")
        assert result is False

    def test_update_project_id(self, session_manager):
        """Should update project_id based on directory hash."""
        import hashlib
        original_project_id = session_manager.project_id
        test_directory = "/path/to/test/workspace"
        
        # Calculate expected hash
        expected_hash = hashlib.sha256(test_directory.encode()).hexdigest()[:16]
        
        # Update project_id
        session_manager.update_project_id(test_directory)
        assert session_manager.project_id == expected_hash
        assert session_manager.project_id != original_project_id

    def test_list_sessions_by_workspace_empty(self, session_manager):
        """Should return empty list when filtering by workspace with no sessions."""
        sessions = session_manager.list_sessions_by_workspace("nonexistent-workspace")
        assert sessions == []