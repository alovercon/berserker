"""
LSP client tool for berserker: start/stop LSP servers and query symbols,
definitions, references via raw JSON-RPC over stdin/stdout.

Provides:
- LspTool: Tool subclass with operations for LSP server lifecycle and queries
- register_lsp_tool(registry): Registration helper

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Dict, Any, Optional, List, Callable, Set

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolResult, ToolError


# ---------------------------------------------------------------------------
# JSON-RPC Helpers
# ---------------------------------------------------------------------------

_JSON_RPC_VERSION = "2.0"  # type: str


def _make_request(method, params, request_id):
    # type: (str, Dict[str, Any], int) -> Dict[str, Any]
    """Build a JSON-RPC 2.0 request message."""
    return {
        "jsonrpc": _JSON_RPC_VERSION,
        "id": request_id,
        "method": method,
        "params": params,
    }


def _make_notification(method, params):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """Build a JSON-RPC 2.0 notification (no id)."""
    return {
        "jsonrpc": _JSON_RPC_VERSION,
        "method": method,
        "params": params,
    }


def _encode_message(msg):
    # type: (Dict[str, Any]) -> bytes
    """Encode a JSON-RPC message with Content-Length header for LSP transport."""
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    header = "Content-Length: {}\r\n\r\n".format(len(body)).encode("ascii")
    return header + body


def _readline_with_timeout(stream, timeout):
    # type: (Any, float) -> bytes
    """Read one line from a binary stream within timeout seconds.

    Uses a background thread for the blocking readline, with the main thread
    waiting via threading.Event. Raises ToolError on timeout.

    Args:
        stream: Binary stream (e.g., subprocess.PIPE stdout).
        timeout: Maximum wait time in seconds.

    Returns:
        The line bytes (including trailing newline).

    Raises:
        ToolError: If the read times out or stream closes.
    """
    result = [None]  # type: List[Optional[bytes]]
    error = [None]  # type: List[Optional[Exception]]
    done = threading.Event()

    def _reader():
        # type: () -> None
        try:
            line = stream.readline()
            if not line:
                error[0] = ToolError("LSP server closed stdout unexpectedly")
            else:
                result[0] = line
        except Exception as e:
            error[0] = ToolError("LSP read error: {}".format(e))
        finally:
            done.set()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    if not done.wait(timeout=timeout):
        raise ToolError("LSP read timed out after {} seconds".format(timeout))

    if error[0] is not None:
        raise error[0]
    if result[0] is None:
        raise ToolError("LSP readline returned no data")
    return result[0]


def _read_exact_with_timeout(stream, num_bytes, timeout):
    # type: (Any, int, float) -> bytes
    """Read exactly num_bytes from a binary stream within timeout seconds.

    Uses a background thread for the blocking read, with the main thread
    waiting via threading.Event. Raises ToolError on timeout.

    Args:
        stream: Binary stream (e.g., subprocess.PIPE stdout).
        num_bytes: Exact number of bytes to read.
        timeout: Maximum wait time in seconds.

    Returns:
        The bytes read.

    Raises:
        ToolError: If the read times out or stream closes prematurely.
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
                    error[0] = ToolError("LSP server closed stdout while reading message body")
                    break
                data += chunk
            if error[0] is None:
                result[0] = data
        except Exception as e:
            error[0] = ToolError("LSP read error: {}".format(e))
        finally:
            done.set()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    if not done.wait(timeout=timeout):
        raise ToolError("LSP read timed out after {} seconds".format(timeout))

    if error[0] is not None:
        raise error[0]
    if result[0] is None:
        raise ToolError("LSP read returned no data")
    return result[0]


