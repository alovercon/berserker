"""
MCP (Model Context Protocol) client for berserker.

Connects to local/remote MCP servers, discovers tools, calls tools,
and registers them with ToolRegistry.

Implements JSON-RPC 2.0 from scratch (no external MCP libraries).
Supports two transport modes:
  - stdio: local subprocess with Content-Length framing
  - http: remote HTTP/SSE with optional Bearer auth

Python 3.8.10 compatible: type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import uuid
from typing import List, Dict, Any, Optional, Union

from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from berserker.tool.base import Tool, ToolContext, ToolResult, ToolError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 helpers
# ---------------------------------------------------------------------------


def _make_request(method, params):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """Build a JSON-RPC 2.0 request dict with a unique integer id."""
    return {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": method,
        "params": params,
    }


_id_counter = 0
_id_lock = threading.Lock()


def _next_id():
    # type: () -> int
    """Generate a monotonically increasing JSON-RPC request id."""
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return _id_counter


def _encode_content_length(body):
    # type: (str) -> bytes
    """Encode a JSON-RPC message with Content-Length framing (stdio transport).

    Format: Content-Length: <n>\\r\\n\\r\\n<json-body>
    """
    encoded = body.encode("utf-8")
    header = "Content-Length: {}\r\n\r\n".format(len(encoded)).encode("utf-8")
    return header + encoded


def _read_with_timeout(stream, num_bytes, timeout):
    # type: (Any, int, float) -> bytes
    """Read exactly num_bytes from a binary stream within timeout seconds.

    Uses a background thread for the blocking read, with the main thread
    waiting via threading.Event. Raises IOError on timeout.

    Args:
        stream: Binary stream (e.g., subprocess.PIPE stdout).
        num_bytes: Exact number of bytes to read.
        timeout: Maximum wait time in seconds.

    Returns:
        The bytes read.

    Raises:
        IOError: If the read times out or stream closes prematurely.
    """
    result = [None]  # type: List[Optional[bytes]]
    error = [None]  # type: List[Optional[Exception]]
    done = threading.Event()

    def _reader():
        # type: () -> None
        try:
            data = b""
            while len(data) < num_bytes:
                chunk = stream.read(num_bytes - len(data))
                if not chunk:
                    error[0] = IOError("Stream closed before reading {} bytes".format(num_bytes))
                    break
                data += chunk
            if error[0] is None:
                result[0] = data
        except Exception as e:
            error[0] = e
        finally:
            done.set()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    if not done.wait(timeout=timeout):
        raise IOError("Read timed out after {} seconds".format(timeout))

    if error[0] is not None:
        raise error[0]
    if result[0] is None:
        raise IOError("Read returned no data")
    return result[0]


def _readline_with_timeout(stream, timeout):
    # type: (Any, float) -> bytes
    """Read one line (up to and including newline) from a binary stream within timeout.

    Args:
        stream: Binary stream (e.g., subprocess.PIPE stdout).
        timeout: Maximum wait time in seconds.

    Returns:
        The line bytes (including trailing newline).

    Raises:
        IOError: If the read times out or stream closes.
    """
    result = [None]  # type: List[Optional[bytes]]
    error = [None]  # type: List[Optional[Exception]]
    done = threading.Event()

    def _reader():
        # type: () -> None
        try:
            line = stream.readline()
            if not line:
                error[0] = IOError("Stream closed while reading line")
            else:
                result[0] = line
        except Exception as e:
            error[0] = e
        finally:
            done.set()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    if not done.wait(timeout=timeout):
        raise IOError("Read timed out after {} seconds".format(timeout))

    if error[0] is not None:
        raise error[0]
    if result[0] is None:
        raise IOError("Readline returned no data")
    return result[0]


def _read_content_length_output(proc_stdout, timeout=30):
    # type: (Any, float) -> str
    """Read a Content-Length framed response from a subprocess stdout.

    Reads the header to get content length, then reads exactly that many bytes.
    Each read operation has a per-operation timeout to prevent indefinite hangs.

    Args:
        proc_stdout: Binary stream (subprocess.PIPE stdout).
        timeout: Maximum wait time in seconds for each read operation (default: 30).

    Returns:
        The decoded response body string.

    Raises:
        IOError: If headers are malformed, content is missing, or a read times out.
    """
    # Read header lines until we find Content-Length
    content_length = None
    while True:
        line = _readline_with_timeout(proc_stdout, timeout=timeout)
        line_str = line.decode("utf-8").strip()
        if not line_str:
            # Blank line signals end of headers
            break
        if line_str.startswith("Content-Length:"):
            try:
                content_length = int(line_str.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                raise IOError("Invalid Content-Length header: {}".format(line_str))

    if content_length is None:
        raise IOError("No Content-Length header found in response")

    # Read exactly content_length bytes with timeout
    body = _read_with_timeout(proc_stdout, content_length, timeout=timeout)

    return body.decode("utf-8")


# ---------------------------------------------------------------------------
# McpServer
# ---------------------------------------------------------------------------


class McpServer(object):
    """Represents a connection to an MCP server.

    Supports two transport modes:
      - 'stdio': local subprocess spawned with command + args
      - 'http': remote HTTP endpoint with optional Bearer auth

    Attributes:
        name: Human-readable server name.
        transport: 'stdio' or 'http'.
        command: Executable for stdio transport.
        args: Argument list for stdio transport.
        url: Endpoint URL for http transport.
        api_key: Optional Bearer token for http auth.
    """

    def __init__(
        self,
        name,  # type: str
        transport,  # type: str
        command=None,  # type: Optional[str]
        args=None,  # type: Optional[List[str]]
        url=None,  # type: Optional[str]
        api_key=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        """Configure an MCP server connection.

        Args:
            name: Human-readable server identifier.
            transport: 'stdio' for local subprocess, 'http' for remote.
            command: Executable path for stdio transport.
            args: Command-line arguments for stdio transport.
            url: HTTP endpoint URL for http transport.
            api_key: Optional Bearer token for http authentication.
        """
        self.name = name
        self.transport = transport
        self.command = command
        self.args = args if args is not None else []
        self.url = url
        self.api_key = api_key

        # Runtime state
        self._process = None  # type: Optional[subprocess.Popen]
        self._lock = threading.Lock()
        self._connected = False

    def connect(self):
        # type: () -> bool
        """Establish connection to the MCP server.

        For stdio: spawns subprocess and performs MCP initialize handshake.
        For http: validates URL and performs initialize handshake.

        Returns:
            True if connection succeeded, False otherwise.
        """
        if self._connected:
            return True

        try:
            if self.transport == "stdio":
                self._connect_stdio()
            elif self.transport == "http":
                self._connect_http()
            else:
                logger.warning(
                    "McpServer[%s]: unknown transport '%s'",
                    self.name,
                    self.transport,
                )
                return False

            # Perform MCP initialize handshake
            self._initialize()
            self._connected = True
            logger.info("McpServer[%s]: connected successfully", self.name)
            return True

        except Exception as e:
            logger.warning(
                "McpServer[%s]: connection failed: %s",
                self.name,
                str(e),
            )
            # Clean up partial state
            self.disconnect()
            return False

    def _connect_stdio(self):
        # type: () -> None
        """Spawn subprocess for stdio transport."""
        if not self.command:
            raise ValueError("stdio transport requires 'command'")

        cmd = [self.command] + list(self.args)
        logger.info("McpServer[%s]: spawning subprocess: %s", self.name, cmd)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,  # unbuffered for real-time I/O
        )

    def _connect_http(self):
        # type: () -> None
        """Validate HTTP transport configuration."""
        if not self.url:
            raise ValueError("http transport requires 'url'")
        logger.info("McpServer[%s]: configured HTTP endpoint: %s", self.name, self.url)

    def _initialize(self):
        # type: () -> Dict[str, Any]
        """Perform MCP protocol initialize handshake.

        Returns:
            Server capabilities dict from initialize response.
        """
        result = self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "berserker",
                    "version": "0.1.0",
                },
            },
        )
        # Send initialized notification (no response expected)
        self._send_notification("notifications/initialized", {})
        return result

    def disconnect(self):
        # type: () -> None
        """Close connection and clean up resources.

        Terminates subprocess for stdio transport.
        """
        self._connected = False
        if self._process is not None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            except Exception as e:
                logger.warning(
                    "McpServer[%s]: error during disconnect: %s",
                    self.name,
                    str(e),
                )
            finally:
                self._process = None
        logger.info("McpServer[%s]: disconnected", self.name)

    def list_tools(self):
        # type: () -> List[Dict[str, Any]]
        """Discover available tools from the MCP server.

        Returns:
            List of tool dicts, each with 'name', 'description', 'inputSchema'.
        """
        if not self._connected:
            logger.warning("McpServer[%s]: not connected, cannot list tools", self.name)
            return []

        try:
            result = self._send_request("tools/list", {})
            tools = result.get("tools", [])  # type: List[Dict[str, Any]]
            logger.info(
                "McpServer[%s]: discovered %d tools",
                self.name,
                len(tools),
            )
            return tools
        except Exception as e:
            logger.warning(
                "McpServer[%s]: failed to list tools: %s",
                self.name,
                str(e),
            )
            return []

    def call_tool(self, tool_name, args):
        # type: (str, Dict[str, Any]) -> Dict[str, Any]
        """Call an MCP tool by name with arguments.

        Args:
            tool_name: Name of the tool to call.
            args: Arguments dict matching the tool's inputSchema.

        Returns:
            Dict with 'content' key on success, or 'error' on failure.
        """
        if not self._connected:
            raise ToolError(
                "McpServer[{}]: not connected, cannot call tool '{}'".format(self.name, tool_name)
            )

        result = self._send_request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": args,
            },
        )
        return result

    def _send_notification(self, method, params):
        # type: (str, Dict[str, Any]) -> None
        """Send a JSON-RPC notification (no id, no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        body = json.dumps(notification)

        if self.transport == "stdio" and self._process is not None:
            if self._process.stdin is not None:
                self._process.stdin.write(_encode_content_length(body))
                self._process.stdin.flush()

    def _send_request(self, method, params):
        # type: (str, Dict[str, Any]) -> Dict[str, Any]
        """Send a JSON-RPC 2.0 request and return the result.

        Args:
            method: JSON-RPC method name.
            params: Method parameters dict.

        Returns:
            The 'result' field from the JSON-RPC response.

        Raises:
            IOError: If transport communication fails.
            ToolError: If the JSON-RPC response contains an error.
        """
        request = _make_request(method, params)
        request_id = request["id"]
        body = json.dumps(request)

        if self.transport == "stdio":
            response = self._send_stdio_request(body)
        elif self.transport == "http":
            response = self._send_http_request(body)
        else:
            raise IOError("Unknown transport: {}".format(self.transport))

        # Parse and validate response
        if response.get("id") != request_id:
            logger.warning(
                "McpServer[%s]: response id mismatch: expected %d, got %s",
                self.name,
                request_id,
                response.get("id"),
            )

        if "error" in response:
            error = response["error"]
            error_msg = "JSON-RPC error [{}]: {}".format(
                error.get("code", -1),
                error.get("message", "unknown error"),
            )
            raise ToolError(error_msg)

        return response.get("result", {})

    def _send_stdio_request(self, body):
        # type: (str) -> Dict[str, Any]
        """Send a JSON-RPC request via stdio and parse the response."""
        if self._process is None or self._process.stdin is None:
            raise IOError("stdio subprocess not available")

        with self._lock:
            # Write request
            self._process.stdin.write(_encode_content_length(body))
            self._process.stdin.flush()

            # Read response
            response_text = _read_content_length_output(self._process.stdout, timeout=30)
            return json.loads(response_text)

    def _send_http_request(self, body):
        # type: (str) -> Dict[str, Any]
        """Send a JSON-RPC request via HTTP POST and parse the response."""
        if not self.url:
            raise IOError("HTTP transport has no URL configured")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = "Bearer {}".format(self.api_key)

        data = body.encode("utf-8")
        req = Request(self.url, data=data, headers=headers, method="POST")

        try:
            response = urlopen(req, timeout=30)
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)
        except HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise IOError("HTTP {} error from MCP server: {}".format(e.code, error_body))
        except URLError as e:
            raise IOError("HTTP connection failed: {}".format(str(e.reason)))


