"""
Google Gemini provider adapter for berserker.

Implements the Provider ABC using the google-generativeai SDK.
Supports Gemini series models (gemini-2.5-pro, gemini-2.0-flash, gemini-1.5-pro, etc.).

NOTE: This provider is optional. The file is importable even without
google-generativeai installed, but instantiation will raise ImportError.

Python 3.8.10 compatible: uses type comments, Optional, no match/case.
"""

import os
import time
import json
import logging
from typing import List, Iterator, Optional, Dict, Any

from berserker.provider.base import (
    Provider,
    ChatMessage,
    ChatResponse,
    AuthenticationError,
    RateLimitError,
    APIError,
    ModelNotFoundError,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conditional SDK Import
# ---------------------------------------------------------------------------

# Try google-genai first (newer SDK), fallback to google-generativeai
_GenerativeModel = None  # type: Optional[type]
_GenAIError = None  # type: Optional[type]
_genai_module = None  # type: Optional[Any]
_genai_types = None  # type: Optional[Any]
_use_new_sdk = False  # type: bool

try:
    # Try the newer google-genai SDK first
    from google import genai as _genai_module_new  # type: ignore
    from google.genai import types as _gt  # type: ignore

    _genai_types = _gt

    _use_new_sdk = True
    _genai_module = _genai_module_new
    _GenAIError = Exception  # type: ignore[assignment]
except ImportError:
    try:
        # Fallback to google-generativeai (older but py38-compatible)
        import google.generativeai as genai  # type: ignore

        _genai_module = genai
        _GenerativeModel = genai.GenerativeModel  # type: ignore
        _GenAIError = Exception  # type: ignore[assignment]
    except ImportError:
        _genai_module = None
        _GenerativeModel = None
        _GenAIError = None


def _ensure_sdk_available():
    # type: () -> None
    """Raise ImportError if the Google AI SDK is not installed."""
    if _genai_module is None:
        raise ImportError(
            "Google provider requires 'google-generativeai' or 'google-genai' SDK. "
            "Install with: pip install google-generativeai>=0.5.0 "
            "(or pip install google-genai for the newer SDK)"
        )


# ---------------------------------------------------------------------------
# Error Mapping Helpers
# ---------------------------------------------------------------------------


def _map_google_error(exc, model=None):
    # type: (Exception, Optional[str]) -> Exception
    """Map Google SDK exceptions to our error hierarchy.

    Args:
        exc: The original exception from the Google SDK.
        model: Optional model name for ModelNotFoundError context.

    Returns:
        A mapped exception from our error hierarchy.
    """
    exc_str = str(exc)
    exc_type = type(exc).__name__

    # Check for authentication errors (401)
    if "401" in exc_str or "API_KEY" in exc_str.upper() or "UNAUTHENTICATED" in exc_type.upper():
        return AuthenticationError("Google API authentication failed: {}".format(exc_str))

    # Check for rate limit errors (429)
    if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_type.upper():
        retry_after = None
        # Try to extract retry-after from the error if available
        if hasattr(exc, "response") and getattr(exc, "response", None) is not None:
            retry_after = exc.response.headers.get("Retry-After")  # type: ignore[union-attr]
            if retry_after is not None:
                try:
                    retry_after = float(retry_after)
                except (ValueError, TypeError):
                    retry_after = None
        return RateLimitError(
            "Google API rate limit exceeded: {}".format(exc_str),
            retry_after=retry_after,
        )

    # Check for model not found (404)
    if "404" in exc_str or "NOT_FOUND" in exc_type.upper():
        model_info = " (model: {})".format(model) if model else ""
        return ModelNotFoundError("Google model not found{}: {}".format(model_info, exc_str))

    # Check for server errors (5xx)
    if "500" in exc_str or "502" in exc_str or "503" in exc_str or "504" in exc_str:
        status_code = None
        for code in [500, 502, 503, 504]:
            if str(code) in exc_str:
                status_code = code
                break
        return APIError(
            "Google API server error: {}".format(exc_str),
            status_code=status_code,
            response_body=exc_str,
        )

    # Default: generic API error
    return APIError(
        "Google API error: {}".format(exc_str),
        response_body=exc_str,
    )


# ---------------------------------------------------------------------------
# Message Conversion Helpers
# ---------------------------------------------------------------------------


def _convert_messages_to_google_format(messages, system_instruction=None):
    # type: (List[ChatMessage], Optional[str]) -> Dict[str, Any]
    """Convert OpenAI-style ChatMessage list to Google API format.

    Google's API expects:
    - contents: list of {role: "user"|"model", parts: [...]}
    - system_instruction: optional {parts: [{text: "..."}]}
    - tools: optional list of tool declarations

    Args:
        messages: List of ChatMessage objects.
        system_instruction: Optional pre-extracted system prompt.

    Returns:
        Dict with 'contents' and optionally 'system_instruction', 'tools'.
    """
    contents = []  # type: List[Dict[str, Any]]
    tools = []  # type: List[Dict[str, Any]]
    seen_tool_names = set()  # type: set

    for msg in messages:
        if msg.role == "system":
            # System messages are handled separately via system_instruction
            if system_instruction is None:
                system_instruction = msg.content
            else:
                system_instruction += "\n" + msg.content
            continue

        if msg.role == "user":
            parts = [{"text": msg.content}] if msg.content else []
            contents.append({"role": "user", "parts": parts})

        elif msg.role == "assistant":
            parts = []  # type: List[Dict[str, Any]]

            # Add text content if present
            if msg.content:
                parts.append({"text": msg.content})

            # Add function_call parts for tool_calls
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    # tc is typically: {"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}
                    func = tc.get("function", {})  # type: Dict[str, Any]
                    func_name = func.get("name", "")
                    func_args = func.get("arguments", "{}")

                    # Parse arguments string to dict if needed
                    if isinstance(func_args, str):
                        try:
                            func_args = json.loads(func_args)
                        except (json.JSONDecodeError, ValueError):
                            func_args = {}

                    parts.append(
                        {
                            "function_call": {
                                "name": func_name,
                                "args": func_args,
                            }
                        }
                    )

                    # Track tool names for tool declarations
                    if func_name and func_name not in seen_tool_names:
                        seen_tool_names.add(func_name)

            if not parts:
                # Ensure at least one part exists
                parts.append({"text": ""})

            contents.append({"role": "model", "parts": parts})

        elif msg.role == "tool":
            # Tool response → function_response part
            # tool_call_id links to the original function_call
            func_name = msg.name or "unknown"
            try:
                response_data = json.loads(msg.content) if msg.content else {}
            except (json.JSONDecodeError, ValueError):
                response_data = {"result": msg.content}

            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": func_name,
                                "response": response_data,
                            }
                        }
                    ],
                }
            )

    # Build tool declarations from seen function names
    if seen_tool_names:
        tool_declarations = []  # type: List[Dict[str, Any]]
        for name in seen_tool_names:
            tool_declarations.append(
                {
                    "name": name,
                    "description": "Tool: {}".format(name),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {},
                    },
                }
            )
        tools.append({"function_declarations": tool_declarations})

    result = {"contents": contents}  # type: Dict[str, Any]
    if system_instruction:
        result["system_instruction"] = {"parts": [{"text": system_instruction}]}
    if tools:
        result["tools"] = tools

    return result


