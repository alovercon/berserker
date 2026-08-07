"""
Git integration for berserker.
Provides status parsing, diff, worktree management, and PR integration.
"""

import os
import subprocess
import sys
from typing import List, Dict, Optional, Any


def _run_git_command(args, repo_path=None):
    # type: (List[str], Optional[str]) -> str
    """Run a git command and return stdout as string."""
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

    cwd = repo_path or os.getcwd()

    try:
        result = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True, env=env, timeout=30
        )
        if result.returncode != 0:
            # Check if it's a "not a git repository" error
            if "not a git repository" in result.stderr.lower():
                print("Warning: Not in a git repository", file=sys.stderr)
                return ""
            # For other errors, we'll let them propagate or handle gracefully
            return ""
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _get_repo_root(repo_path=None):
    # type: (Optional[str]) -> Optional[str]
    """Get the root of the git repository."""
    output = _run_git_command(["rev-parse", "--show-toplevel"], repo_path)
    if output:
        return output.strip()
    return None


def git_status(repo_path=None):
    # type: (Optional[str]) -> List[Dict[str, Any]]
    """
    Parse git status --porcelain output.

    Returns list of dicts with keys: status, path, staged
    Status codes: M (modified), A (added), D (deleted), R (renamed), ?? (untracked)
    """
    repo_root = _get_repo_root(repo_path)
    if not repo_root:
        return []

    output = _run_git_command(["status", "--porcelain"], repo_root)
    if not output:
        return []

    result = []
    lines = output.strip().split("\n")

    for line in lines:
        if not line.strip():
            continue

        # Parse porcelain format: XY PATH or XY ORIG_PATH -> PATH
        if len(line) < 3:
            continue

        x_status = line[0]
        y_status = line[1]
        path_part = line[3:]

        # Determine if staged based on X status
        staged = x_status != " "

        # Determine main status code
        if x_status == "?" and y_status == "?":
            status = "??"  # untracked
        elif x_status == "R" or y_status == "R":
            status = "R"  # renamed
        elif x_status != " ":
            status = x_status
        elif y_status != " ":
            status = y_status
        else:
            continue  # skip if no status

        # Handle renamed files (format: "R100 old_path -> new_path")
        if " -> " in path_part:
            # For renamed, we'll use the new path
            paths = path_part.split(" -> ")
            if len(paths) >= 2:
                path = paths[-1].strip()
            else:
                path = path_part.strip()
        else:
            path = path_part.strip()

        result.append({"status": status, "path": path, "staged": staged})

    return result


def git_diff(repo_path=None, staged=False):
    # type: (Optional[str], bool) -> str
    """
    Get unified diff output.

    Args:
        repo_path: Optional repository path
        staged: If True, get diff of staged changes (--cached)

    Returns:
        Unified diff string
    """
    repo_root = _get_repo_root(repo_path)
    if not repo_root:
        return ""

    args = ["diff"]
    if staged:
        args.append("--cached")

    return _run_git_command(args, repo_root)


def worktree_list(repo_path=None):
    # type: (Optional[str]) -> List[Dict[str, Any]]
    """
    List all worktrees.

    Returns list of dicts with keys: path, branch, detached
    """
    repo_root = _get_repo_root(repo_path)
    if not repo_root:
        return []

    output = _run_git_command(["worktree", "list", "--porcelain"], repo_root)
    if not output:
        return []

    result = []
    worktrees = output.strip().split("\n\n")

    for worktree_block in worktrees:
        if not worktree_block.strip():
            continue

        lines = worktree_block.strip().split("\n")
        worktree_info = {}

        for line in lines:
            if line.startswith("worktree "):
                worktree_info["path"] = line[9:].strip()
            elif line.startswith("HEAD "):
                # Skip HEAD line
                continue
            elif line.startswith("branch "):
                worktree_info["branch"] = line[7:].strip()
                worktree_info["detached"] = False
            elif line.startswith("detached"):
                worktree_info["branch"] = None
                worktree_info["detached"] = True

        # Only add if we have a path
        if "path" in worktree_info:
            if "branch" not in worktree_info:
                worktree_info["branch"] = None
                worktree_info["detached"] = True
            result.append(worktree_info)

    return result


def worktree_create(path, branch=None):
    # type: (str, Optional[str]) -> bool
    """
    Create a new worktree.

    Args:
        path: Path where to create the worktree
        branch: Optional branch name to checkout

    Returns:
        True if successful, False otherwise
    """
    repo_root = _get_repo_root()
    if not repo_root:
        return False

    args = ["worktree", "add", path]
    if branch:
        args.append(branch)

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

    try:
        result = subprocess.run(
            ["git"] + args, cwd=repo_root, capture_output=True, text=True, env=env, timeout=60
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def worktree_delete(path):
    # type: (str) -> bool
    """
    Delete a worktree.

    Args:
        path: Path of the worktree to delete

    Returns:
        True if successful, False otherwise
    """
    repo_root = _get_repo_root()
    if not repo_root:
        return False

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

    try:
        result = subprocess.run(
            ["git", "worktree", "remove", path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def pr_list(owner_repo):
    # type: (str) -> List[Dict[str, Any]]
    """
    List pull requests for a repository.

    Args:
        owner_repo: Repository in format 'owner/repo'

    Returns:
        List of dicts with keys: number, title, state, author
    """
    try:
        # Use gh command to get PRs
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

        result = subprocess.run(
            ["gh", "pr", "list", "--repo", owner_repo, "--json", "number,title,state,author"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        if result.returncode != 0:
            return []

        import json

        try:
            prs = json.loads(result.stdout)
            result_list = []
            for pr in prs:
                result_list.append(
                    {
                        "number": pr.get("number"),
                        "title": pr.get("title"),
                        "state": pr.get("state"),
                        "author": pr.get("author", {}).get("login") if pr.get("author") else None,
                    }
                )
            return result_list
        except (json.JSONDecodeError, KeyError):
            return []

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ImportError):
        return []


def pr_diff(owner_repo, number):
    # type: (str, int) -> str
    """
    Get diff for a specific pull request.

    Args:
        owner_repo: Repository in format 'owner/repo'
        number: PR number

    Returns:
        Diff string
    """
    try:
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

        result = subprocess.run(
            ["gh", "pr", "diff", str(number), "--repo", owner_repo],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        if result.returncode == 0:
            return result.stdout
        return ""

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
