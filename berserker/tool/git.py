"""
Git tool for berserker.

Provides git operations (status, diff, log, commit, add, branch, stash, show)
wrapped as a proper tool for LLM agents.

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import os
import subprocess
from typing import List, Dict, Any, Optional

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolResult, ToolExecutionError


class GitTool(Tool):
    """Git operations tool for LLM agents.

    Provides structured git operations with formatted output.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=30, max_output_tokens=8192)

    def __init__(self):
        # type: () -> None
        super(GitTool, self).__init__(
            id="git",
            description="Execute git operations. Supported: status, diff, log, commit, add, branch, stash, show. For operations NOT listed (push, pull, fetch, merge, rebase, checkout, remote, tag, reset), use bash tool with git command. Use 'operation' parameter to specify action. Non-interactive mode only (CI-friendly environment variables set).",
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "status",
                            "diff",
                            "log",
                            "commit",
                            "add",
                            "branch",
                            "stash",
                            "show",
                        ],
                        "description": "Git operation to perform",
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional file/directory path to scope the operation",
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message (required for commit operation)",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "For diff: show staged changes only (default: false)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "For log: number of commits to show (default: 10)",
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Branch name (for branch/stash operations)",
                    },
                    "stash_message": {
                        "type": "string",
                        "description": "Stash message (for stash save operation)",
                    },
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute git operation."""
        operation = args.get("operation", "")
        path = args.get("path")
        repo_root = _get_repo_root(path)

        if not repo_root:
            return ToolResult(
                title="Git Error",
                output="Not a git repository or git is not initialized.",
            )

        # Ensure cwd is a valid string path
        if path and os.path.isdir(path):
            cwd = path
        else:
            cwd = repo_root

        try:
            if operation == "status":
                return self._status(cwd)
            elif operation == "diff":
                return self._diff(cwd, staged=args.get("staged", False), path=path)
            elif operation == "log":
                return self._log(cwd, limit=args.get("limit", 10), path=path)
            elif operation == "commit":
                return self._commit(
                    cwd, message=args.get("message", ""), staged=args.get("staged", False)
                )
            elif operation == "add":
                return self._add(cwd, path=path, all_files=args.get("staged", False))
            elif operation == "branch":
                return self._branch(cwd, branch_name=args.get("branch_name"))
            elif operation == "stash":
                return self._stash(
                    cwd, action=args.get("branch_name", "save"), message=args.get("stash_message")
                )
            elif operation == "show":
                return self._show(cwd, ref=path or "HEAD")
            else:
                return ToolResult(
                    title="Git Error",
                    output="Unknown operation: {}".format(operation),
                )
        except ToolExecutionError as e:
            return ToolResult(
                title="Git Error",
                output=str(e),
            )

    def _add(self, cwd, path=None, all_files=False):
        # type: (str, Optional[str], bool) -> ToolResult
        """Stage files for commit."""
        if all_files:
            args = ["add", "-A"]
        elif path:
            args = ["add", path]
        else:
            return ToolResult(
                title="Git Add Error",
                output="Specify 'path' to stage specific files, or set 'staged' to true to stage all changes.",
            )

        code, out, err = _run_git(args, cwd=cwd)
        if code != 0:
            return ToolResult(title="Git Add Error", output=err or "Failed to stage files")

        staged_msg = "all changes" if all_files else path
        return ToolResult(
            title="Git Add",
            output="Staged: {}".format(staged_msg),
        )

    def _status(self, cwd):
        # type: (str) -> ToolResult
        """Get git status."""
        code, out, err = _run_git(["status", "--short"], cwd=cwd)
        if code != 0:
            return ToolResult(title="Git Status", output=err or "Failed to get status")

        if not out.strip():
            return ToolResult(
                title="Git Status",
                output="Working tree is clean. No changes to commit.",
            )

        # Parse and format status
        lines = out.strip().split("\n")
        staged = []
        modified = []
        untracked = []

        for line in lines:
            if len(line) < 3:
                continue
            x, y = line[0], line[1]
            file_path = line[3:].strip()

            if x == "?" and y == "?":
                untracked.append(file_path)
            elif x != " " and y == " ":
                staged.append(file_path)
            elif x == " " and y != " ":
                modified.append(file_path)
            else:
                modified.append(file_path)

        output_parts = ["## Git Status\n"]

        if staged:
            output_parts.append("### Staged changes:")
            for f in staged:
                output_parts.append("  [M] {}".format(f))
            output_parts.append("")

        if modified:
            output_parts.append("### Modified (not staged):")
            for f in modified:
                output_parts.append("  [M] {}".format(f))
            output_parts.append("")

        if untracked:
            output_parts.append("### Untracked files:")
            for f in untracked:
                output_parts.append("  [?] {}".format(f))
            output_parts.append("")

        return ToolResult(
            title="Git Status",
            output="\n".join(output_parts),
        )

    def _diff(self, cwd, staged=False, path=None):
        # type: (str, bool, Optional[str]) -> ToolResult
        """Get git diff."""
        args = ["diff"]
        if staged:
            args.append("--cached")
        if path:
            args.append("--")
            args.append(path)

        code, out, err = _run_git(args, cwd=cwd)
        if code != 0:
            return ToolResult(title="Git Diff", output=err or "No diff available")

        if not out.strip():
            return ToolResult(
                title="Git Diff",
                output="No changes to show." + (" (staged)" if staged else ""),
            )

        return ToolResult(
            title="Git Diff" + (" (staged)" if staged else ""),
            output=out,
        )

    def _log(self, cwd, limit=10, path=None):
        # type: (str, int, Optional[str]) -> ToolResult
        """Get git log."""
        fmt = "--pretty=format:%h - %s (%ar) <%an>"
        args = ["log", "-n", str(limit), fmt]
        if path:
            args.append("--")
            args.append(path)

        code, out, err = _run_git(args, cwd=cwd)
        if code != 0:
            return ToolResult(title="Git Log", output=err or "No log available")

        if not out.strip():
            return ToolResult(
                title="Git Log",
                output="No commits found.",
            )

        output_parts = ["## Git Log (last {} commits)\n".format(limit)]
        output_parts.append(out)

        return ToolResult(
            title="Git Log",
            output="\n".join(output_parts),
        )

    def _commit(self, cwd, message="", staged=False):
        # type: (str, str, bool) -> ToolResult
        """Create a git commit."""
        if not message:
            return ToolResult(
                title="Git Commit Error",
                output="Commit message is required. Use 'message' parameter.",
            )

        # Stage changes if requested
        if staged:
            code, out, err = _run_git(["add", "-A"], cwd=cwd)
            if code != 0:
                return ToolResult(
                    title="Git Commit Error", output="Failed to stage changes: {}".format(err)
                )

        # Create commit
        code, out, err = _run_git(["commit", "-m", message], cwd=cwd)
        if code != 0:
            return ToolResult(title="Git Commit Error", output=err or "Commit failed")

        return ToolResult(
            title="Git Commit",
            output="Committed successfully: {}\n{}".format(message, out),
        )

    def _branch(self, cwd, branch_name=None):
        # type: (str, Optional[str]) -> ToolResult
        """List or create branches."""
        if branch_name:
            # Create new branch
            code, out, err = _run_git(["branch", branch_name], cwd=cwd)
            if code != 0:
                return ToolResult(title="Git Branch Error", output=err or "Failed to create branch")
            return ToolResult(
                title="Git Branch",
                output="Created branch: {}".format(branch_name),
            )
        else:
            # List branches
            code, out, err = _run_git(["branch", "-a"], cwd=cwd)
            if code != 0:
                return ToolResult(title="Git Branch", output=err or "Failed to list branches")

            if not out.strip():
                return ToolResult(title="Git Branch", output="No branches found.")

            return ToolResult(
                title="Git Branches",
                output="## Git Branches\n\n{}".format(out),
            )

    def _stash(self, cwd, action="save", message=None):
        # type: (str, str, Optional[str]) -> ToolResult
        """Stash operations."""
        if action == "save":
            args = ["stash", "push"]
            if message:
                args.extend(["-m", message])
            code, out, err = _run_git(args, cwd=cwd)
            if code != 0:
                return ToolResult(title="Git Stash Error", output=err or "Failed to stash")
            return ToolResult(
                title="Git Stash",
                output="Stashed changes.{}".format(
                    " Message: {}".format(message) if message else ""
                ),
            )
        elif action == "list":
            code, out, err = _run_git(["stash", "list"], cwd=cwd)
            if code != 0:
                return ToolResult(title="Git Stash", output=err or "Failed to list stashes")
            if not out.strip():
                return ToolResult(title="Git Stash", output="No stashes.")
            return ToolResult(
                title="Git Stash List",
                output="## Stashed Changes\n\n{}".format(out),
            )
        elif action == "pop":
            code, out, err = _run_git(["stash", "pop"], cwd=cwd)
            if code != 0:
                return ToolResult(title="Git Stash Error", output=err or "Failed to pop stash")
            return ToolResult(title="Git Stash", output="Popped latest stash.")
        elif action == "apply":
            code, out, err = _run_git(["stash", "apply"], cwd=cwd)
            if code != 0:
                return ToolResult(title="Git Stash Error", output=err or "Failed to apply stash")
            return ToolResult(title="Git Stash", output="Applied latest stash.")
        elif action == "drop":
            code, out, err = _run_git(["stash", "drop"], cwd=cwd)
            if code != 0:
                return ToolResult(title="Git Stash Error", output=err or "Failed to drop stash")
            return ToolResult(title="Git Stash", output="Dropped latest stash.")
        else:
            return ToolResult(
                title="Git Stash Error",
                output="Unknown stash action: {}. Use: save, list, pop, apply, drop".format(action),
            )

    def _show(self, cwd, ref="HEAD"):
        # type: (str, str) -> ToolResult
        """Show git object (commit, tag, etc.)."""
        code, out, err = _run_git(["show", ref], cwd=cwd)
        if code != 0:
            return ToolResult(title="Git Show Error", output=err or "Failed to show {}".format(ref))

        if not out.strip():
            return ToolResult(title="Git Show", output="No output for {}".format(ref))

        return ToolResult(
            title="Git Show: {}".format(ref),
            output=out,
        )
