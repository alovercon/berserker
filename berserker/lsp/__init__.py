"""
berserker.lsp — High-level LSP client with language detection, server lifecycle,
multi-file support, and diagnostics collection.

Built on top of berserker.tool.lsp for raw JSON-RPC communication.

Public API:
    LspClient(config=None)        — Full LSP client instance
    lsp_client                    — Module-level singleton
    detect_language(file_path)    — Map file extension to language string
    get_default_server_command(language) — Get default server command list

Python 3.8.10 compatible: type comments, no match/case, no str.removeprefix().
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import Dict, Any, Optional, List, Callable

# Import base LSP communication from existing tool
from berserker.tool.lsp import (
    _make_request,
    _make_notification,
    _encode_message,
    _read_message,
    _LspServer,
    ToolError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------

# Default mapping: file extension -> language string
_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".md": "markdown",
    ".sh": "shellscript",
    ".bash": "shellscript",
    ".bat": "bat",
    ".ps1": "powershell",
    ".lua": "lua",
    ".swift": "swift",
    ".kt": "kotlin",
    ".dart": "dart",
}  # type: Dict[str, str]

# Default LSP server commands per language
_DEFAULT_SERVER_COMMANDS = {
    "python": ["pylsp"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "go": ["gopls"],
    "rust": ["rust-analyzer"],
    "java": ["jdtls"],
    "c": ["clangd"],
    "cpp": ["clangd"],
    "csharp": ["omnisharp"],
    "ruby": ["solargraph", "stdio"],
    "php": ["intelephense", "--stdio"],
    "html": ["vscode-html-language-server", "--stdio"],
    "css": ["vscode-css-language-server", "--stdio"],
    "json": ["vscode-json-language-server", "--stdio"],
    "yaml": ["yaml-language-server", "--stdio"],
    "xml": ["lemminx"],
    "markdown": ["vscode-markdown-language-server", "--stdio"],
    "shellscript": ["bash-language-server", "start"],
    "bat": [],
    "powershell": ["powershell-editor-services", "-Stdio"],
    "lua": ["lua-language-server"],
    "swift": ["sourcekit-lsp"],
    "kotlin": ["kotlin-language-server"],
    "dart": ["dart", "language-server", "--protocol=lsp"],
    "plaintext": [],
}  # type: Dict[str, List[str]]

# Language ID mapping for LSP textDocument languageId field
_LANGUAGE_IDS = {
    "python": "python",
    "typescript": "typescript",
    "javascript": "javascript",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "csharp": "csharp",
    "ruby": "ruby",
    "php": "php",
    "html": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "xml": "xml",
    "markdown": "markdown",
    "shellscript": "shellscript",
    "bat": "bat",
    "powershell": "powershell",
    "lua": "lua",
    "swift": "swift",
    "kotlin": "kotlin",
    "dart": "dart",
    "plaintext": "plaintext",
}  # type: Dict[str, str]

# Diagnostic severity mapping: LSP severity -> human-readable string
_SEVERITY_MAP = {
    1: "error",
    2: "warning",
    3: "information",
    4: "hint",
}  # type: Dict[int, str]


def detect_language(file_path):
    # type: (str) -> str
    """Detect language string from file extension.

    Args:
        file_path: Absolute or relative file path.

    Returns:
        Language string (e.g. 'python', 'typescript'), or 'plaintext' if unknown.
    """
    ext = os.path.splitext(file_path)[1].lower()
    return _EXTENSION_TO_LANGUAGE.get(ext, "plaintext")


def get_default_server_command(language):
    # type: (str) -> List[str]
    """Get the default LSP server command list for a language.

    Args:
        language: Language string (e.g. 'python', 'typescript').

    Returns:
        List of command strings, or empty list if no default.
    """
    return list(_DEFAULT_SERVER_COMMANDS.get(language, []))


def get_language_id(language):
    # type: (str) -> str
    """Get the LSP languageId string for a language.

    Args:
        language: Language string.

    Returns:
        LSP languageId (e.g. 'python', 'typescript').
    """
    return _LANGUAGE_IDS.get(language, "plaintext")


# ---------------------------------------------------------------------------
# _NotifyingLspServer — Server wrapper with background notification reader
# ---------------------------------------------------------------------------


class _NotifyingLspServer(object):
    """LSP server wrapper with background notification reader thread.

    Extends the basic _LspServer pattern by continuously reading messages
    from stdout in a background thread, dispatching notifications to
    registered handlers, and making responses available to the request thread.
    """

    def __init__(self, proc, server_id, root_uri):
        # type: (subprocess.Popen, str, str) -> None
        self.proc = proc  # type: subprocess.Popen
        self.server_id = server_id  # type: str
        self.root_uri = root_uri  # type: str
        self._next_id = 1  # type: int
        self._lock = threading.Lock()
        self._pending = {}  # type: Dict[int, threading.Event]
        self._responses = {}  # type: Dict[int, Dict[str, Any]]
        self._notification_handlers = {}  # type: Dict[str, List[Callable[[Dict[str, Any]], None]]]
        self._stop_event = threading.Event()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="lsp-reader-{}".format(server_id), daemon=True
        )
        self._reader_thread.start()

    def _reader_loop(self):
        # type: () -> None
        """Background loop that reads messages and dispatches them."""
        while not self._stop_event.is_set():
            try:
                msg = _read_message(self.proc, timeout=1)
                if msg is None:
                    continue
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
            except (ToolError, IOError, OSError):
                if self._stop_event.is_set():
                    break
                continue
            except Exception:
                logger.exception("Error in LSP reader loop for %s", self.server_id)
                if self._stop_event.is_set():
                    break
                continue

    def register_notification_handler(self, method, handler):
        # type: (str, Callable[[Dict[str, Any]], None]) -> None
        """Register a callback for a notification method.

        Args:
            method: Notification method name (e.g. 'textDocument/publishDiagnostics').
            handler: Callback function that takes params dict.
        """
        with self._lock:
            if method not in self._notification_handlers:
                self._notification_handlers[method] = []
            self._notification_handlers[method].append(handler)

    def unregister_notification_handler(self, method, handler):
        # type: (str, Callable[[Dict[str, Any]], None]) -> None
        """Unregister a notification handler."""
        with self._lock:
            if method in self._notification_handlers:
                try:
                    self._notification_handlers[method].remove(handler)
                except ValueError:
                    pass

    def _dispatch_notification(self, method, params):
        # type: (str, Dict[str, Any]) -> None
        """Call all registered handlers for a notification method."""
        with self._lock:
            handlers = list(self._notification_handlers.get(method, []))
        for handler in handlers:
            try:
                handler(params)
            except Exception:
                logger.exception(
                    "Error in notification handler for %s on %s", method, self.server_id
                )

    def send_request(self, method, params, timeout=30):
        # type: (str, Dict[str, Any], int) -> Dict[str, Any]
        """Send a JSON-RPC request and wait for the response.

        Returns the 'result' field from the response.
        Raises ToolError on error response or communication failure.
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

    def send_notification(self, method, params):
        # type: (str, Dict[str, Any]) -> None
        """Send a JSON-RPC notification (no response expected)."""
        msg = _make_notification(method, params)
        with self._lock:
            if self.proc.stdin is None:
                raise ToolError("LSP server stdin is not available")
            self.proc.stdin.write(_encode_message(msg))
            self.proc.stdin.flush()

    def stop(self):
        # type: () -> None
        """Gracefully shutdown the LSP server and stop the reader thread."""
        self._stop_event.set()
        try:
            self.send_notification("shutdown", {})
            self.send_notification("exit", {})
        except (ToolError, IOError, OSError):
            pass  # Server may already be dead
        try:
            self.proc.terminate()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            self.proc.kill()
        # Wait for reader thread to finish
        self._reader_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Global registries (thread-safe)