def _read_message(proc, timeout=30):
    # type: (subprocess.Popen, int) -> Optional[Dict[str, Any]]
    """Read a single JSON-RPC response from an LSP server subprocess.

    Parses the Content-Length header, then reads exactly that many bytes
    of JSON body and decodes it.

    Each read operation has a per-operation timeout to prevent indefinite hangs.
    The total timeout is split: half for header reading, half for body reading.

    Returns the parsed JSON dict, or None on error/timeout.
    Raises ToolError on communication failure.
    """
    if proc.stdout is None:
        raise ToolError("LSP server stdout is not available")

    # Split timeout: half for headers, half for body
    header_timeout = timeout / 2.0
    body_timeout = timeout / 2.0

    # Read headers line by line until we find Content-Length
    content_length = None  # type: Optional[int]
    while True:
        line = _readline_with_timeout(proc.stdout, timeout=header_timeout)
        line_str = line.decode("utf-8", errors="replace").strip()
        if not line_str:
            # Empty line = end of headers
            break
        if line_str.lower().startswith("content-length:"):
            try:
                content_length = int(line_str.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                raise ToolError("Invalid Content-Length header: {}".format(line_str))

    if content_length is None:
        raise ToolError("Missing Content-Length header in LSP response")

    # Read exactly content_length bytes of body
    body = _read_exact_with_timeout(proc.stdout, content_length, timeout=body_timeout)

    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# LspServer wrapper
# ---------------------------------------------------------------------------


class _LspServer(object):
    """Internal wrapper around an LSP server subprocess.

    Manages the process, request ID counter, and thread-safe communication.
    """

    def __init__(self, proc, server_id, root_uri):
        # type: (subprocess.Popen, str, str) -> None
        self.proc = proc  # type: subprocess.Popen
        self.server_id = server_id  # type: str
        self.root_uri = root_uri  # type: str
        self._next_id = 1  # type: int
        self._lock = threading.Lock()

    def _next_request_id(self):
        # type: () -> int
        """Get the next unique request ID."""
        rid = self._next_id
        self._next_id += 1
        return rid

    def send_request(self, method, params, timeout=30):
        # type: (str, Dict[str, Any], int) -> Dict[str, Any]
        """Send a JSON-RPC request and wait for the response.

        Returns the 'result' field from the response.
        Raises ToolError on error response or communication failure.
        """
        with self._lock:
            request_id = self._next_request_id()
            msg = _make_request(method, params, request_id)
            if self.proc.stdin is None:
                raise ToolError("LSP server stdin is not available")
            self.proc.stdin.write(_encode_message(msg))
            self.proc.stdin.flush()

        # Read response (outside lock to avoid blocking other threads,
        # but LSP servers respond in order so this is safe for single-threaded use)
        response = _read_message(self.proc, timeout=timeout)

        if response is None:
            raise ToolError("No response from LSP server for method '{}'".format(method))

        # Check for error response
        if "error" in response:
            err = response["error"]
            raise ToolError(
                "LSP error (code {}): {}".format(
                    err.get("code", -1), err.get("message", "Unknown error")
                )
            )

        return response.get("result", {})

    def send_notification(self, method, params):
        # type: (str, Dict[str, Any]) -> None
        """Send a JSON-RPC notification (no response expected)."""
        with self._lock:
            msg = _make_notification(method, params)
            if self.proc.stdin is None:
                raise ToolError("LSP server stdin is not available")
            self.proc.stdin.write(_encode_message(msg))
            self.proc.stdin.flush()

    def stop(self):
        # type: () -> None
        """Gracefully shutdown the LSP server and terminate the process."""
        try:
            self.send_notification("shutdown", {})
            self.send_notification("exit", {})
        except ToolError:
            pass  # Server may already be dead
        try:
            self.proc.terminate()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# ---------------------------------------------------------------------------
# Global server registry (thread-safe)
# ---------------------------------------------------------------------------

_servers = {}  # type: Dict[str, _LspServer]
_servers_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Global diagnostics registry (thread-safe)
# ---------------------------------------------------------------------------

_diagnostics = {}  # type: Dict[str, List[Dict[str, Any]]]  # file_uri -> diagnostics
_diagnostics_lock = threading.Lock()
_diagnostics_events = {}  # type: Dict[str, threading.Event]  # file_uri -> signal event
_diagnostics_events_lock = threading.Lock()

# LSP diagnostic severity mapping: 1=error, 2=warning, 3=information, 4=hint
_SEVERITY_MAP = {
    1: "error",
    2: "warning",
    3: "information",
    4: "hint",
}  # type: Dict[int, str]


def _get_server(server_id):
    # type: (str) -> _LspServer
    """Get a running LSP server by ID.

    Raises ToolError if not found.
    """
    with _servers_lock:
        if server_id not in _servers:
            raise ToolError(
                "LSP server '{}' is not running. Start it first with operation='start_server'.".format(
                    server_id
                )
            )
        return _servers[server_id]


# ---------------------------------------------------------------------------
# _NotifyingLspServer — Server wrapper with background notification reader
# ---------------------------------------------------------------------------


class _NotifyingLspServer(_LspServer):
    """LSP server wrapper with background notification reader thread.

    Extends _LspServer by continuously reading messages from stdout in a
    background thread, dispatching notifications to registered handlers.
    This is required for receiving textDocument/publishDiagnostics.
    """

    def __init__(self, proc, server_id, root_uri):
        # type: (subprocess.Popen, str, str) -> None
        super(_NotifyingLspServer, self).__init__(proc, server_id, root_uri)
        self._pending = {}  # type: Dict[int, threading.Event]
        self._responses = {}  # type: Dict[int, Dict[str, Any]]
        self._notification_handlers = {}  # type: Dict[str, List[Callable[[Dict[str, Any]], None]]]
        self._opened_files = set()  # type: Set[str]
        self._stop_event = threading.Event()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="lsp-reader-{}".format(server_id), daemon=True
        )
        self._reader_thread.start()

    def is_file_open(self, file_uri):
        # type: (str) -> bool
        """Check if a file has been opened on this server."""
        with self._lock:
            return file_uri in self._opened_files

    def mark_file_open(self, file_uri):
        # type: (str) -> None
        """Mark a file as opened on this server."""
        with self._lock:
            self._opened_files.add(file_uri)

    def _reader_loop(self):
        # type: () -> None
        """Background loop that reads messages and dispatches them.

        Uses blocking reads directly (not _read_message) to avoid
        creating multiple threads that race on the same stdout stream.
        """
        if self.proc.stdout is None:
            return

        stream = self.proc.stdout  # type: Any

        while not self._stop_event.is_set():
            try:
                # Read all headers first
                headers = {}  # type: Dict[str, str]
                while not self._stop_event.is_set():
                    line = stream.readline()
                    if not line:
                        # Stream closed
                        return
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        # Empty line = end of headers
                        break
                    if ":" in line_str:
                        key, value = line_str.split(":", 1)
                        headers[key.strip().lower()] = value.strip()

                if self._stop_event.is_set():
                    return

                content_length = int(headers.get("content-length", 0))
                if content_length == 0:
                    continue

                # Read body
                body = b""
                while len(body) < content_length:
                    chunk = stream.read(content_length - len(body))
                    if not chunk:
                        # Stream closed
                        return
                    body += chunk

                msg = json.loads(body.decode("utf-8"))

                msg_id = msg.get("id")  # type: Optional[int]
                if msg_id is not None:
                    # Response to a request
                    with self._lock:
                        self._responses[msg_id] = msg
                        if msg_id in self._pending:
                            self._pending[msg_id].set()
                else:
                    # Notification
                    method = msg.get("method", "")  # type: str
                    params = msg.get("params", {})  # type: Dict[str, Any]
                    self._dispatch_notification(method, params)

            except (IOError, OSError):
                return
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Error in LSP reader loop for %s", self.server_id)
                if self._stop_event.is_set():
                    break
                continue

    def register_notification_handler(self, method, handler):
        # type: (str, Callable[[Dict[str, Any]], None]) -> None
        """Register a callback for a notification method."""
        with self._lock:
            if method not in self._notification_handlers:
                self._notification_handlers[method] = []
            self._notification_handlers[method].append(handler)

    def _dispatch_notification(self, method, params):
        # type: (str, Dict[str, Any]) -> None
        """Call all registered handlers for a notification method."""
        with self._lock:
            handlers = list(self._notification_handlers.get(method, []))
        for handler in handlers:
            try:
                handler(params)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Error in notification handler for %s on %s", method, self.server_id
                )

    def send_request(self, method, params, timeout=30):
        # type: (str, Dict[str, Any], int) -> Dict[str, Any]
        """Send a JSON-RPC request and wait for the response.

        Overrides _LspServer.send_request to use the background reader.
        """
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            self._pending[request_id] = event

        msg = _make_request(method, params, request_id)
        with self._lock:
            if self.proc.stdin is None:
                raise ToolError("LSP server stdin is not available")
            self.proc.stdin.write(_encode_message(msg))
            self.proc.stdin.flush()

        # Wait for response
        got_response = event.wait(timeout=timeout)
        if not got_response:
            with self._lock:
                self._pending.pop(request_id, None)
            raise ToolError(
                "Timeout waiting for LSP response to '{}' ({}s)".format(method, timeout)
            )

        with self._lock:
            self._pending.pop(request_id, None)
            response = self._responses.pop(request_id, None)

        if response is None:
            raise ToolError("No response from LSP server for method '{}'".format(method))

        if "error" in response:
            err = response["error"]
            raise ToolError(
                "LSP error (code {}): {}".format(
                    err.get("code", -1), err.get("message", "Unknown error")
                )
            )

        return response.get("result", {})

    def stop(self):
        # type: () -> None
        """Gracefully shutdown and stop the reader thread."""
        self._stop_event.set()
        try:
            self.send_notification("shutdown", {})
            self.send_notification("exit", {})
        except ToolError:
            pass
        try:
            self.proc.terminate()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._reader_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# LspTool