# ---------------------------------------------------------------------------
# McpTool
# ---------------------------------------------------------------------------


class McpTool(Tool):
    """Wraps an MCP-discovered tool as a berserker Tool.

    The id is formatted as 'mcp-{server_name}-{tool_name}' to ensure
    uniqueness across multiple MCP servers.
    """

    def __init__(self, server, tool_def):
        # type: (McpServer, Dict[str, Any]) -> None
        """Create an McpTool wrapper.

        Args:
            server: The McpServer that provides this tool.
            tool_def: Tool definition dict from MCP tools/list (name, description, inputSchema).
        """
        server_name = server.name
        tool_name = tool_def.get("name", "unknown")
        tool_id = "mcp-{}-{}".format(server_name, tool_name)
        description = tool_def.get("description", "")
        parameters = tool_def.get(
            "inputSchema",
            {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

        super(McpTool, self).__init__(tool_id, description, parameters)
        self._server = server
        self._tool_name = tool_name

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute the MCP tool via the server.

        Args:
            args: Arguments dict matching the tool's inputSchema.
            ctx: ToolContext (unused for MCP calls but required by interface).

        Returns:
            ToolResult with the MCP tool's output.

        Raises:
            ToolError: If the MCP tool call fails.
        """
        # Check abort signal
        if ctx.abort.is_set():
            raise ToolError("MCP tool execution aborted")

        try:
            result = self._server.call_tool(self._tool_name, args)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError("MCP tool '{}' call failed: {}".format(self._tool_name, str(e)))

        # Format MCP result into ToolResult
        # MCP tools/call returns: {"content": [...], "isError": bool}
        is_error = result.get("isError", False)
        content = result.get("content", [])

        # Build output string from content array
        output_parts = []  # type: List[str]
        for item in content:
            if isinstance(item, dict):
                content_type = item.get("type", "text")
                if content_type == "text":
                    output_parts.append(item.get("text", ""))
                elif content_type == "image":
                    output_parts.append("[Image: {}]".format(item.get("mimeType", "unknown")))
                elif content_type == "resource":
                    resource = item.get("resource", {})
                    output_parts.append("[Resource: {}]".format(resource.get("uri", "unknown")))
                else:
                    output_parts.append(json.dumps(item))
            else:
                output_parts.append(str(item))

        output = "\n".join(output_parts) if output_parts else json.dumps(result)

        if is_error:
            raise ToolError("MCP tool '{}' returned error: {}".format(self._tool_name, output))

        return ToolResult(
            title="MCP tool: {}".format(self._tool_name),
            output=output,
            metadata={"server": self._server.name, "tool": self._tool_name},
        )

    def __repr__(self):
        # type: () -> str
        return "<McpTool id={} server={} tool={}>".format(
            self.id, self._server.name, self._tool_name
        )


# ---------------------------------------------------------------------------
# McpManager
# ---------------------------------------------------------------------------


class McpManager(object):
    """Manages multiple MCP server connections and their discovered tools.

    Provides a central point to configure, connect, and register MCP tools
    with the berserker ToolRegistry.
    """

    def __init__(self):
        # type: () -> None
        """Initialize the MCP manager."""
        self._servers = {}  # type: Dict[str, McpServer]
        self._tools = []  # type: List[McpTool]
        self._lock = threading.Lock()

    def add_server(self, server):
        # type: (McpServer) -> None
        """Register an MCP server configuration.

        Args:
            server: McpServer instance to manage.
        """
        with self._lock:
            self._servers[server.name] = server
            logger.info("McpManager: added server '%s'", server.name)

    def connect_all(self):
        # type: () -> List[str]
        """Connect all registered servers and discover their tools.

        Servers that fail to connect are logged and skipped (graceful fallback).

        Returns:
            List of names of successfully connected servers.
        """
        connected = []  # type: List[str]
        all_tools = []  # type: List[McpTool]

        with self._lock:
            servers_snapshot = list(self._servers.values())

        for server in servers_snapshot:
            try:
                success = server.connect()
                if not success:
                    logger.warning(
                        "McpManager: server '%s' failed to connect, skipping",
                        server.name,
                    )
                    continue

                # Discover tools from this server
                tool_defs = server.list_tools()
                for tool_def in tool_defs:
                    mcp_tool = McpTool(server, tool_def)
                    all_tools.append(mcp_tool)

                connected.append(server.name)
                logger.info(
                    "McpManager: server '%s' connected with %d tools",
                    server.name,
                    len(tool_defs),
                )

            except Exception as e:
                logger.warning(
                    "McpManager: error connecting server '%s': %s",
                    server.name,
                    str(e),
                )
                # Graceful fallback: skip this server, continue with others
                continue

        with self._lock:
            self._tools = all_tools

        return connected

    def get_tools(self):
        # type: () -> List[McpTool]
        """Get all discovered McpTool objects from all connected servers.

        Returns:
            List of McpTool instances ready for registration.
        """
        with self._lock:
            return list(self._tools)

    def register_with(self, registry):
        # type: (Any) -> int
        """Register all discovered McpTool objects with a ToolRegistry.

        Args:
            registry: A ToolRegistry instance.

        Returns:
            Number of tools successfully registered.
        """
        tools = self.get_tools()
        count = 0
        for tool in tools:
            try:
                registry.register(tool)
                count += 1
                logger.info("McpManager: registered tool '%s'", tool.id)
            except ToolError as e:
                logger.warning(
                    "McpManager: failed to register tool '%s': %s",
                    tool.id,
                    str(e),
                )
        return count

    def disconnect_all(self):
        # type: () -> None
        """Disconnect all MCP servers and clear discovered tools."""
        with self._lock:
            servers_snapshot = list(self._servers.values())
            self._tools = []

        for server in servers_snapshot:
            try:
                server.disconnect()
            except Exception as e:
                logger.warning(
                    "McpManager: error disconnecting server '%s': %s",
                    server.name,
                    str(e),
                )

        logger.info("McpManager: all servers disconnected")


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

mcp_manager = McpManager()