def _extract_system_messages(messages):
    # type: (List[ChatMessage]) -> Optional[str]
    """Extract and combine all system messages from the message list.

    Args:
        messages: List of ChatMessage objects.

    Returns:
        Combined system prompt string, or None if no system messages.
    """
    system_parts = []  # type: List[str]
    for msg in messages:
        if msg.role == "system" and msg.content:
            system_parts.append(msg.content)
    if system_parts:
        return "\n".join(system_parts)
    return None


def _filter_non_system_messages(messages):
    # type: (List[ChatMessage]) -> List[ChatMessage]
    """Return messages excluding system messages (handled separately).

    Args:
        messages: List of ChatMessage objects.

    Returns:
        Filtered list without system messages.
    """
    return [msg for msg in messages if msg.role != "system"]


# ---------------------------------------------------------------------------
# GoogleProvider Class
# ---------------------------------------------------------------------------

# Default Gemini models
DEFAULT_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.0-pro",
]


def _map_tools(tools):
    # type: (List[Any]) -> List[Dict[str, Any]]
    """Convert Tool objects to Google tool schema format.

    Google format: {"name", "description", "parameters"}

    Args:
        tools: List of Tool objects from ToolRegistry.

    Returns:
        List of Google-compatible tool schema dicts.
    """
    result = []  # type: List[Dict[str, Any]]
    for tool in tools:
        result.append(
            {
                "name": tool.id,
                "description": tool.description,
                "parameters": tool.parameters,
            }
        )
    return result


