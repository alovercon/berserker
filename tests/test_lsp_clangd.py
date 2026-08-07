"""Test LSP tool with clangd."""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from berserker.tool.lsp import LspTool
from berserker.tool.base import ToolContext

TEST_FILE = os.path.join(os.path.dirname(__file__), "lsp_test.c")
WORKSPACE = os.path.dirname(os.path.dirname(__file__))


def main():
    # type: () -> None
    tool = LspTool()
    ctx = ToolContext(
        session_id="lsp-test",
        message_id="msg-1",
        agent="build",
        abort=threading.Event(),
        extra={"workspace": WORKSPACE},
    )

    print("=" * 60)
    print("1. Starting clangd server...")
    print("=" * 60)
    result = tool.execute(
        {
            "operation": "start_server",
            "server_command": ["clangd", "--background-index"],
            "query": "clangd",
        },
        ctx,
    )
    print("Title:", result.title)
    print("Output:", result.output)
    print("")

    print("=" * 60)
    print("2. Querying symbols in lsp_test.c...")
    print("=" * 60)
    result = tool.execute(
        {
            "operation": "symbols",
            "file_path": TEST_FILE,
            "query": "clangd",
        },
        ctx,
    )
    print("Title:", result.title)
    print("Output:", result.output)
    print("")

    print("=" * 60)
    print("3. Go to definition at line 20, column 5 (create_point call)...")
    print("=" * 60)
    result = tool.execute(
        {
            "operation": "definition",
            "file_path": TEST_FILE,
            "line": 20,
            "column": 5,
            "query": "clangd",
        },
        ctx,
    )
    print("Title:", result.title)
    print("Output:", result.output)
    print("")

    print("=" * 60)
    print("4. Find references at line 9, column 6 (print_point definition)...")
    print("=" * 60)
    result = tool.execute(
        {
            "operation": "references",
            "file_path": TEST_FILE,
            "line": 9,
            "column": 6,
            "query": "clangd",
        },
        ctx,
    )
    print("Title:", result.title)
    print("Output:", result.output)
    print("")

    print("=" * 60)
    print("5. Checking diagnostics (syntax errors) in lsp_test.c...")
    print("=" * 60)
    result = tool.execute(
        {
            "operation": "diagnostics",
            "file_path": TEST_FILE,
            "query": "clangd",
        },
        ctx,
    )
    print("Title:", result.title)
    print("Output:", result.output)
    print("Metadata count:", result.metadata.get("count", 0))
    print("")

    print("=" * 60)
    print("6. Stopping clangd server...")
    print("=" * 60)
    result = tool.execute(
        {
            "operation": "stop_server",
            "query": "clangd",
        },
        ctx,
    )
    print("Title:", result.title)
    print("Output:", result.output)
    print("")

    print("=" * 60)
    print("All tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