# ---------------------------------------------------------------------------

_servers = {}  # type: Dict[str, _NotifyingLspServer]
_servers_lock = threading.Lock()

_diagnostics = {}  # type: Dict[str, List[Dict[str, Any]]]  # file_uri -> diagnostics
_diagnostics_lock = threading.Lock()

_file_versions = {}  # type: Dict[str, int]  # file_path -> version
_file_versions_lock = threading.Lock()


# ---------------------------------------------------------------------------
# LspClient
# ---------------------------------------------------------------------------


class LspClient(object):
    """High-level LSP client with language detection, server lifecycle,
    multi-file support, and diagnostics collection.

    Usage:
        client = LspClient()
        client.start_server("python", "/path/to/project")
        client.did_open("/path/to/file.py", source_text)
        diags = client.get_diagnostics("/path/to/file.py")
        client.did_close("/path/to/file.py")
        client.stop_server("python")

    Args:
        config: Optional config dict. If provided, reads 'lsp.servers' to
            override default server commands. Expected format:
            {"lsp": {"servers": {"python": ["pyright-langserver", "--stdio"]}}}
    """

    def __init__(self, config=None):
        # type: (Optional[Dict[str, Any]]) -> None
        self._config = config or {}  # type: Dict[str, Any]
        self._server_commands = self._load_server_commands()  # type: Dict[str, List[str]]
        self._started = False  # type: bool

    def _load_server_commands(self):
        # type: () -> Dict[str, List[str]]
        """Load server commands from config, falling back to defaults."""
        commands = {}  # type: Dict[str, List[str]]
        # Start with defaults
        for lang, cmd in _DEFAULT_SERVER_COMMANDS.items():
            commands[lang] = list(cmd)
        # Override with config
        lsp_config = self._config.get("lsp", {})  # type: Dict[str, Any]
        servers_config = lsp_config.get("servers", {})  # type: Dict[str, Any]
        for lang, cmd in servers_config.items():
            if isinstance(cmd, list):
                commands[str(lang)] = list(cmd)
            elif isinstance(cmd, str):
                commands[str(lang)] = [cmd]
        return commands

    def get_server_command(self, language):
        # type: (str) -> List[str]
        """Get the configured server command for a language.

        Args:
            language: Language string.

        Returns:
            List of command strings, or empty list if not configured.
        """
        return list(self._server_commands.get(language, []))

    def set_server_command(self, language, command):
        # type: (str, List[str]) -> None
        """Set or override the server command for a language.

        Args:
            language: Language string.
            command: List of command strings.
        """
        self._server_commands[language] = list(command)

    def detect_language(self, file_path):
        # type: (str) -> str
        """Detect language from file extension.

        Args:
            file_path: File path (absolute or relative).

        Returns:
            Language string.
        """
        return detect_language(file_path)

    def start_server(self, language, workspace_root):
        # type: (str, str) -> Dict[str, Any]
        """Start an LSP server for the given language.

        Auto-detects the server command, launches the process, sends
        initialize request, and starts the notification reader.

        Args:
            language: Language string (e.g. 'python', 'typescript').
            workspace_root: Absolute path to the workspace/project root.

        Returns:
            Dict with 'server_id', 'root_uri', 'command' on success.

        Raises:
            ToolError: If server is already running or fails to start.
        """
        # Check if already running
        with _servers_lock:
            if language in _servers:
                raise ToolError(
                    "LSP server for '{}' is already running. Stop it first.".format(language)
                )

        # Get server command
        command = self.get_server_command(language)
        if not command:
            logger.warning("No LSP server command configured for language '%s', skipping", language)
            return {"server_id": language, "status": "skipped", "reason": "no_command"}

        # Resolve workspace root
        abs_root = os.path.abspath(workspace_root)
        root_uri = "file:///" + abs_root.replace("\\", "/")

        # Launch subprocess
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            logger.warning(
                "Failed to start LSP server for '%s' (command: %s): %s", language, command, e
            )
            return {"server_id": language, "status": "failed", "reason": str(e)}

        server = _NotifyingLspServer(proc, language, root_uri)

        # Register diagnostics notification handler
        server.register_notification_handler(
            "textDocument/publishDiagnostics",
            self._on_publish_diagnostics,
        )

        # Send initialize request
        init_params = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "didOpen": {},
                        "didChange": {"dynamicRegistration": False},
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
                        "relatedDocumentSupport": False,
                    },
                },
                "workspace": {
                    "didChangeConfiguration": {"dynamicRegistration": False},
                },
            },
            "initializationOptions": {},
        }  # type: Dict[str, Any]

        try:
            server.send_request("initialize", init_params, timeout=60)
        except ToolError as e:
            proc.kill()
            logger.warning("LSP initialize failed for '%s': %s", language, e)
            return {"server_id": language, "status": "failed", "reason": str(e)}

        # Send initialized notification
        server.send_notification("initialized", {})

        # Register the server
        with _servers_lock:
            _servers[language] = server

        self._started = True
        logger.info("LSP server '%s' started: %s", language, command)

        return {
            "server_id": language,
            "root_uri": root_uri,
            "command": command,
            "status": "started",
        }

    def stop_server(self, language):
        # type: (str) -> Dict[str, Any]
        """Stop an LSP server for the given language.

        Sends shutdown + exit notifications, terminates the process,
        and cleans up the reader thread.

        Args:
            language: Language string.

        Returns:
            Dict with 'server_id' and 'status'.
        """
        server = self.get_server(language)
        if server is None:
            logger.warning("LSP server for '%s' is not running", language)
            return {"server_id": language, "status": "not_running"}

        server.stop()

        with _servers_lock:
            _servers.pop(language, None)

        # Clean up diagnostics for files served by this server
        # (We keep them for now as they may be useful, but clear on restart)

        logger.info("LSP server '%s' stopped", language)
        return {"server_id": language, "status": "stopped"}

    def restart_server(self, language, workspace_root):
        # type: (str, str) -> Dict[str, Any]
        """Restart an LSP server for the given language.

        Stops the existing server (if running) and starts a new one.

        Args:
            language: Language string.
            workspace_root: Absolute path to the workspace/project root.

        Returns:
            Dict with server info from start_server.
        """
        # Stop existing server if running
        existing = self.get_server(language)
        if existing is not None:
            self.stop_server(language)

        # Clear diagnostics for this language's files
        with _diagnostics_lock:
            keys_to_remove = []
            for key in _diagnostics:
                # Diagnostics are keyed by URI, we keep them as-is
                pass
            # Actually, we clear all diagnostics on restart
            _diagnostics.clear()

        # Clear file versions
        with _file_versions_lock:
            _file_versions.clear()

        return self.start_server(language, workspace_root)

    def get_server(self, language):
        # type: (str) -> Optional[_NotifyingLspServer]
        """Get a running LSP server by language.

        Args:
            language: Language string.

        Returns:
            _NotifyingLspServer instance, or None if not running.
        """
        with _servers_lock:
            return _servers.get(language)

    def did_open(self, file_path, text):
        # type: (str, str) -> Dict[str, Any]
        """Notify the LSP server that a file has been opened.

        Sends textDocument/didOpen notification.

        Args:
            file_path: Absolute or relative file path.
            text: Full file content.

        Returns:
            Dict with 'file_uri', 'language_id', 'version'.

        Raises:
            ToolError: If no server is running for the file's language.
        """
        resolved_path = os.path.abspath(file_path)
        language = self.detect_language(resolved_path)
        server = self.get_server(language)

        if server is None:
            logger.warning(
                "No LSP server running for language '%s' (file: %s), skipping did_open",
                language,
                resolved_path,
            )
            return {
                "file_uri": self._path_to_uri(resolved_path),
                "status": "skipped",
                "reason": "no_server",
            }

        file_uri = self._path_to_uri(resolved_path)

        # Track version
        with _file_versions_lock:
            version = _file_versions.get(resolved_path, 0) + 1
            _file_versions[resolved_path] = version

        server.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": get_language_id(language),
                    "version": version,
                    "text": text,
                }
            },
        )

        return {
            "file_uri": file_uri,
            "language_id": get_language_id(language),
            "version": version,
            "status": "opened",
        }

    def did_change(self, file_path, text, version=None):
        # type: (str, str, Optional[int]) -> Dict[str, Any]
        """Notify the LSP server that a file has changed.

        Sends textDocument/didChange notification.

        Args:
            file_path: Absolute or relative file path.
            text: New full file content.
            version: Optional version number. If None, auto-increments.

        Returns:
            Dict with 'file_uri', 'version'.

        Raises:
            ToolError: If no server is running for the file's language.
        """
        resolved_path = os.path.abspath(file_path)
        language = self.detect_language(resolved_path)
        server = self.get_server(language)

        if server is None:
            logger.warning(
                "No LSP server running for language '%s' (file: %s), skipping did_change",
                language,
                resolved_path,
            )
            return {
                "file_uri": self._path_to_uri(resolved_path),
                "status": "skipped",
                "reason": "no_server",
            }

        file_uri = self._path_to_uri(resolved_path)

        # Track version
        with _file_versions_lock:
            if version is not None:
                _file_versions[resolved_path] = version
            else:
                current = _file_versions.get(resolved_path, 0)
                version = current + 1
                _file_versions[resolved_path] = version

        server.send_notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": file_uri, "version": version},
                "contentChanges": [{"text": text}],
            },
        )

        return {
            "file_uri": file_uri,
            "version": version,
            "status": "changed",
        }

    def did_close(self, file_path):
        # type: (str) -> Dict[str, Any]
        """Notify the LSP server that a file has been closed.

        Sends textDocument/didClose notification.

        Args:
            file_path: Absolute or relative file path.

        Returns:
            Dict with 'file_uri', 'status'.
        """
        resolved_path = os.path.abspath(file_path)
        language = self.detect_language(resolved_path)
        server = self.get_server(language)

        if server is None:
            logger.warning(
                "No LSP server running for language '%s' (file: %s), skipping did_close",
                language,
                resolved_path,
            )
            return {
                "file_uri": self._path_to_uri(resolved_path),
                "status": "skipped",
                "reason": "no_server",
            }

        file_uri = self._path_to_uri(resolved_path)

        server.send_notification(
            "textDocument/didClose",
            {"textDocument": {"uri": file_uri}},
        )

        # Clean up version tracking
        with _file_versions_lock:
            _file_versions.pop(resolved_path, None)

        return {
            "file_uri": file_uri,
            "status": "closed",
        }

    def get_diagnostics(self, file_path):
        # type: (str) -> List[Dict[str, Any]]
        """Get diagnostics for a file.

        Args:
            file_path: Absolute or relative file path.

        Returns:
            List of diagnostic dicts with keys:
                - severity: 'error', 'warning', 'information', 'hint'
                - message: Diagnostic message string
                - line: 1-based line number
                - column: 1-based column number
                - end_line: 1-based end line (optional)
                - end_column: 1-based end column (optional)
                - source: Diagnostic source (e.g. 'pylsp', 'pyright')
        """
        resolved_path = os.path.abspath(file_path)
        file_uri = self._path_to_uri(resolved_path)

        with _diagnostics_lock:
            raw_diags = _diagnostics.get(file_uri, [])

        result = []  # type: List[Dict[str, Any]]
        for diag in raw_diags:
            range_ = diag.get("range", {})  # type: Dict[str, Any]
            start = range_.get("start", {})  # type: Dict[str, Any]
            end = range_.get("end", {})  # type: Dict[str, Any]
            severity_code = diag.get("severity", 1)  # type: int

            entry = {
                "severity": _SEVERITY_MAP.get(severity_code, "error"),
                "message": diag.get("message", ""),
                "line": start.get("line", 0) + 1,  # Convert 0-based to 1-based
                "column": start.get("character", 0) + 1,
                "source": diag.get("source", ""),
            }  # type: Dict[str, Any]

            # Optional end position
            if end:
                entry["end_line"] = end.get("line", 0) + 1
                entry["end_column"] = end.get("character", 0) + 1

            # Optional code
            if "code" in diag:
                entry["code"] = diag["code"]

            result.append(entry)

        return result

    def get_all_diagnostics(self):
        # type: () -> Dict[str, List[Dict[str, Any]]]
        """Get diagnostics for all tracked files.

        Returns:
            Dict mapping file URIs to lists of diagnostic dicts.
        """
        with _diagnostics_lock:
            result = {}  # type: Dict[str, List[Dict[str, Any]]]
            for uri, diags in _diagnostics.items():
                result[uri] = list(diags)
            return result

    def clear_diagnostics(self, file_path=None):
        # type: (Optional[str]) -> None
        """Clear diagnostics for a file or all files.

        Args:
            file_path: If provided, clear only this file. Otherwise clear all.
        """
        with _diagnostics_lock:
            if file_path is not None:
                resolved_path = os.path.abspath(file_path)
                file_uri = self._path_to_uri(resolved_path)
                _diagnostics.pop(file_uri, None)
            else:
                _diagnostics.clear()

    def _on_publish_diagnostics(self, params):
        # type: (Dict[str, Any]) -> None
        """Handle textDocument/publishDiagnostics notification.

        Called by the background reader thread when diagnostics arrive.
        """
        uri = params.get("uri", "")  # type: str
        diagnostics = params.get("diagnostics", [])  # type: List[Dict[str, Any]]

        with _diagnostics_lock:
            _diagnostics[uri] = list(diagnostics)

        logger.debug("Received %d diagnostics for %s", len(diagnostics), uri)

    def _path_to_uri(self, file_path):
        # type: (str) -> str
        """Convert a file path to a file:// URI."""
        abs_path = os.path.abspath(file_path)
        return "file:///" + abs_path.replace("\\", "/")

    def stop_all(self):
        # type: () -> Dict[str, Any]
        """Stop all running LSP servers.

        Returns:
            Dict mapping language to stop status.
        """
        results = {}  # type: Dict[str, Any]
        with _servers_lock:
            languages = list(_servers.keys())

        for language in languages:
            results[language] = self.stop_server(language)

        self._started = False
        return results

    @property
    def is_running(self):
        # type: () -> bool
        """Check if any LSP servers are currently running."""
        with _servers_lock:
            return len(_servers) > 0

    @property
    def running_servers(self):
        # type: () -> List[str]
        """Get list of languages with running servers."""
        with _servers_lock:
            return list(_servers.keys())


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

lsp_client = LspClient()  # type: LspClient


def configure(config):
    # type: (Dict[str, Any]) -> LspClient
    """Configure and return a new LspClient instance.

    Args:
        config: Config dict with 'lsp.servers' overrides.

    Returns:
        New LspClient instance.
    """
    global lsp_client
    lsp_client = LspClient(config)
    return lsp_client
