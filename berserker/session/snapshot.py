"""
berserker.session.snapshot — Git snapshot tracking for session state.

Provides SnapshotTracker class that creates Git snapshots, computes diffs
between snapshots, and extracts diff statistics (additions/deletions/files).

Python 3.8.10 compatible: uses type comments, Optional/Dict/List/Any,
no match/case, no walrus operator, no | union syntax.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from typing import Dict, List, Optional, Any

from berserker.storage import insert as db_insert, get_db


# ---------------------------------------------------------------------------
# CI-friendly git environment (mirrors berserker/tool/git.py _run_git)
# ---------------------------------------------------------------------------

_GIT_ENV = None  # type: Optional[Dict[str, str]]


def _get_git_env():
    # type: () -> Dict[str, str]
    """Return a copy of the current environment with CI-friendly git variables."""
    global _GIT_ENV
    if _GIT_ENV is not None:
        return _GIT_ENV

    env = os.environ.copy()
    env.update(
        {
            "CI": "true",
            "DEBIAN_FRONTEND": "noninteractive",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "HOMEBREW_NO_AUTO_UPDATE": "1",
            "GIT_EDITOR": ":",
            "EDITOR": ":",
            "VISUAL": "",
            "GIT_SEQUENCE_EDITOR": ":",
            "GIT_MERGE_AUTOEDIT": "no",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    _GIT_ENV = env
    return env


def _run_git(args, cwd=None, timeout=30):
    # type: (List[str], Optional[str], int) -> tuple
    """Run a git command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_get_git_env(),
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "Git command timed out"
    except FileNotFoundError:
        return 1, "", "Git not found. Ensure git is installed and in PATH."
    except OSError as e:
        return 1, "", "OS error: {}".format(str(e))


def _get_repo_root(path=None):
    # type: (Optional[str]) -> Optional[str]
    """Get the root of the git repository, or None if not in a git repo."""
    cwd = path or os.getcwd()
    code, out, _ = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if code == 0 and out.strip():
        return out.strip()
    return None


# ---------------------------------------------------------------------------
# SnapshotTracker
# ---------------------------------------------------------------------------


class SnapshotTracker(object):
    """Creates Git snapshots, computes diffs, and extracts statistics.

    Uses `git stash create` to capture the current working tree state WITHOUT
    modifying the working directory. Snapshots can be persisted to a SQLite
    database via the `session_snapshots` table.

    Usage:
        tracker = SnapshotTracker()
        snap = tracker.create_snapshot(session_id="abc123")
        # ... make changes ...
        snap2 = tracker.create_snapshot(session_id="abc123")
        diff = tracker.diff_snapshots(snap["hash"], snap2["hash"])
        stats = tracker.get_diff_stats(diff)
        tracker.save_snapshot(snap2, **stats)
    """

    def __init__(self, repo_path=None, db=None):
        # type: (Optional[str], Optional[Any]) -> None
        """Initialize the SnapshotTracker.

        Args:
            repo_path: Path to the git repository. Defaults to current working
                       directory's git root.
            db: Optional database connection (Database instance). If None,
                uses `get_db()` from berserker.storage when needed.
        """
        self._repo_path = repo_path  # type: Optional[str]
        self._db = db  # type: Optional[Any]
        self._resolved_root = None  # type: Optional[str]

    @property
    def repo_root(self):
        # type: () -> Optional[str]
        """Return the resolved git repository root, or None."""
        if self._resolved_root is None:
            if self._repo_path is not None:
                self._resolved_root = _get_repo_root(self._repo_path)
            else:
                self._resolved_root = _get_repo_root()
        return self._resolved_root

    @property
    def db(self):
        # type: () -> Any
        """Return the database instance, lazily initializing if needed."""
        if self._db is None:
            self._db = get_db()
        return self._db

    def create_snapshot(self, session_id=""):
        # type: (str) -> Dict[str, Any]
        """Create a snapshot of the current working tree state.

        Uses `git stash create` to obtain a stash commit hash WITHOUT
        modifying the working directory or index.

        Args:
            session_id: Optional session identifier to associate with this
                        snapshot.

        Returns:
            Dict with keys:
                - snapshot_id: Short UUID (12 hex chars)
                - hash: Git commit hash string, or None if not in a git repo
                - session_id: The provided session_id
                - created_at: Unix timestamp (int)
        """
        snapshot_id = uuid.uuid4().hex[:12]
        created_at = int(time.time())

        result = {
            "snapshot_id": snapshot_id,
            "hash": None,
            "session_id": session_id,
            "created_at": created_at,
        }

        root = self.repo_root
        if root is None:
            return result

        code, out, _ = _run_git(["stash", "create"], cwd=root)
        if code == 0 and out.strip():
            result["hash"] = out.strip()

        return result

    def diff_snapshots(self, old_hash, new_hash):
        # type: (Optional[str], Optional[str]) -> str
        """Compute the git diff between two snapshot hashes.

        Args:
            old_hash: Git commit hash of the older snapshot.
            new_hash: Git commit hash of the newer snapshot.

        Returns:
            Raw unified diff string. Empty string if either hash is None
            or if the diff command fails.
        """
        if old_hash is None or new_hash is None:
            return ""

        root = self.repo_root
        if root is None:
            return ""

        code, out, _ = _run_git(
            ["diff", "{}..{}".format(old_hash, new_hash)],
            cwd=root,
        )
        if code == 0:
            return out
        return ""

    def get_diff_stats(self, diff_text):
        # type: (str) -> Dict[str, int]
        """Parse a git diff string and extract statistics.

        Counts:
            - additions: Lines starting with '+' (excluding '+++ ' headers)
            - deletions: Lines starting with '-' (excluding '--- ' headers)
            - files: Number of 'diff --git' lines (i.e., files changed)

        Args:
            diff_text: Raw unified diff string from `diff_snapshots`.

        Returns:
            Dict with keys: additions (int), deletions (int), files (int).
        """
        additions = 0
        deletions = 0
        files = 0

        if not diff_text:
            return {"additions": 0, "deletions": 0, "files": 0}

        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                files += 1
            elif line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return {
            "additions": additions,
            "deletions": deletions,
            "files": files,
        }

    def save_snapshot(self, snapshot, additions=0, deletions=0, files=0, diff_summary=""):
        # type: (Dict[str, Any], int, int, int, str) -> None
        """Persist a snapshot to the session_snapshots table.

        Args:
            snapshot: Dict from `create_snapshot` (must contain snapshot_id,
                      hash, session_id, created_at).
            additions: Number of added lines.
            deletions: Number of deleted lines.
            files: Number of files changed.
            diff_summary: Optional human-readable diff summary.
        """
        db_insert(
            self.db,
            "session_snapshots",
            {
                "id": snapshot["snapshot_id"],
                "session_id": snapshot.get("session_id", ""),
                "snapshot_hash": snapshot.get("hash") or "",
                "additions": additions,
                "deletions": deletions,
                "files": files,
                "diff_summary": diff_summary,
                "created_at": snapshot.get("created_at", int(time.time())),
            },
        )

    def get_session_snapshots(self, session_id):
        # type: (str) -> List[Dict[str, Any]]
        """Load all snapshots for a session from the database.

        Args:
            session_id: Session identifier to query.

        Returns:
            List of dicts, each representing a snapshot row from the
            session_snapshots table.
        """
        cursor = self.db.execute(
            "SELECT * FROM session_snapshots WHERE session_id = ? ORDER BY created_at ASC",
            [session_id],
        )
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
