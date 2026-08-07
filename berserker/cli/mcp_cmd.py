"""
MCP (Model Context Protocol) CLI commands for berserker.
"""

from __future__ import annotations

import sys
from typing import List, Dict, Any, Optional

from berserker.mcp import mcp_manager
from berserker.config import load_config


def _get_configured_servers():
    # type: () -> Dict[str, Dict[str, Any]]
    """
    Get all configured MCP servers from the current config.

    Returns:
        Dict mapping server name to server config dict.
    """
    config = load_config()
    mcp_config = config.get("mcp", {})
    servers = mcp_config.get("servers", {})
    return servers


def _get_server_status(server_name):
    # type: (str) -> str
    """
    Get the connection status of an MCP server.

    Args:
        server_name: Name of the server to check.

    Returns:
        Status string: "connected", "disconnected", or "error".
    """
    # Check if server is in the manager
    with mcp_manager._lock:
        if server_name not in mcp_manager._servers:
            return "disconnected"

        server = mcp_manager._servers[server_name]
        if not hasattr(server, "_connected"):
            return "error"
        if server._connected:
            return "connected"
        else:
            return "disconnected"


def _get_server_tools_count(server_name):
    # type: (str) -> int
    """
    Get the number of tools available from a connected server.

    Args:
        server_name: Name of the server to check.

    Returns:
        Number of tools, or 0 if server is not connected.
    """
    with mcp_manager._lock:
        if server_name not in mcp_manager._servers:
            return 0

        server = mcp_manager._servers[server_name]
        if not server._connected:
            return 0

        # Count tools from this server
        tool_count = 0
        for tool in mcp_manager._tools:
            if hasattr(tool, "_server") and tool._server.name == server_name:
                tool_count += 1
        return tool_count


def cmd_mcp_list():
    # type: () -> None
    """
    List all configured MCP servers from config.

    Output table: SERVER NAME | TRANSPORT | STATUS | TOOLS
    Status: "connected", "disconnected", "error"
    """
    # Get configured servers from config
    configured_servers = _get_configured_servers()

    if not configured_servers:
        print("No MCP servers configured.")
        return

    # Print table header
    print("SERVER NAME | TRANSPORT | STATUS | TOOLS")
    print("-" * 50)

    # Print each server
    for server_name, server_config in configured_servers.items():
        transport = server_config.get("transport", "unknown")
        status = _get_server_status(server_name)
        tools_count = _get_server_tools_count(server_name)

        print(f"{server_name} | {transport} | {status} | {tools_count}")


def cmd_mcp_connect(name):
    # type: (str) -> None
    """
    Connect to MCP server by name.

    Uses berserker.mcp.mcp_manager to connect.
    Prints connection result: "Connected to '{name}', found X tools"

    Args:
        name: Name of the MCP server to connect to.
    """
    # Get server config from current config
    configured_servers = _get_configured_servers()

    if name not in configured_servers:
        print(f"Error: MCP server '{name}' not found in configuration.", file=sys.stderr)
        return

    server_config = configured_servers[name]

    # Create McpServer instance if not already in manager
    from berserker.mcp import McpServer

    transport = server_config.get("transport")
    if transport is None:
        print(f"Error: MCP server '{name}' missing 'transport' configuration.", file=sys.stderr)
        return

    with mcp_manager._lock:
        if name not in mcp_manager._servers:
            # Create new server instance
            server = McpServer(
                name=name,
                transport=transport,
                command=server_config.get("command"),
                args=server_config.get("args", []),
                url=server_config.get("url"),
                api_key=server_config.get("api_key"),
            )
            mcp_manager.add_server(server)

    # Attempt to connect
    try:
        success = mcp_manager._servers[name].connect()
        if success:
            # Discover tools
            tools = mcp_manager._servers[name].list_tools()
            tool_count = len(tools)
            print(f"Connected to '{name}', found {tool_count} tools")
        else:
            print(f"Failed to connect to '{name}'", file=sys.stderr)
    except Exception as e:
        print(f"Error connecting to '{name}': {e}", file=sys.stderr)


def cmd_mcp_disconnect(name):
    # type: (str) -> None
    """
    Disconnect from MCP server.

    Prints confirmation message.

    Args:
        name: Name of the MCP server to disconnect from.
    """
    with mcp_manager._lock:
        if name not in mcp_manager._servers:
            print(f"Error: MCP server '{name}' is not connected.", file=sys.stderr)
            return

    try:
        mcp_manager._servers[name].disconnect()
        print(f"Disconnected from '{name}'")
    except Exception as e:
        print(f"Error disconnecting from '{name}': {e}", file=sys.stderr)
