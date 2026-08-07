"""

berserker.cli.serve — HTTP API server for berserker.



Provides a simplified REST API using Python's built-in http.server module.

Python 3.8.10 compatible: uses type comments, no match/case, no str.removeprefix().



Endpoints:

    GET  /health                    — Health check

    GET  /api/models                — List available models from provider registry

    POST /api/chat                  — Send chat message to provider

    GET  /api/sessions              — List sessions

    GET  /api/sessions/{id}         — Get session details

    POST /api/sessions/{id}/messages — Append message to session

"""



import json

import os

import re

from typing import Optional, List

from http.server import HTTPServer, BaseHTTPRequestHandler



from berserker.provider.registry import registry

from berserker.provider.base import ChatMessage, ProviderError

from berserker.session.manager import session_manager





def _json_response(handler, status_code, data):

    # type: (BaseHTTPRequestHandler, int, dict) -> None

    """Send a JSON response with the given status code and data."""

    body = json.dumps(data).encode("utf-8")

    handler.send_response(status_code)

    handler.send_header("Content-Type", "application/json")

    handler.send_header("Content-Length", str(len(body)))

    handler.end_headers()

    handler.wfile.write(body)





def _check_auth(handler):

    # type: (BaseHTTPRequestHandler) -> bool

    """Check Bearer token authentication. Returns True if auth passes or no password set."""

    password = os.environ.get("BERSERKER_SERVER_PASSWORD")

    if password is None:

        return True



    auth_header = handler.headers.get("Authorization", "")

    expected = "Bearer " + password

    if auth_header != expected:

        _json_response(

            handler,

            401,

            {"error": "Unauthorized", "message": "Invalid or missing authentication token"},

        )

        return False

    return True





def _read_body(handler):

    # type: (BaseHTTPRequestHandler) -> bytes

    """Read and return the request body."""

    content_length = int(handler.headers.get("Content-Length", 0))

    if content_length > 0:

        return handler.rfile.read(content_length)

    return b""





def _parse_json_body(handler):

    # type: (BaseHTTPRequestHandler) -> dict

    """Parse JSON body from request. Returns dict or raises ValueError."""

    body = _read_body(handler)

    if not body:

        raise ValueError("Request body is empty")

    return json.loads(body.decode("utf-8"))





# Regex for session path patterns

_SESSION_DETAIL_RE = re.compile(r"^/api/sessions/([^/]+)$")

_SESSION_MESSAGES_RE = re.compile(r"^/api/sessions/([^/]+)/messages$")





