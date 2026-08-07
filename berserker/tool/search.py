"""
Search tools for berserker: GrepTool and WebSearchTool.

GrepTool:
    Search file contents with regex. Uses ripgrep (rg) if available,
    falls back to Python re module with os.walk traversal.

WebSearchTool:
    Web search via exa.ai API. Requires EXA_API_KEY environment variable.

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:
    from urllib2 import HTTPError, Request, URLError, urlopen  # type: ignore

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolError, ToolResult

# ---------------------------------------------------------------------------
# GrepTool
# ---------------------------------------------------------------------------

_GREP_PARAMETERS = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Regular expression pattern to search for.",
        },
        "path": {
            "type": "string",
            "description": "File or directory to search in. Defaults to workspace root.",
        },
        "glob": {
            "type": "string",
            "description": "Glob pattern to filter files (e.g. '*.py').",
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "Whether the search is case-sensitive. Default: False.",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


class GrepTool(Tool):
    """Search file contents using regex patterns.

    Attempts to use ripgrep (rg) for speed, falls back to Python re
    with os.walk for directory traversal.

    Parameters:
        pattern (str): Regex pattern to search for (required).
        path (str, optional): File or directory to search. Defaults to workspace.
        glob (str, optional): Glob pattern to filter files (e.g. '*.py').
        case_sensitive (bool, optional): Case-sensitive search. Default False.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=30, max_output_tokens=16384)

    MAX_RESULTS = 100  # type: int

    def __init__(self):
        # type: () -> None
        super(GrepTool, self).__init__(
            id="grep",
            description=(
                "Fast content search with regex patterns. Uses ripgrep if available (faster), falls back to Python re. Supports full regex syntax (e.g., 'log.*Error', 'function\\s+\\w+'). Filter files with glob parameter (e.g., '*.py'). Output: 'file:line:content' format. 100 result limit. Use for finding code patterns across files. "
                "IMPORTANT: Use this tool instead of bash 'grep', 'rg', or 'findstr' commands. "
                "This tool provides structured output with automatic workspace security validation."
            ),
            parameters=_GREP_PARAMETERS,
        )
        self._rg_available = None  # type: Optional[bool]

    def _check_ripgrep(self):
        # type: () -> bool
        """Check if ripgrep (rg) is available on PATH."""
        if self._rg_available is not None:
            return self._rg_available
        try:
            subprocess.check_output(
                ["rg", "--version"],
                stderr=subprocess.STDOUT,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._rg_available = True
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            self._rg_available = False
        return self._rg_available

    def _validate_path(self, path, workspace):
        # type: (str, str) -> str
        """Validate that path is within workspace. Returns resolved absolute path.

        Note: Absolute paths are allowed. Workspace restriction only applies to relative paths.
        """
        resolved = os.path.realpath(path)

        # Allow absolute paths
        if not os.path.isabs(path):
            # Relative path - must be within workspace
            workspace_resolved = os.path.realpath(workspace)
            if (
                not resolved.startswith(workspace_resolved + os.sep)
                and resolved != workspace_resolved
            ):
                raise ToolError("Path '{}' is outside workspace '{}'".format(path, workspace))

        return resolved

    def _search_with_ripgrep(self, pattern, search_path, glob_pattern, case_sensitive):
        # type: (str, str, Optional[str], bool) -> List[str]
        """Search using ripgrep command."""
        cmd = ["rg", "--no-heading", "--line-number", "--color=never"]

        if not case_sensitive:
            cmd.append("-i")

        if glob_pattern:
            cmd.extend(["--glob", glob_pattern])

        cmd.extend(["--max-count", str(self.MAX_RESULTS)])
        cmd.extend([pattern, search_path])

        try:
            output = subprocess.check_output(
                cmd,
                stderr=subprocess.PIPE,
                timeout=30,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.CalledProcessError as e:
            # rg returns exit code 1 when no matches found — that's OK
            if e.returncode == 1:
                return []
            raise ToolError("ripgrep failed: {}".format(e.stderr))
        except subprocess.TimeoutExpired:
            raise ToolError("ripgrep timed out after 30 seconds")
        except OSError as e:
            raise ToolError("ripgrep not available: {}".format(e))

        if not output or not output.strip():
            return []
        return output.strip().split("\n")

    def _search_with_python(self, pattern, search_path, glob_pattern, case_sensitive):
        # type: (str, str, Optional[str], bool) -> List[str]
        """Search using Python re module with os.walk traversal."""
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ToolError("Invalid regex pattern: {}".format(e))

        results = []  # type: List[str]

        # Determine file filter from glob
        glob_re = None  # type: Optional[re.Pattern]
        if glob_pattern:
            # Convert glob pattern to regex
            glob_re_str = glob_pattern.replace(".", r"\.").replace("*", ".*").replace("?", ".")
            glob_re = re.compile(glob_re_str)

        if os.path.isfile(search_path):
            files_to_search = [search_path]
        else:
            files_to_search = []
            for dirpath, dirnames, filenames in os.walk(search_path):
                # Skip hidden directories and common non-source dirs
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")
                ]
                for fname in filenames:
                    if glob_re and not glob_re.match(fname):
                        continue
                    if fname.startswith("."):
                        continue
                    files_to_search.append(os.path.join(dirpath, fname))

        for filepath in files_to_search:
            if len(results) >= self.MAX_RESULTS:
                break
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(
                                "{}:{}:{}".format(filepath, line_num, line.rstrip("\n\r"))
                            )
                            if len(results) >= self.MAX_RESULTS:
                                break
            except (IOError, OSError):
                # Skip files we can't read
                continue

        return results

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute the grep search."""
        pattern = args["pattern"]  # type: str
        path = args.get("path", "")  # type: str
        glob_pattern = args.get("glob")  # type: Optional[str]
        case_sensitive = args.get("case_sensitive", False)  # type: bool

        # Determine workspace from context extra or default to cwd
        workspace = ""  # type: str
        if ctx.extra and "workspace" in ctx.extra:
            workspace = ctx.extra["workspace"]
        else:
            workspace = os.getcwd()

        # Resolve search path
        if not path:
            search_path = workspace
        else:
            search_path = self._validate_path(path, workspace)

        if not os.path.exists(search_path):
            raise ToolError("Path '{}' does not exist".format(search_path))

        # Choose search backend
        if self._check_ripgrep():
            lines = self._search_with_ripgrep(pattern, search_path, glob_pattern, case_sensitive)
        else:
            lines = self._search_with_python(pattern, search_path, glob_pattern, case_sensitive)

        total_matches = len(lines)
        truncated = total_matches >= self.MAX_RESULTS

        if not lines:
            output = "No matches found for pattern '{}' in {}".format(pattern, search_path)
        else:
            output = "Found {} match(es) for pattern '{}' in {}:\n\n{}".format(
                total_matches,
                pattern,
                search_path,
                "\n".join(lines),
            )
            if truncated:
                output += "\n\n... (truncated to {} results)".format(self.MAX_RESULTS)

        return ToolResult(
            title="Grep results for '{}'".format(pattern),
            output=output,
            metadata={
                "pattern": pattern,
                "path": search_path,
                "glob": glob_pattern,
                "case_sensitive": case_sensitive,
                "total_matches": total_matches,
                "truncated": truncated,
                "backend": "ripgrep" if self._rg_available else "python_re",
            },
        )


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------

_WEBSEARCH_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query string.",
        },
        "num_results": {
            "type": "integer",
            "description": "Number of results to return. Default: 5.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class WebSearchTool(Tool):
    """Search the web using exa.ai API.

    Requires the EXA_API_KEY environment variable to be set.

    Parameters:
        query (str): Search query string (required).
        num_results (int, optional): Number of results. Default 5.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=30, max_output_tokens=8192)

    API_URL = "https://api.exa.ai/search"  # type: str

    def __init__(self):
        # type: () -> None
        super(WebSearchTool, self).__init__(
            id="websearch",
            description=(
                "Search the web using exa.ai API. Requires EXA_API_KEY environment variable. Returns clean, ready-to-use content from search results. Use for finding current information, documentation, or web content. Parameters: query (required), num_results (optional, default 5)."
            ),
            parameters=_WEBSEARCH_PARAMETERS,
        )

    def _get_api_key(self):
        # type: () -> Optional[str]
        """Get the Exa API key from environment."""
        return os.environ.get("EXA_API_KEY")

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute the web search."""
        query = args["query"]  # type: str
        num_results = args.get("num_results", 5)  # type: int

        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                title="Web search unavailable",
                output="Web search requires EXA_API_KEY environment variable",
                metadata={"query": query, "available": False},
            )

        # Build request payload
        payload = json.dumps(
            {
                "query": query,
                "numResults": num_results,
                "text": True,
                "highlights": True,
            }
        ).encode("utf-8")

        req = Request(
            self.API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
            },
            method="POST",
        )

        try:
            response = urlopen(req, timeout=30)
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")
            except Exception as e:
                logger.warning("Failed to read Exa error response body: %s", e)
                pass
            raise ToolError("Exa API returned HTTP {}: {}".format(e.code, body))
        except URLError as e:
            raise ToolError("Exa API request failed: {}".format(e.reason))
        except Exception as e:
            raise ToolError("Web search failed: {}".format(e))

        # Parse results
        results = data.get("results", [])  # type: List[Dict[str, Any]]

        if not results:
            output = "No results found for query: '{}'".format(query)
        else:
            lines = []  # type: List[str]
            for i, item in enumerate(results, 1):
                title = item.get("title", "Untitled")
                url = item.get("url", "")
                # Get text snippet — prefer highlights if available
                highlights = item.get("highlights", [])
                if highlights:
                    snippet = highlights[0] if isinstance(highlights, list) else str(highlights)
                else:
                    text = item.get("text", "")
                    snippet = text[:300] if text else "No snippet available"

                lines.append(
                    "{i}. {title}\n   URL: {url}\n   {snippet}".format(
                        i=i, title=title, url=url, snippet=snippet
                    )
                )
            output = "Web search results for '{}':\n\n{}".format(query, "\n\n".join(lines))

        return ToolResult(
            title="Web search: '{}'".format(query),
            output=output,
            metadata={
                "query": query,
                "num_results": num_results,
                "result_count": len(results),
                "available": True,
            },
        )


# ---------------------------------------------------------------------------
# Registration Helper
# ---------------------------------------------------------------------------


def register_search_tools(registry):
    # type: (Any) -> None
    """Register all search tools with the given registry.

    Args:
        registry: A ToolRegistry instance to register tools with.
    """
    registry.register(GrepTool())
    registry.register(WebSearchTool())