class GoogleProvider(Provider):
    """Google Gemini provider implementation.

    Uses the google-generativeai SDK (or google-genai if available)
    to interact with Google's Gemini models.

    Attributes:
        api_key: Google API key (from constructor or GOOGLE_API_KEY env var).
    """

    def __init__(self, id=None, api_key=None, timeout=60, max_retries=3):
        # type: (Optional[str], Optional[str], int, int) -> None
        """Initialize the Google Gemini provider.

        Args:
            id: Unique provider identifier (default: "google").
            api_key: Google API key. If None, reads from GOOGLE_API_KEY env var.
            timeout: Request timeout in seconds (default: 60).
            max_retries: Maximum retry attempts (default: 3).

        Raises:
            ImportError: If the Google AI SDK is not installed.
        """
        _ensure_sdk_available()

        provider_id = id if id is not None else "google"
        super(GoogleProvider, self).__init__(
            id=provider_id,
            name="Google Gemini",
            models=list(DEFAULT_MODELS),
            timeout=timeout,
            max_retries=max_retries,
        )

        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        if not self.api_key:
            raise AuthenticationError(
                "Google API key is required. "
                "Pass api_key to constructor or set GOOGLE_API_KEY environment variable."
            )

        # Initialize the SDK with the API key
        self._configure_sdk()

    def _configure_sdk(self):
        # type: () -> None
        """Configure the Google AI SDK with the API key."""
        if _use_new_sdk:
            # google-genai SDK
            self._client = _genai_module.Client(api_key=self.api_key)  # type: ignore
        else:
            # google-generativeai SDK
            _genai_module.configure(api_key=self.api_key)  # type: ignore

    def _get_model(self, model_name):
        # type: (str) -> Any
        """Get a GenerativeModel instance for the given model name.

        Args:
            model_name: The model identifier (e.g., "gemini-2.0-flash").

        Returns:
            A GenerativeModel instance configured for the model.
        """
        if _use_new_sdk:
            # google-genai: model is accessed via client methods directly
            return model_name
        else:
            # google-generativeai: use GenerativeModel class
            return _GenerativeModel(model_name)  # type: ignore

    @retry_with_backoff()
    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Send a chat completion request to Google Gemini.

        Args:
            messages: List of ChatMessage objects forming the conversation.
            model: Model identifier to use (e.g., "gemini-2.0-flash").
            **options: Additional options (temperature, top_p, max_tokens, etc.).

        Returns:
            ChatResponse with the generated content and metadata.

        Raises:
            AuthenticationError: If API key is invalid.
            RateLimitError: If rate limit is exceeded.
            ModelNotFoundError: If the model doesn't exist.
            APIError: For other API errors.
        """
        _ensure_sdk_available()

        try:
            # Extract system messages (handled separately)
            system_instruction = _extract_system_messages(messages)
            non_system_messages = _filter_non_system_messages(messages)

            # Build generation config from options
            generation_config = {}  # type: Dict[str, Any]
            if "temperature" in options:
                generation_config["temperature"] = options["temperature"]
            if "top_p" in options:
                generation_config["top_p"] = options["top_p"]
            if "max_tokens" in options:
                generation_config["max_output_tokens"] = options["max_tokens"]
            if "stop_sequences" in options:
                generation_config["stop_sequences"] = options["stop_sequences"]

            if _use_new_sdk:
                return self._chat_new_sdk(
                    model, non_system_messages, system_instruction, generation_config, options
                )
            else:
                return self._chat_legacy_sdk(
                    model, non_system_messages, system_instruction, generation_config, options
                )

        except AuthenticationError:
            raise
        except RateLimitError:
            raise
        except ModelNotFoundError:
            raise
        except APIError:
            raise
        except Exception as exc:
            raise _map_google_error(exc, model=model)

    def _chat_new_sdk(self, model, messages, system_instruction, generation_config, options):
        # type: (str, List[ChatMessage], Optional[str], Dict[str, Any], Dict[str, Any]) -> ChatResponse
        """Chat using the newer google-genai SDK.

        Args:
            model: Model identifier.
            messages: Non-system messages.
            system_instruction: Combined system prompt.
            generation_config: Generation parameters.
            options: Additional options.

        Returns:
            ChatResponse with generated content.
        """
        assert _genai_types is not None  # Guaranteed by _use_new_sdk check
        # Build contents list
        contents = []  # type: List[Any]
        for msg in messages:
            if msg.role == "user":
                parts = [{"text": msg.content}] if msg.content else []
                contents.append(
                    _genai_types.Content(
                        role="user", parts=[_genai_types.Part.from_text(text=msg.content)]
                    )  # type: ignore
                )
            elif msg.role == "assistant":
                parts = []  # type: list
                if msg.content:
                    parts.append(_genai_types.Part.from_text(text=msg.content))  # type: ignore
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        func = tc.get("function", {})
                        parts.append(
                            _genai_types.Part.from_function_call(  # type: ignore
                                name=func.get("name", ""),
                                args=_parse_json_safe(func.get("arguments", "{}")),
                            )
                        )
                if not parts:
                    parts.append(_genai_types.Part.from_text(text=""))  # type: ignore
                contents.append(
                    _genai_types.Content(role="model", parts=parts)  # type: ignore
                )
            elif msg.role == "tool":
                func_name = msg.name or "unknown"
                try:
                    response_data = json.loads(msg.content) if msg.content else {}
                except (json.JSONDecodeError, ValueError):
                    response_data = {"result": msg.content}
                contents.append(
                    _genai_types.Content(  # type: ignore
                        role="user",
                        parts=[
                            _genai_types.Part.from_function_response(  # type: ignore
                                name=func_name,
                                response=response_data,
                            )
                        ],
                    )
                )

        # Build system instruction
        sys_content = None  # type: Any
        if system_instruction:
            sys_content = _genai_types.Content(  # type: ignore
                role="user",
                parts=[_genai_types.Part.from_text(text=system_instruction)],  # type: ignore
            )

        # Build generate content config
        generate_config = {}  # type: Dict[str, Any]
        if generation_config:
            # Extract tools from options if provided
            tools_config = None  # type: Optional[Any]
            if "tools" in options:
                raw_tools = options["tools"]
                if isinstance(raw_tools, list) and raw_tools:
                    tools_config = [
                        _genai_types.Tool(function_declarations=raw_tools)  # type: ignore
                    ]

            generate_config["config"] = _genai_types.GenerateContentConfig(  # type: ignore
                temperature=generation_config.get("temperature"),
                top_p=generation_config.get("top_p"),
                max_output_tokens=generation_config.get("max_output_tokens"),
                stop_sequences=generation_config.get("stop_sequences"),
                system_instruction=sys_content,
                tools=tools_config,
            )

        response = self._client.models.generate_content(  # type: ignore
            model=model, contents=contents, **generate_config
        )

        # Extract response text
        content_text = ""
        if response.text:
            content_text = response.text

        # Extract usage
        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }  # type: Dict[str, int]
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage["prompt_tokens"] = getattr(response.usage_metadata, "prompt_token_count", 0)
            usage["completion_tokens"] = getattr(
                response.usage_metadata, "candidates_token_count", 0
            )
            usage["total_tokens"] = getattr(response.usage_metadata, "total_token_count", 0)

        # Determine finish reason
        finish_reason = "stop"
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "finish_reason"):
                fr = str(candidate.finish_reason).upper()
                if "STOP" in fr:
                    finish_reason = "stop"
                elif "MAX" in fr or "LENGTH" in fr:
                    finish_reason = "length"
                elif (
                    "SAFETY" in fr
                    or "BLOCKLIST" in fr
                    or "PROHIBITED" in fr
                    or "SPII" in fr
                    or "ARMOR" in fr
                ):
                    finish_reason = "content_filter"
                elif "TOOL" in fr or "FUNCTION" in fr:
                    finish_reason = "tool_calls"
                elif "RECITATION" in fr:
                    finish_reason = "content_filter"
                elif "MALFORMED" in fr:
                    finish_reason = "error"
                elif "UNSPECIFIED" in fr or "OTHER" in fr:
                    finish_reason = "stop"
                # else: keep default "stop"

        # Extract tool_calls from function_call parts (new SDK)
        tool_calls = None  # type: Optional[List[Dict[str, Any]]]
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and candidate.content:
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        if tool_calls is None:
                            tool_calls = []
                        fc = part.function_call
                        tool_calls.append(
                            {
                                "id": "gemini-{}".format(int(time.time())),
                                "type": "function",
                                "function": {
                                    "name": getattr(fc, "name", ""),
                                    "arguments": json.dumps(getattr(fc, "args", {}))
                                    if hasattr(fc, "args")
                                    else "{}",
                                },
                            }
                        )

        return ChatResponse(
            id="gemini-{}".format(int(time.time())),
            model=model,
            content=content_text,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )

    def _chat_legacy_sdk(self, model, messages, system_instruction, generation_config, options):
        # type: (str, List[ChatMessage], Optional[str], Dict[str, Any], Dict[str, Any]) -> ChatResponse
        """Chat using the older google-generativeai SDK.

        Args:
            model: Model identifier.
            messages: Non-system messages.
            system_instruction: Combined system prompt.
            generation_config: Generation parameters.
            options: Additional options.

        Returns:
            ChatResponse with generated content.
        """
        # Create model instance
        model_instance = self._get_model(model)

        # Apply system_instruction if supported by the SDK version
        if system_instruction:
            try:
                model_instance = _GenerativeModel(  # type: ignore
                    model,
                    system_instruction=system_instruction,
                )
            except TypeError:
                # Older SDK versions don't support system_instruction parameter
                # Prepend system message to first user message
                if messages and messages[0].role == "user":
                    messages[0].content = system_instruction + "\n\n" + messages[0].content
                else:
                    # Insert a synthetic user message with system prompt
                    from berserker.provider.base import ChatMessage as _CM

                    messages = [_CM(role="user", content=system_instruction)] + messages

        # Convert messages to Google format
        google_messages = []  # type: List[Any]
        for msg in messages:
            if msg.role == "user":
                google_messages.append(
                    {"role": "user", "parts": [msg.content] if msg.content else []}
                )
            elif msg.role == "assistant":
                parts = []  # type: list
                if msg.content:
                    parts.append(msg.content)
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except (json.JSONDecodeError, ValueError):
                            args = {}
                        parts.append(
                            {
                                "function_call": {
                                    "name": func.get("name", ""),
                                    "args": args,
                                }
                            }
                        )
                if not parts:
                    parts.append("")
                google_messages.append({"role": "model", "parts": parts})
            elif msg.role == "tool":
                func_name = msg.name or "unknown"
                try:
                    response_data = json.loads(msg.content) if msg.content else {}
                except (json.JSONDecodeError, ValueError):
                    response_data = {"result": msg.content}
                google_messages.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "function_response": {
                                    "name": func_name,
                                    "response": response_data,
                                }
                            }
                        ],
                    }
                )

        # Build tool declarations if tools provided in options
        tools_param = None  # type: Optional[List[Dict[str, Any]]]
        if "tools" in options:
            raw_tools = options["tools"]
            if isinstance(raw_tools, list) and raw_tools:
                tools_param = [{"function_declarations": raw_tools}]

        # Send the chat request
        response = model_instance.generate_content(
            google_messages,
            generation_config=generation_config if generation_config else None,
            tools=tools_param,
        )

        # Extract response text
        content_text = ""
        try:
            content_text = response.text
        except (AttributeError, ValueError):
            # Some responses may not have text (e.g., blocked by safety)
            pass

        # Extract usage
        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }  # type: Dict[str, int]
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage["prompt_tokens"] = getattr(response.usage_metadata, "prompt_token_count", 0)
            usage["completion_tokens"] = getattr(
                response.usage_metadata, "candidates_token_count", 0
            )
            usage["total_tokens"] = getattr(response.usage_metadata, "total_token_count", 0)

        # Determine finish reason
        finish_reason = "stop"
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "finish_reason"):
                fr = str(candidate.finish_reason).upper()
                if "STOP" in fr:
                    finish_reason = "stop"
                elif "MAX" in fr or "LENGTH" in fr:
                    finish_reason = "length"
                elif (
                    "SAFETY" in fr
                    or "BLOCKLIST" in fr
                    or "PROHIBITED" in fr
                    or "SPII" in fr
                    or "ARMOR" in fr
                ):
                    finish_reason = "content_filter"
                elif "TOOL" in fr or "FUNCTION" in fr:
                    finish_reason = "tool_calls"
                elif "RECITATION" in fr:
                    finish_reason = "content_filter"
                elif "MALFORMED" in fr:
                    finish_reason = "error"
                elif "UNSPECIFIED" in fr or "OTHER" in fr:
                    finish_reason = "stop"
                # else: keep default "stop"

        # Extract tool_calls from function_call parts (legacy SDK)
        tool_calls = None  # type: Optional[List[Dict[str, Any]]]
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and candidate.content:
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        if tool_calls is None:
                            tool_calls = []
                        fc = part.function_call
                        tool_calls.append(
                            {
                                "id": "gemini-{}".format(int(time.time())),
                                "type": "function",
                                "function": {
                                    "name": getattr(fc, "name", ""),
                                    "arguments": json.dumps(getattr(fc, "args", {}))
                                    if hasattr(fc, "args")
                                    else "{}",
                                },
                            }
                        )

        return ChatResponse(
            id="gemini-{}".format(int(time.time())),
            model=model,
            content=content_text,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )

    @retry_with_backoff()
    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion from Google Gemini.

        Args:
            messages: List of ChatMessage objects forming the conversation.
            model: Model identifier to use.
            **options: Additional options (temperature, top_p, etc.).

        Yields:
            Text chunks as they are generated.

        Raises:
            AuthenticationError: If API key is invalid.
            RateLimitError: If rate limit is exceeded.
            ModelNotFoundError: If the model doesn't exist.
            APIError: For other API errors.
        """
        _ensure_sdk_available()

        try:
            system_instruction = _extract_system_messages(messages)
            non_system_messages = _filter_non_system_messages(messages)

            generation_config = {}  # type: Dict[str, Any]
            if "temperature" in options:
                generation_config["temperature"] = options["temperature"]
            if "top_p" in options:
                generation_config["top_p"] = options["top_p"]
            if "max_tokens" in options:
                generation_config["max_output_tokens"] = options["max_tokens"]
            if "stop_sequences" in options:
                generation_config["stop_sequences"] = options["stop_sequences"]

            if _use_new_sdk:
                for chunk in self._stream_new_sdk(
                    model, non_system_messages, system_instruction, generation_config, options
                ):
                    yield chunk
            else:
                for chunk in self._stream_legacy_sdk(
                    model, non_system_messages, system_instruction, generation_config, options
                ):
                    yield chunk

        except Exception as exc:
            mapped = _map_google_error(exc, model=model)
            raise mapped

    def _stream_new_sdk(self, model, messages, system_instruction, generation_config, options):
        # type: (str, List[ChatMessage], Optional[str], Dict[str, Any], Dict[str, Any]) -> Iterator[str]
        """Stream using the newer google-genai SDK."""
        assert _genai_types is not None  # Guaranteed by _use_new_sdk check
        contents = []  # type: list
        for msg in messages:
            if msg.role == "user":
                contents.append(
                    _genai_types.Content(
                        role="user", parts=[_genai_types.Part.from_text(text=msg.content)]
                    )  # type: ignore
                )
            elif msg.role == "assistant":
                parts = []  # type: list
                if msg.content:
                    parts.append(_genai_types.Part.from_text(text=msg.content))  # type: ignore
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        func = tc.get("function", {})
                        parts.append(
                            _genai_types.Part.from_function_call(  # type: ignore
                                name=func.get("name", ""),
                                args=_parse_json_safe(func.get("arguments", "{}")),
                            )
                        )
                if not parts:
                    parts.append(_genai_types.Part.from_text(text=""))  # type: ignore
                contents.append(
                    _genai_types.Content(role="model", parts=parts)  # type: ignore
                )
            elif msg.role == "tool":
                func_name = msg.name or "unknown"
                try:
                    response_data = json.loads(msg.content) if msg.content else {}
                except (json.JSONDecodeError, ValueError):
                    response_data = {"result": msg.content}
                contents.append(
                    _genai_types.Content(  # type: ignore
                        role="user",
                        parts=[
                            _genai_types.Part.from_function_response(  # type: ignore
                                name=func_name,
                                response=response_data,
                            )
                        ],
                    )
                )

        sys_content = None  # type: Any
        if system_instruction:
            sys_content = _genai_types.Content(  # type: ignore
                role="user",
                parts=[_genai_types.Part.from_text(text=system_instruction)],  # type: ignore
            )

        generate_config = {}  # type: Dict[str, Any]
        if generation_config:
            generate_config["config"] = _genai_types.GenerateContentConfig(  # type: ignore
                temperature=generation_config.get("temperature"),
                top_p=generation_config.get("top_p"),
                max_output_tokens=generation_config.get("max_output_tokens"),
                stop_sequences=generation_config.get("stop_sequences"),
                system_instruction=sys_content,
            )

        response = self._client.models.generate_content_stream(  # type: ignore
            model=model, contents=contents, **generate_config
        )

        for chunk in response:
            if hasattr(chunk, "text") and chunk.text:
                yield chunk.text

    def _stream_legacy_sdk(self, model, messages, system_instruction, generation_config, options):
        # type: (str, List[ChatMessage], Optional[str], Dict[str, Any], Dict[str, Any]) -> Iterator[str]
        """Stream using the older google-generativeai SDK."""
        model_instance = self._get_model(model)

        if system_instruction:
            try:
                model_instance = _GenerativeModel(  # type: ignore
                    model,
                    system_instruction=system_instruction,
                )
            except TypeError:
                if messages and messages[0].role == "user":
                    messages[0].content = system_instruction + "\n\n" + messages[0].content
                else:
                    from berserker.provider.base import ChatMessage as _CM

                    messages = [_CM(role="user", content=system_instruction)] + messages

        google_messages = []  # type: List[Any]
        for msg in messages:
            if msg.role == "user":
                google_messages.append(
                    {"role": "user", "parts": [msg.content] if msg.content else []}
                )
            elif msg.role == "assistant":
                parts = []  # type: list
                if msg.content:
                    parts.append(msg.content)
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except (json.JSONDecodeError, ValueError):
                            args = {}
                        parts.append(
                            {
                                "function_call": {
                                    "name": func.get("name", ""),
                                    "args": args,
                                }
                            }
                        )
                if not parts:
                    parts.append("")
                google_messages.append({"role": "model", "parts": parts})
            elif msg.role == "tool":
                func_name = msg.name or "unknown"
                try:
                    response_data = json.loads(msg.content) if msg.content else {}
                except (json.JSONDecodeError, ValueError):
                    response_data = {"result": msg.content}
                google_messages.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "function_response": {
                                    "name": func_name,
                                    "response": response_data,
                                }
                            }
                        ],
                    }
                )

        response = model_instance.generate_content(
            google_messages,
            generation_config=generation_config if generation_config else None,
            stream=True,
        )

        for chunk in response:
            if hasattr(chunk, "text") and chunk.text:
                yield chunk.text

    @retry_with_backoff()
    def list_models(self):
        # type: () -> List[str]
        """List all available generative models from Google.

        Returns:
            List of generative model names (excludes embedding, tuning models).

        Raises:
            AuthenticationError: If API key is invalid.
            APIError: For other API errors.
        """
        _ensure_sdk_available()

        try:
            if _use_new_sdk:
                return self._list_models_new_sdk()
            else:
                return self._list_models_legacy_sdk()

        except Exception as exc:
            raise _map_google_error(exc)

    def _list_models_new_sdk(self):
        # type: () -> List[str]
        """List models using the newer google-genai SDK."""
        models = []  # type: List[str]
        try:
            response = self._client.models.list()  # type: ignore
            for m in response:
                name = getattr(m, "name", "")
                # Filter to generative models only
                if name and "embed" not in name.lower() and "tuning" not in name.lower():
                    # Clean up the model name (remove "models/" prefix if present)
                    if name.startswith("models/"):
                        name = name[7:]
                    models.append(name)
        except Exception:
            # If API call fails, return default models
            logger.warning("Failed to fetch models from Google API, returning defaults")
            return list(DEFAULT_MODELS)

        return models if models else list(DEFAULT_MODELS)

    def _list_models_legacy_sdk(self):
        # type: () -> List[str]
        """List models using the older google-generativeai SDK."""
        models = []  # type: List[str]
        try:
            # google-generativeai provides list_models()
            all_models = _genai_module.list_models()  # type: ignore
            for m in all_models:
                name = getattr(m, "name", "")
                # Filter to generative models only
                if name and "embed" not in name.lower() and "tuning" not in name.lower():
                    # Clean up the model name
                    if name.startswith("models/"):
                        name = name[7:]
                    models.append(name)
        except Exception:
            # If API call fails, return default models
            logger.warning("Failed to fetch models from Google API, returning defaults")
            return list(DEFAULT_MODELS)

        return models if models else list(DEFAULT_MODELS)

    @retry_with_backoff()
    def count_tokens(self, text):
        # type: (str) -> int
        """Count tokens in the given text using Google's API.

        Falls back to character-based estimation if API is unavailable.

        Args:
            text: The text to count tokens for.

        Returns:
            Approximate token count.

        Raises:
            AuthenticationError: If API key is invalid.
            APIError: For other API errors.
        """
        _ensure_sdk_available()

        try:
            if _use_new_sdk:
                return self._count_tokens_new_sdk(text)
            else:
                return self._count_tokens_legacy_sdk(text)

        except Exception as exc:
            # Fallback to character-based estimation
            logger.debug("Token counting API failed, using fallback estimation: %s", exc)
            return self._estimate_tokens_fallback(text)

    def _count_tokens_new_sdk(self, text):
        # type: (str) -> int
        """Count tokens using the newer google-genai SDK."""
        response = self._client.models.count_tokens(  # type: ignore
            model="gemini-2.0-flash",  # Use a default model for counting
            contents=[_genai_types.Part.from_text(text=text)],  # type: ignore
        )
        return getattr(response, "total_tokens", 0) or self._estimate_tokens_fallback(text)

    def _count_tokens_legacy_sdk(self, text):
        # type: (str) -> int
        """Count tokens using the older google-generativeai SDK."""
        model_instance = self._get_model("gemini-2.0-flash")
        try:
            response = model_instance.count_tokens(text)
            return getattr(response, "total_tokens", 0)
        except Exception as e:
            logger.warning("Google legacy SDK count_tokens failed, using fallback: %s", e)
            return self._estimate_tokens_fallback(text)

    @staticmethod
    def _estimate_tokens_fallback(text):
        # type: (str) -> int
        """Estimate token count using character-based heuristic.

        Rough approximation: ~4 characters per token for English text.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count.
        """
        if not text:
            return 0
        # Average ~4 chars per token for English
        return max(1, len(text) // 4)

    def map_tools(self, tools):
        # type: (List[Any]) -> List[Dict[str, Any]]
        """Convert Tool objects to Google tool schema format."""
        return _map_tools(tools)


def _parse_json_safe(value):
    # type: (str) -> Dict[str, Any]
    """Safely parse a JSON string, returning empty dict on failure.

    Args:
        value: JSON string to parse.

    Returns:
        Parsed dict, or empty dict if parsing fails.
    """
    try:
        result = json.loads(value)
        if isinstance(result, dict):
            return result
        return {}
    except (json.JSONDecodeError, ValueError):
        return {}