class ApiHandler(BaseHTTPRequestHandler):

    """HTTP request handler for the berserker API."""



    def log_message(self, format, *args):

        # type: (str, *object) -> None

        """Override to use a cleaner log format."""

        pass  # Suppress default logging; can be enabled if needed



    def do_GET(self):

        # type: () -> None

        """Handle GET requests."""

        try:

            self._handle_get()

        except Exception as exc:

            _json_response(self, 500, {"error": "Internal Server Error", "message": str(exc)})



    def do_POST(self):

        # type: () -> None

        """Handle POST requests."""

        try:

            self._handle_post()

        except Exception as exc:

            _json_response(self, 500, {"error": "Internal Server Error", "message": str(exc)})



    def _handle_get(self):

        # type: () -> None

        """Route GET requests to appropriate handlers."""

        path = self.path.split("?")[0]  # Strip query string



        if path == "/health":

            self._handle_health()

        elif path == "/api/models":

            if not _check_auth(self):

                return

            self._handle_list_models()

        elif path == "/api/sessions":

            if not _check_auth(self):

                return

            self._handle_list_sessions()

        else:

            # Check /api/sessions/{id}

            match = _SESSION_DETAIL_RE.match(path)

            if match:

                if not _check_auth(self):

                    return

                session_id = match.group(1)

                self._handle_get_session(session_id)

            else:

                _json_response(

                    self, 404, {"error": "Not Found", "message": "Route not found: " + path}

                )



    def _handle_post(self):

        # type: () -> None

        """Route POST requests to appropriate handlers."""

        path = self.path.split("?")[0]  # Strip query string



        if path == "/api/chat":

            if not _check_auth(self):

                return

            self._handle_chat()

        else:

            # Check /api/sessions/{id}/messages

            match = _SESSION_MESSAGES_RE.match(path)

            if match:

                if not _check_auth(self):

                    return

                session_id = match.group(1)

                self._handle_append_message(session_id)

            else:

                _json_response(

                    self, 404, {"error": "Not Found", "message": "Route not found: " + path}

                )



    # ------------------------------------------------------------------

    # Endpoint handlers

    # ------------------------------------------------------------------



    def _handle_health(self):

        # type: () -> None

        """GET /health — Health check endpoint."""

        _json_response(self, 200, {"status": "ok"})



    def _handle_list_models(self):

        # type: () -> None

        """GET /api/models — List all available models from provider registry."""

        try:

            models = registry.list_all_models()

            _json_response(self, 200, {"models": models})

        except Exception as exc:

            _json_response(self, 500, {"error": "Internal Server Error", "message": str(exc)})



    def _handle_chat(self):

        # type: () -> None

        """POST /api/chat — Send a chat message to a provider."""

        try:

            body = _parse_json_body(self)

        except (ValueError, KeyError) as exc:

            _json_response(self, 400, {"error": "Bad Request", "message": str(exc)})

            return



        message = body.get("message")

        if message is None:

            _json_response(

                self, 400, {"error": "Bad Request", "message": "Missing required field: 'message'"}

            )

            return



        session_id = body.get("session_id")

        provider_id = body.get("provider")

        model = body.get("model")



        try:

            # Resolve provider

            if provider_id is not None:

                provider = registry.get(provider_id)

            elif model is not None:

                provider, model = registry.get_provider_for_model(model)

            else:

                _json_response(

                    self,

                    400,

                    {

                        "error": "Bad Request",

                        "message": "Either 'provider' or 'model' must be specified",

                    },

                )

                return



            # Build messages list

            messages = []  # type: List[ChatMessage]

            if session_id is not None:

                # Load existing session messages

                session = session_manager.load(session_id)

                if session is not None and "messages" in session:

                    for msg in session["messages"]:

                        messages.append(ChatMessage(role=msg["role"], content=msg["content"], tool_call_id=msg.get("tool_call_id") or msg.get("tool_result_for")))



            # Add the new user message

            messages.append(ChatMessage(role="user", content=message))



            # Determine model to use

            if model is None:

                models = provider.list_models()

                if models:

                    model = models[0]

                else:

                    _json_response(

                        self,

                        400,

                        {

                            "error": "Bad Request",

                            "message": "No model specified and provider has no models",

                        },

                    )

                    return



            # Send to provider

            response = provider.chat(messages, model)



            # If session_id provided, save the assistant response

            if session_id is not None:

                session_manager.append_message(session_id, "assistant", response.content)



            _json_response(

                self,

                200,

                {

                    "id": response.id,

                    "model": response.model,

                    "content": response.content,

                    "usage": response.usage,

                    "finish_reason": response.finish_reason,

                },

            )



        except ProviderError as exc:

            _json_response(self, 400, {"error": "Provider Error", "message": str(exc)})

        except Exception as exc:

            _json_response(self, 500, {"error": "Internal Server Error", "message": str(exc)})



    def _handle_list_sessions(self):

        # type: () -> None

        """GET /api/sessions — List all sessions."""

        try:

            sessions = session_manager.list_sessions()

            _json_response(self, 200, {"sessions": sessions})

        except Exception as exc:

            _json_response(self, 500, {"error": "Internal Server Error", "message": str(exc)})



    def _handle_get_session(self, session_id):

        # type: (str) -> None

        """GET /api/sessions/{id} — Get session details."""

        try:

            session = session_manager.load(session_id)

            if session is None:

                _json_response(

                    self,

                    404,

                    {"error": "Not Found", "message": "Session '{}' not found".format(session_id)},

                )

                return

            _json_response(self, 200, {"session": session})

        except Exception as exc:

            _json_response(self, 500, {"error": "Internal Server Error", "message": str(exc)})



    def _handle_append_message(self, session_id):

        # type: (str) -> None

        """POST /api/sessions/{id}/messages — Append a message to a session."""

        try:

            body = _parse_json_body(self)

        except (ValueError, KeyError) as exc:

            _json_response(self, 400, {"error": "Bad Request", "message": str(exc)})

            return



        role = body.get("role", "user")

        content = body.get("content")

        if content is None:

            _json_response(

                self, 400, {"error": "Bad Request", "message": "Missing required field: 'content'"}

            )

            return



        try:

            message_id = session_manager.append_message(session_id, role, content)

            _json_response(self, 200, {"message_id": message_id, "session_id": session_id})

        except ValueError as exc:

            _json_response(self, 404, {"error": "Not Found", "message": str(exc)})

        except Exception as exc:

            _json_response(self, 500, {"error": "Internal Server Error", "message": str(exc)})





def start_server(host, port, password=None):

    # type: (str, int, Optional[str]) -> None

    """Start the HTTP API server and block.



    Args:

        host: Host address to bind to (e.g. '0.0.0.0', '127.0.0.1').

        port: Port number to listen on.

        password: Optional password for Bearer token authentication.

                  If provided, sets BERSERKER_SERVER_PASSWORD env var.

    """

    if password is not None:

        os.environ["BERSERKER_SERVER_PASSWORD"] = password



    server = HTTPServer((host, port), ApiHandler)

    print("Server starting on http://{}:{}".format(host, port))

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print("\nShutting down server...")

        server.server_close()