# ---------------------------------------------------------------------------


class LspTool(Tool):
    """LSP client tool for starting/stopping servers and querying language features.

    Operations:
    - start_server: Launch an LSP server subprocess and initialize it
    - stop_server: Gracefully shutdown an LSP server
    - symbols: Query document symbols from an LSP server
    - definition: Go to definition at a position
    - references: Find all references to a symbol at a position
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=30, max_output_tokens=8192)

    def __init__(self):
        # type: () -> None
        super(LspTool, self).__init__(
            id="lsp",
            description=(
                "LSP client for starting/stopping language servers and querying code intelligence features. "
                "Provides operations: start_server, stop_server, symbols, definition, references, diagnostics. "
                "Servers must be explicitly started with server_command before use, each server has an ID (query parameter). "
                "Use for precise code navigation - finding symbol definitions, finding all references to a symbol, "
                "listing document symbols, and checking syntax/type errors (diagnostics). Uses raw JSON-RPC over "
                "stdin/stdout, file_path is required for symbols/definition/references/diagnostics, line/column are 1-based."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "start_server",
                            "stop_server",
                            "symbols",
                            "definition",
                            "references",
                            "diagnostics",
                        ],
                        "description": "The LSP operation to perform.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Absolute or relative path to the source file. Required for symbols, definition, references, diagnostics.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Server identifier for start_server/stop_server/diagnostics. For symbols, optional symbol filter.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-based line number. Required for definition and references.",
                    },
                    "column": {
                        "type": "integer",
                        "description": "1-based column number. Required for definition and references.",
                    },
                    "server_command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command to start the LSP server (e.g. ['pyright-langserver', '--stdio']). Required for start_server.",
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": "Maximum time in milliseconds to wait for diagnostics after opening file (default: 10000, max: 30000). For diagnostics operation only.",
                    },
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        operation = args["operation"]  # type: str

        if operation == "start_server":
            return self._start_server(args, ctx)
        elif operation == "stop_server":
            return self._stop_server(args, ctx)
        elif operation == "symbols":
            return self._symbols(args, ctx)
        elif operation == "definition":
            return self._definition(args, ctx)
        elif operation == "references":
            return self._references(args, ctx)
        elif operation == "diagnostics":
            return self._diagnostics(args, ctx)
        else:
            raise ToolError("Unknown LSP operation: {}".format(operation))

    # ------------------------------------------------------------------
    # Operation: start_server
    # ------------------------------------------------------------------

    def _start_server(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        server_command = args.get("server_command")  # type: Optional[List[str]]
        server_id = args.get("query") or "default"  # type: str

        if server_command is None:
            raise ToolError("Parameter 'server_command' is required for operation 'start_server'.")

        # Check if already running
        with _servers_lock:
            if server_id in _servers:
                raise ToolError(
                    "LSP server '{}' is already running. Stop it first.".format(server_id)
                )

        # Determine workspace root for rootUri
        workspace = ctx.extra.get("workspace", ".") if ctx.extra else "."
        root_uri = "file:///" + os.path.abspath(workspace).replace("\\", "/")

        # Launch subprocess
        try:
            proc = subprocess.Popen(
                server_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            raise ToolError("Failed to start LSP server '{}': {}".format(server_command, e))

        server = _NotifyingLspServer(proc, server_id, root_uri)

        # Register diagnostics notification handler
        def _on_publish_diagnostics(params):
            # type: (Dict[str, Any]) -> None
            uri = params.get("uri", "")  # type: str
            diags = params.get("diagnostics", [])  # type: List[Dict[str, Any]]
            with _diagnostics_lock:
                _diagnostics[uri] = diags
            # Signal waiting thread
            with _diagnostics_events_lock:
                evt = _diagnostics_events.get(uri)
                if evt:
                    evt.set()

        server.register_notification_handler(
            "textDocument/publishDiagnostics", _on_publish_diagnostics
        )

        # Send initialize request
        init_params = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "didOpen": {},
                        "didClose": {},
                    },
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "documentSymbol": {
                        "dynamicRegistration": False,
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "diagnostic": {
                        "dynamicRegistration": False,
                        "relatedInformationSupport": True,
                    },
                },
            },
            "initializationOptions": {},
        }  # type: Dict[str, Any]

        try:
            server.send_request("initialize", init_params, timeout=60)
        except ToolError as e:
            proc.kill()
            raise ToolError("LSP initialize failed: {}".format(e))

        # Send initialized notification
        server.send_notification("initialized", {})

        # Register the server
        with _servers_lock:
            _servers[server_id] = server

        output = "LSP server '{}' started successfully.\nCommand: {}\nRoot URI: {}".format(
            server_id, server_command, root_uri
        )

        return ToolResult(
            title="LSP Server Started: {}".format(server_id),
            output=output,
            metadata={"server_id": server_id, "root_uri": root_uri},
        )

    # ------------------------------------------------------------------
    # Operation: stop_server
    # ------------------------------------------------------------------

    def _stop_server(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        server_id = args.get("query") or "default"  # type: str

        server = _get_server(server_id)

        server.stop()

        with _servers_lock:
            del _servers[server_id]

        output = "LSP server '{}' stopped successfully.".format(server_id)

        return ToolResult(
            title="LSP Server Stopped: {}".format(server_id),
            output=output,
            metadata={"server_id": server_id},
        )

    # ------------------------------------------------------------------
    # Operation: symbols
    # ------------------------------------------------------------------

    def _symbols(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args.get("file_path")  # type: Optional[str]
        server_id = args.get("query") or "default"  # type: str

        if file_path is None:
            raise ToolError("Parameter 'file_path' is required for operation 'symbols'.")

        server = _get_server(server_id)

        resolved_path = os.path.abspath(file_path)
        file_uri = "file:///" + resolved_path.replace("\\", "/")

        # Open the document (required before querying)
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                text = f.read()
        except IOError as e:
            raise ToolError("Cannot read file '{}': {}".format(resolved_path, e))

        server.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": self._guess_language_id(resolved_path),
                    "version": 1,
                    "text": text,
                }
            },
        )

        # Request document symbols
        result = server.send_request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": file_uri}},
            timeout=30,
        )

        # Format output
        output_lines = []  # type: List[str]
        output_lines.append("Document Symbols: {}".format(resolved_path))
        output_lines.append("")

        count = self._format_symbols(result, output_lines, indent=0)

        if count == 0:
            output_lines.append("  (no symbols found)")

        output = "\n".join(output_lines)

        return ToolResult(
            title="LSP Symbols: {}".format(os.path.basename(resolved_path)),
            output=output,
            metadata={"file_path": resolved_path, "symbol_count": count},
        )

    def _format_symbols(self, symbols, lines, indent):
        # type: (Any, List[str], int) -> int
        """Recursively format symbol list into output lines. Returns count."""
        if not symbols:
            return 0
        count = 0
        prefix = "  " * indent
        for sym in symbols:
            name = sym.get("name", "?")
            kind = sym.get("kind", 0)
            kind_name = self._symbol_kind_name(kind)
            lines.append("{}{} [{}]".format(prefix, name, kind_name))
            count += 1
            # Recurse into children
            children = sym.get("children", [])
            if children:
                count += self._format_symbols(children, lines, indent + 1)
        return count

    def _symbol_kind_name(self, kind):
        # type: (int) -> str
        """Map LSP SymbolKind integer to human-readable name."""
        kinds = {
            1: "File",
            2: "Module",
            3: "Namespace",
            4: "Package",
            5: "Class",
            6: "Method",
            7: "Property",
            8: "Field",
            9: "Constructor",
            10: "Enum",
            11: "Interface",
            12: "Function",
            13: "Variable",
            14: "Constant",
            15: "String",
            16: "Number",
            17: "Boolean",
            18: "Array",
            19: "Object",
            20: "Key",
            21: "Null",
            22: "EnumMember",
            23: "Struct",
            24: "Event",
            25: "Operator",
            26: "TypeParameter",
        }  # type: Dict[int, str]
        return kinds.get(kind, "Unknown({})".format(kind))

    def _guess_language_id(self, file_path):
        # type: (str) -> str
        """Guess the LSP languageId from file extension."""
        ext = os.path.splitext(file_path)[1].lower()
        mapping = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascriptreact",
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".json": "json",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".cs": "csharp",
            ".rb": "ruby",
            ".php": "php",
            ".html": "html",
            ".css": "css",
            ".md": "markdown",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".xml": "xml",
            ".sh": "shellscript",
            ".bat": "bat",
            ".ps1": "powershell",
        }  # type: Dict[str, str]
        return mapping.get(ext, "plaintext")

    # ------------------------------------------------------------------
    # Operation: definition
    # ------------------------------------------------------------------

    def _definition(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args.get("file_path")  # type: Optional[str]
        line = args.get("line")  # type: Optional[int]
        column = args.get("column")  # type: Optional[int]
        server_id = args.get("query") or "default"  # type: str

        if file_path is None:
            raise ToolError("Parameter 'file_path' is required for operation 'definition'.")
        if line is None:
            raise ToolError("Parameter 'line' is required for operation 'definition'.")
        if column is None:
            raise ToolError("Parameter 'column' is required for operation 'definition'.")

        server = _get_server(server_id)

        resolved_path = os.path.abspath(file_path)
        file_uri = "file:///" + resolved_path.replace("\\", "/")

        # Open the document
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                text = f.read()
        except IOError as e:
            raise ToolError("Cannot read file '{}': {}".format(resolved_path, e))

        server.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": self._guess_language_id(resolved_path),
                    "version": 1,
                    "text": text,
                }
            },
        )

        # Request definition (LSP uses 0-based line/column)
        result = server.send_request(
            "textDocument/definition",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line - 1, "character": column - 1},
            },
            timeout=30,
        )

        # Format output
        locations = self._normalize_locations(result)
        output_lines = []  # type: List[str]
        output_lines.append("Definition at {}:{}:{}".format(resolved_path, line, column))
        output_lines.append("")

        if not locations:
            output_lines.append("  (no definition found)")
        else:
            output_lines.append("Found {} location(s):".format(len(locations)))
            output_lines.append("")
            for loc in locations:
                output_lines.append(
                    "  {}:{}:{}".format(loc["uri"], loc["line"] + 1, loc["character"] + 1)
                )

        output = "\n".join(output_lines)

        return ToolResult(
            title="LSP Definition: {}:{}:{}".format(os.path.basename(resolved_path), line, column),
            output=output,
            metadata={
                "file_path": resolved_path,
                "line": line,
                "column": column,
                "locations": locations,
            },
        )

    # ------------------------------------------------------------------
    # Operation: references
    # ------------------------------------------------------------------

    def _references(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args.get("file_path")  # type: Optional[str]
        line = args.get("line")  # type: Optional[int]
        column = args.get("column")  # type: Optional[int]
        server_id = args.get("query") or "default"  # type: str

        if file_path is None:
            raise ToolError("Parameter 'file_path' is required for operation 'references'.")
        if line is None:
            raise ToolError("Parameter 'line' is required for operation 'references'.")
        if column is None:
            raise ToolError("Parameter 'column' is required for operation 'references'.")

        server = _get_server(server_id)

        resolved_path = os.path.abspath(file_path)
        file_uri = "file:///" + resolved_path.replace("\\", "/")

        # Open the document
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                text = f.read()
        except IOError as e:
            raise ToolError("Cannot read file '{}': {}".format(resolved_path, e))

        server.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": self._guess_language_id(resolved_path),
                    "version": 1,
                    "text": text,
                }
            },
        )

        # Request references
        result = server.send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line - 1, "character": column - 1},
                "context": {"includeDeclaration": True},
            },
            timeout=30,
        )

        # Format output
        locations = self._normalize_locations(result)
        output_lines = []  # type: List[str]
        output_lines.append("References at {}:{}:{}".format(resolved_path, line, column))
        output_lines.append("")

        if not locations:
            output_lines.append("  (no references found)")
        else:
            output_lines.append("Found {} reference(s):".format(len(locations)))
            output_lines.append("")
            for loc in locations:
                output_lines.append(
                    "  {}:{}:{}".format(loc["uri"], loc["line"] + 1, loc["character"] + 1)
                )

        output = "\n".join(output_lines)

        return ToolResult(
            title="LSP References: {}:{}:{}".format(os.path.basename(resolved_path), line, column),
            output=output,
            metadata={
                "file_path": resolved_path,
                "line": line,
                "column": column,
                "locations": locations,
            },
        )

    # ------------------------------------------------------------------
    # Operation: diagnostics
    # ------------------------------------------------------------------

    def _diagnostics(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        file_path = args.get("file_path")  # type: Optional[str]
        server_id = args.get("query") or "default"  # type: str
        wait_ms = args.get("wait_ms", 10000)  # type: int

        if file_path is None:
            raise ToolError("Parameter 'file_path' is required for operation 'diagnostics'.")

        # Clamp wait_ms: 1000-30000
        wait_ms = max(1000, min(30000, wait_ms))

        server = _get_server(server_id)

        resolved_path = os.path.abspath(file_path)
        file_uri = "file:///" + resolved_path.replace("\\", "/")

        # Open the document to trigger diagnostics (only if not already opened)
        if isinstance(server, _NotifyingLspServer) and not server.is_file_open(file_uri):
            try:
                with open(resolved_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except IOError as e:
                raise ToolError("Cannot read file '{}': {}".format(resolved_path, e))

            server.send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_uri,
                        "languageId": self._guess_language_id(resolved_path),
                        "version": 1,
                        "text": text,
                    }
                },
            )
            server.mark_file_open(file_uri)

        # Wait for diagnostics using event signaling
        evt = threading.Event()
        with _diagnostics_events_lock:
            _diagnostics_events[file_uri] = evt

        # Check if diagnostics already arrived
        with _diagnostics_lock:
            diags = list(_diagnostics.get(file_uri, []))

        if not diags:
            # Wait for notification (blocking with timeout)
            evt.wait(timeout=wait_ms / 1000.0)
            with _diagnostics_lock:
                diags = list(_diagnostics.get(file_uri, []))

        # Cleanup event
        with _diagnostics_events_lock:
            _diagnostics_events.pop(file_uri, None)

        # Format output
        output_lines = []  # type: List[str]
        output_lines.append("Diagnostics: {}".format(resolved_path))
        output_lines.append("")

        if not diags:
            output_lines.append("  (no diagnostics)")
        else:
            error_count = 0
            warning_count = 0
            info_count = 0
            hint_count = 0

            for d in diags:
                severity = d.get("severity", 0)
                severity_name = _SEVERITY_MAP.get(severity, "unknown")
                if severity == 1:
                    error_count += 1
                elif severity == 2:
                    warning_count += 1
                elif severity == 3:
                    info_count += 1
                elif severity == 4:
                    hint_count += 1

                range_info = d.get("range", {})
                start = range_info.get("start", {})
                line = start.get("line", 0) + 1  # Convert to 1-based
                character = start.get("character", 0) + 1

                message = d.get("message", "No message")
                source = d.get("source", "")
                code = d.get("code", "")

                line_str = "  [{}] {}:{}: {}".format(severity_name.upper(), line, character, message)
                if source:
                    line_str += " ({})".format(source)
                if code:
                    line_str += " [{}]".format(code)
                output_lines.append(line_str)

            output_lines.append("")
            output_lines.append("Summary: {} error(s), {} warning(s), {} info, {} hint(s)".format(
                error_count, warning_count, info_count, hint_count
            ))

        output = "\n".join(output_lines)

        return ToolResult(
            title="LSP Diagnostics: {}".format(os.path.basename(resolved_path)),
            output=output,
            metadata={
                "file_path": resolved_path,
                "diagnostics": diags,
                "count": len(diags),
            },
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _normalize_locations(self, result):
        # type: (Any) -> List[Dict[str, Any]]
        """Normalize LSP definition/references result to a list of location dicts.

        Handles:
        - None -> []
        - Single Location -> [loc]
        - List of Location -> list
        - LocationLink[] -> normalized list
        """
        if result is None:
            return []

        if isinstance(result, list):
            locations = []  # type: List[Dict[str, Any]]
            for item in result:
                loc = self._normalize_single_location(item)
                if loc:
                    locations.append(loc)
            return locations

        loc = self._normalize_single_location(result)
        return [loc] if loc else []

    def _normalize_single_location(self, item):
        # type: (Dict[str, Any]) -> Optional[Dict[str, Any]]
        """Normalize a single Location or LocationLink to a dict."""
        if not isinstance(item, dict):
            return None

        # LocationLink format (used by some servers for definition)
        if "targetUri" in item:
            target_range = item.get("targetSelectionRange", item.get("targetRange", {}))
            target_pos = target_range.get("start", {})
            return {
                "uri": item["targetUri"],
                "line": target_pos.get("line", 0),
                "character": target_pos.get("character", 0),
            }

        # Standard Location format
        if "uri" in item:
            pos = item.get("range", {}).get("start", {})
            return {
                "uri": item["uri"],
                "line": pos.get("line", 0),
                "character": pos.get("character", 0),
            }

        return None


# ---------------------------------------------------------------------------
# Registration Helper
# ---------------------------------------------------------------------------


def register_lsp_tool(registry):
    # type: (Any) -> None
    """Register the LSP tool with the given registry.

    Args:
        registry: A ToolRegistry instance to register the tool with.
    """
    registry.register(LspTool())
