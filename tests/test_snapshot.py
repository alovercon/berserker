"""Tests for SnapshotTracker class in berserker.session.snapshot."""

import os
import pytest
import subprocess
import tempfile
import shutil
from unittest.mock import patch

from berserker.session.snapshot import (
    SnapshotTracker,
    _run_git,
    _get_repo_root,
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def has_git():
    # type: () -> bool
    """Check if git is available in PATH."""
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Skip git-dependent tests if git is not available
requires_git = pytest.mark.skipif(not has_git(), reason="git not available")


# ---------------------------------------------------------------------------
# _run_git() Tests
# ---------------------------------------------------------------------------


class TestRunGit:
    """Test the _run_git helper function."""

    def test_run_git_timeout(self):
        """Should handle timeout scenario gracefully."""
        # Mock subprocess.run to raise TimeoutExpired
        with patch("berserker.session.snapshot.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
            code, out, err = _run_git(["status"], timeout=1)
            assert code == 1
            assert out == ""
            assert "timed out" in err

    def test_run_git_not_found(self):
        """Should handle git not found scenario."""
        # Mock subprocess.run to raise FileNotFoundError
        with patch("berserker.session.snapshot.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            code, out, err = _run_git(["status"])
            assert code == 1
            assert out == ""
            assert "Git not found" in err

    @requires_git
    def test_run_git_valid_command(self):
        """Should execute valid git commands successfully."""
        code, out, err = _run_git(["--version"])
        assert code == 0
        assert "git version" in out
        assert err == ""


# ---------------------------------------------------------------------------
# _get_repo_root() Tests
# ---------------------------------------------------------------------------


class TestGetRepoRoot:
    """Test the _get_repo_root helper function."""

    @requires_git
    def test_get_repo_root_in_git_repo(self):
        """Should return repo root when in a git repository."""
        root = _get_repo_root()
        assert root is not None
        assert os.path.isdir(root)
        # Should contain .git directory
        assert os.path.isdir(os.path.join(root, ".git"))

    def test_get_repo_root_invalid_path(self):
        """Should return None for invalid/non-git paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a non-git directory
            non_git_dir = os.path.join(tmpdir, "not-a-repo")
            os.makedirs(non_git_dir)
            root = _get_repo_root(non_git_dir)
            assert root is None


# ---------------------------------------------------------------------------
# SnapshotTracker.__init__() and repo_root Tests
# ---------------------------------------------------------------------------


class TestSnapshotTrackerInit:
    """Test SnapshotTracker initialization and repo_root property."""

    def test_init_default(self):
        """Default initialization should resolve repo_root to current repo."""
        tracker = SnapshotTracker()
        # Since we're in a git repo, repo_root should be set
        if has_git():
            assert tracker.repo_root is not None
            assert os.path.isdir(tracker.repo_root)
        else:
            assert tracker.repo_root is None

    def test_init_with_invalid_path(self):
        """With invalid path, repo_root should be None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            non_git_dir = os.path.join(tmpdir, "not-a-repo")
            os.makedirs(non_git_dir)
            tracker = SnapshotTracker(repo_path=non_git_dir)
            assert tracker.repo_root is None

    def test_init_with_none_repo_path(self):
        """With repo_path=None, should use current working directory."""
        tracker = SnapshotTracker(repo_path=None)
        if has_git():
            assert tracker.repo_root is not None
        else:
            assert tracker.repo_root is None

    def test_repo_root_cached(self):
        """repo_root property should be cached after first access."""
        tracker = SnapshotTracker()
        # Access repo_root multiple times
        root1 = tracker.repo_root
        root2 = tracker.repo_root
        assert root1 is root2  # Same object (cached)


# ---------------------------------------------------------------------------
# create_snapshot() Tests
# ---------------------------------------------------------------------------


class TestCreateSnapshot:
    """Test SnapshotTracker.create_snapshot() method."""

    def test_create_snapshot_returns_dict_with_required_keys(self):
        """Should return dict with snapshot_id, hash, session_id, created_at."""
        tracker = SnapshotTracker()
        snapshot = tracker.create_snapshot(session_id="test-session")

        assert isinstance(snapshot, dict)
        assert "snapshot_id" in snapshot
        assert "hash" in snapshot
        assert "session_id" in snapshot
        assert "created_at" in snapshot

    def test_create_snapshot_snapshot_id_format(self):
        """snapshot_id should be a non-empty string (12 hex chars)."""
        tracker = SnapshotTracker()
        snapshot = tracker.create_snapshot()

        snapshot_id = snapshot["snapshot_id"]
        assert isinstance(snapshot_id, str)
        assert len(snapshot_id) == 12
        # Should be hexadecimal characters
        assert all(c in "0123456789abcdef" for c in snapshot_id)

    def test_create_snapshot_created_at_is_timestamp(self):
        """created_at should be a positive integer (Unix timestamp)."""
        import time

        tracker = SnapshotTracker()
        snapshot = tracker.create_snapshot()

        created_at = snapshot["created_at"]
        assert isinstance(created_at, int)
        assert created_at > 0
        # Should be within reasonable time range (not too far in past/future)
        current_time = int(time.time())
        assert abs(created_at - current_time) < 60  # Within 1 minute

    @requires_git
    def test_create_snapshot_in_git_repo_has_hash(self):
        """When in git repo, should have a hash value."""
        tracker = SnapshotTracker()
        snapshot = tracker.create_snapshot()

        # In a git repo, hash should be either a commit hash or None if no changes
        # But it should not raise an exception
        assert snapshot["hash"] is None or isinstance(snapshot["hash"], str)

    def test_create_snapshot_not_in_git_repo_hash_none(self):
        """When NOT in a git repo, hash should be None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            non_git_dir = os.path.join(tmpdir, "not-a-repo")
            os.makedirs(non_git_dir)
            tracker = SnapshotTracker(repo_path=non_git_dir)
            snapshot = tracker.create_snapshot()
            assert snapshot["hash"] is None

    def test_create_snapshot_session_id_preserved(self):
        """session_id should be preserved in the returned snapshot."""
        tracker = SnapshotTracker()
        test_session_id = "abc123-def456"
        snapshot = tracker.create_snapshot(session_id=test_session_id)
        assert snapshot["session_id"] == test_session_id


# ---------------------------------------------------------------------------
# get_diff_stats() Tests
# ---------------------------------------------------------------------------


class TestGetDiffStats:
    """Test SnapshotTracker.get_diff_stats() method."""

    def test_get_diff_stats_empty_string(self):
        """Empty string should return zero counts."""
        tracker = SnapshotTracker()
        stats = tracker.get_diff_stats("")
        expected = {"additions": 0, "deletions": 0, "files": 0}
        assert stats == expected

    def test_get_diff_stats_real_diff_with_counts(self):
        """Real git diff should return correct counts."""
        tracker = SnapshotTracker()
        # Simulate a real git diff output
        diff_text = """diff --git a/file1.txt b/file1.txt
index abc123..def456 100644
--- a/file1.txt
+++ b/file1.txt
@@ -1,3 +1,4 @@
 line1
-line2
+line2_modified
+line3_new
diff --git a/file2.txt b/file2.txt
index xyz789..uvw012 100644
--- a/file2.txt
+++ b/file2.txt
@@ -1 +1 @@
-old content
+new content
"""
        stats = tracker.get_diff_stats(diff_text)
        # Should count: 3 additions (+line2_modified, +line3_new, +new content)
        # Should count: 2 deletions (-line2, -old content)
        # Should count: 2 files (2 diff --git lines)
        expected = {"additions": 3, "deletions": 2, "files": 2}
        assert stats == expected

    def test_get_diff_stats_plus_plus_plus_not_counted(self):
        """Lines starting with '+++' should NOT count as additions."""
        tracker = SnapshotTracker()
        diff_text = """diff --git a/test.txt b/test.txt
+++ b/test.txt
+actual_addition
"""
        stats = tracker.get_diff_stats(diff_text)
        # Only +actual_addition should count, not +++ b/test.txt
        expected = {"additions": 1, "deletions": 0, "files": 1}
        assert stats == expected

    def test_get_diff_stats_minus_minus_minus_not_counted(self):
        """Lines starting with '---' should NOT count as deletions."""
        tracker = SnapshotTracker()
        diff_text = """diff --git a/test.txt b/test.txt
--- a/test.txt
-actual_deletion
"""
        stats = tracker.get_diff_stats(diff_text)
        # Only -actual_deletion should count, not --- a/test.txt
        expected = {"additions": 0, "deletions": 1, "files": 1}
        assert stats == expected

    def test_get_diff_stats_multiple_files(self):
        """Multiple 'diff --git' lines should result in correct file count."""
        tracker = SnapshotTracker()
        diff_text = """diff --git a/file1.txt b/file1.txt
+add1
diff --git a/file2.txt b/file2.txt
+add2
diff --git a/file3.txt b/file3.txt
+add3
"""
        stats = tracker.get_diff_stats(diff_text)
        expected = {"additions": 3, "deletions": 0, "files": 3}
        assert stats == expected

    def test_get_diff_stats_no_diff_lines(self):
        """Diff with no actual changes should return zero additions/deletions."""
        tracker = SnapshotTracker()
        diff_text = """diff --git a/file.txt b/file.txt
index old..new 100644
--- a/file.txt
+++ b/file.txt
"""
        stats = tracker.get_diff_stats(diff_text)
        expected = {"additions": 0, "deletions": 0, "files": 1}
        assert stats == expected


# ---------------------------------------------------------------------------
# diff_snapshots() Tests
# ---------------------------------------------------------------------------


class TestDiffSnapshots:
    """Test SnapshotTracker.diff_snapshots() method."""

    def test_diff_snapshots_both_hashes_none(self):
        """Both hashes None should return empty string."""
        tracker = SnapshotTracker()
        diff = tracker.diff_snapshots(None, None)
        assert diff == ""

    def test_diff_snapshots_one_hash_none(self):
        """One hash None should return empty string."""
        tracker = SnapshotTracker()
        diff = tracker.diff_snapshots("valid_hash", None)
        assert diff == ""
        diff = tracker.diff_snapshots(None, "valid_hash")
        assert diff == ""

    def test_diff_snapshots_not_in_git_repo(self):
        """When not in git repo, should return empty string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            non_git_dir = os.path.join(tmpdir, "not-a-repo")
            os.makedirs(non_git_dir)
            tracker = SnapshotTracker(repo_path=non_git_dir)
            diff = tracker.diff_snapshots("hash1", "hash2")
            assert diff == ""

    @requires_git
    def test_diff_snapshots_valid_hashes(self):
        """With valid hashes in git repo, should attempt diff."""
        tracker = SnapshotTracker()
        # Even if hashes don't exist, should return empty string rather than crash
        diff = tracker.diff_snapshots("nonexistent_hash1", "nonexistent_hash2")
        # Should return empty string since the hashes don't exist
        assert diff == ""


# ---------------------------------------------------------------------------
# save_snapshot() and get_session_snapshots() Tests
# ---------------------------------------------------------------------------


class TestSaveAndGetSnapshots:
    """Test SnapshotTracker.save_snapshot() and get_session_snapshots() methods."""

    def test_save_and_get_snapshot_with_isolated_db(self, session_manager):
        """Save a snapshot, then retrieve it — data should match."""
        tracker = SnapshotTracker()
        session_id = session_manager.create()

        # Create a snapshot
        snapshot = {
            "snapshot_id": "snap123456789",
            "hash": "abc123def456",
            "session_id": session_id,
            "created_at": 1234567890,
        }

        # Save it with stats
        tracker.save_snapshot(
            snapshot,
            additions=5,
            deletions=3,
            files=2,
            diff_summary="Modified two files",
        )

        # Retrieve it
        snapshots = tracker.get_session_snapshots(session_id)
        assert len(snapshots) == 1

        retrieved = snapshots[0]
        assert retrieved["id"] == "snap123456789"
        assert retrieved["session_id"] == session_id
        assert retrieved["snapshot_hash"] == "abc123def456"
        assert retrieved["additions"] == 5
        assert retrieved["deletions"] == 3
        assert retrieved["files"] == 2
        assert retrieved["diff_summary"] == "Modified two files"
        assert retrieved["created_at"] == 1234567890

    def test_get_multiple_snapshots_same_session(self, session_manager):
        """Multiple snapshots for same session should all be returned in order."""
        tracker = SnapshotTracker()
        session_id = session_manager.create()

        # Create and save three snapshots with different timestamps
        snapshots_data = [
            {
                "snapshot_id": "snap1",
                "hash": "hash1",
                "session_id": session_id,
                "created_at": 1000,
            },
            {
                "snapshot_id": "snap2",
                "hash": "hash2",
                "session_id": session_id,
                "created_at": 2000,
            },
            {
                "snapshot_id": "snap3",
                "hash": "hash3",
                "session_id": session_id,
                "created_at": 3000,
            },
        ]

        # Save them in reverse order to test sorting
        for snap_data in reversed(snapshots_data):
            tracker.save_snapshot(snap_data)

        # Retrieve them
        snapshots = tracker.get_session_snapshots(session_id)
        assert len(snapshots) == 3

        # Should be ordered by created_at ASC
        assert snapshots[0]["id"] == "snap1"
        assert snapshots[1]["id"] == "snap2"
        assert snapshots[2]["id"] == "snap3"
        assert snapshots[0]["created_at"] == 1000
        assert snapshots[1]["created_at"] == 2000
        assert snapshots[2]["created_at"] == 3000

    def test_get_snapshots_different_sessions(self, session_manager):
        """Snapshots from different sessions should not mix."""
        tracker = SnapshotTracker()
        session1 = session_manager.create()
        session2 = session_manager.create()

        # Save snapshot for session1
        snap1 = {
            "snapshot_id": "snap_a1",
            "hash": "hash_a1",
            "session_id": session1,
            "created_at": 1000,
        }
        tracker.save_snapshot(snap1)

        # Save snapshot for session2
        snap2 = {
            "snapshot_id": "snap_b1",
            "hash": "hash_b1",
            "session_id": session2,
            "created_at": 2000,
        }
        tracker.save_snapshot(snap2)

        # Retrieve only session1 snapshots
        session1_snaps = tracker.get_session_snapshots(session1)
        assert len(session1_snaps) == 1
        assert session1_snaps[0]["id"] == "snap_a1"

        # Retrieve only session2 snapshots
        session2_snaps = tracker.get_session_snapshots(session2)
        assert len(session2_snaps) == 1
        assert session2_snaps[0]["id"] == "snap_b1"


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestSnapshotTrackerIntegration:
    """Integration tests combining multiple methods."""

    @requires_git
    def test_full_workflow_in_git_repo(self, session_manager):
        """Test complete workflow: create, diff, stats, save, retrieve."""
        tracker = SnapshotTracker()
        session_id = session_manager.create()

        # Create first snapshot
        snap1 = tracker.create_snapshot(session_id=session_id)
        assert "snapshot_id" in snap1
        assert "created_at" in snap1

        # Create second snapshot
        snap2 = tracker.create_snapshot(session_id=session_id)
        assert "snapshot_id" in snap2
        assert "created_at" in snap2
        assert snap2["created_at"] >= snap1["created_at"]

        # Get diff between snapshots
        diff = tracker.diff_snapshots(snap1["hash"], snap2["hash"])
        # Diff might be empty if no changes, but shouldn't crash
        assert isinstance(diff, str)

        # Get stats from diff
        stats = tracker.get_diff_stats(diff)
        assert "additions" in stats
        assert "deletions" in stats
        assert "files" in stats
        assert isinstance(stats["additions"], int)
        assert isinstance(stats["deletions"], int)
        assert isinstance(stats["files"], int)

        # Save second snapshot with stats
        tracker.save_snapshot(
            snap2,
            additions=stats["additions"],
            deletions=stats["deletions"],
            files=stats["files"],
        )

        # Retrieve saved snapshot
        saved_snaps = tracker.get_session_snapshots(session_id)
        assert len(saved_snaps) == 1
        saved = saved_snaps[0]
        assert saved["id"] == snap2["snapshot_id"]
        assert saved["additions"] == stats["additions"]
        assert saved["deletions"] == stats["deletions"]
        assert saved["files"] == stats["files"]

    def test_workflow_without_git_repo(self, session_manager):
        """Test workflow when not in a git repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            non_git_dir = os.path.join(tmpdir, "not-a-repo")
            os.makedirs(non_git_dir)
            tracker = SnapshotTracker(repo_path=non_git_dir)
            session_id = session_manager.create()

            # Create snapshots (hashes will be None)
            snap1 = tracker.create_snapshot(session_id=session_id)
            snap2 = tracker.create_snapshot(session_id=session_id)

            assert snap1["hash"] is None
            assert snap2["hash"] is None

            # Diff should be empty
            diff = tracker.diff_snapshots(snap1["hash"], snap2["hash"])
            assert diff == ""

            # Stats should be zero
            stats = tracker.get_diff_stats(diff)
            assert stats == {"additions": 0, "deletions": 0, "files": 0}

            # Save and retrieve should work
            tracker.save_snapshot(snap2, **stats)
            saved_snaps = tracker.get_session_snapshots(session_id)
            assert len(saved_snaps) == 1
            assert saved_snaps[0]["snapshot_hash"] == ""
