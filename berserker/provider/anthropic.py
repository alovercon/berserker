"""
Anthropic provider adapter for berserker.

Implements the Provider ABC for Anthropic's Claude models using the official
anthropic Python SDK. Handles Anthropic's unique message format where system
prompts are passed as a separate parameter (not in the messages array).

Compatible with Python 3.8.10+.
"""

import os
import json
import logging
from typing import List, Iterator, Optional, Dict, Any

import anthropic

from berserker.provider.base import (
    Provider,
    ChatMessage,
    ChatResponse,
    AuthenticationError,
    RateLimitError,
    APIError,
    ModelNotFoundError,
    ContextLengthExceeded,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)

# Static list of known Claude models (Anthropic has no models.list() API)
_CLAUDE_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
]


def _handle_error(exc):
    # type: (Exception) -> None
    """Map Anthropic SDK exceptions to our error hierarchy.

    Raises the appropriate berserker error type.
    """
    if isinstance(exc, anthropic.AuthenticationError):
        raise AuthenticationError(str(exc))
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = None
        # type: Optional[float]
        try:
            retry_after = float(exc.response.headers.get("retry-after", 0))
        except (AttributeError, ValueError, TypeError):
            pass
        raise RateLimitError(str(exc), retry_after=retry_after)
    if isinstance(exc, anthropic.NotFoundError):
        raise ModelNotFoundError(str(exc))
    if isinstance(exc, anthropic.APIStatusError):
        status_code = exc.status_code  # type: int
        response_body = None  # type: Optional[str]
        try:
            response_body = str(exc.message)
        except (AttributeError, TypeError):
            pass
        # Check for context window exceeded in error body
        body_lower = response_body.lower() if response_body else ""
        if "model_context_window_exceeded" in body_lower or (
            "context" in body_lower and "window" in body_lower and "exceed" in body_lower
        ):
            raise ContextLengthExceeded(
                "Anthropic context window exceeded: {}".format(str(exc)),
            )
        raise APIError(str(exc), status_code=status_code, response_body=response_body)
    # Re-raise if not a recognized Anthropic error
    raise


def _convert_messages(messages):
    # type: (List[ChatMessage]) -> tuple
    """Convert ChatMessage list to Anthropic format.

    Anthropic requires:
    - System prompt as a separate `system` parameter (NOT in messages array)
    - Messages alternate between user and assistant roles
    - Tool results are sent as user messages with tool_result content blocks

    Returns:
        Tuple of (system_prompt, anthropic_messages)
    """
    system_parts = []  # type: List[str]
    anthropic_messages = []  # type: List[Dict[str, Any]]

    for msg in messages:
        if msg.role == "system":
            # Collect system prompt parts (not added to messages array)
            system_parts.append(msg.content)
        elif msg.role == "user":
            # Regular user message
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": msg.content,
                }
            )
        elif msg.role == "tool":
            # Tool result: user message with tool_result content block
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                }
            )
        elif msg.role == "assistant":
            if msg.tool_calls is not None and len(msg.tool_calls) > 0:
                # Assistant message with tool calls: content array with text + tool_use blocks
                content_blocks = []  # type: List[Dict[str, Any]]
                if msg.content:
                    content_blocks.append(
                        {
                            "type": "text",
                            "text": msg.content,
                        }
                    )
                for tc in msg.tool_calls:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": tc.get("name", ""),
                            "input": tc.get("input", {}),
                        }
                    )
                anthropic_messages.append(
                    {
                        "role": "assistant",
                        "content": content_blocks,
                    }
                )
            else:
                # Regular assistant message
                anthropic_messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content,
                    }
                )
        # Skip any unrecognized roles

    # Join system parts into a single system prompt
    system_prompt = "\n\n".join(system_parts) if system_parts else None

    return system_prompt, anthropic_messages


def _map_tools(tools):
    # type: (List[Any]) -> List[Dict[str, Any]]
    """Convert Tool objects to Anthropic tool schema format.

    Anthropic format: {"name", "description", "input_schema"}

    Args:
        tools: List of Tool objects from ToolRegistry.

    Returns:
        List of Anthropic-compatible tool schema dicts.
    """
    result = []  # type: List[Dict[str, Any]]
    for tool in tools:
        result.append(
            {
                "name": tool.id,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
        )
    return result


class AnthropicProvider(Provider):
    """Anthropic Claude provider adapter.

    Uses the official anthropic Python SDK to interact with Claude models.
    Handles Anthropic's unique message format where system prompts are passed
    as a separate parameter rather than in the messages array.

    API key is read from the constructor or the ANTHROPIC_API_KEY environment
    variable.

    Usage:
        provider = AnthropicProvider(api_key="sk-ant-...")
        response = provider.chat(
            messages=[ChatMessage(role="user", content="Hello")],
            model="claude-sonnet-4-20250514",
        )
    """

    def __init__(self, id=None, api_key=None, base_url=None, timeout=60, max_retries=3):
        # type: (Optional[str], Optional[str], Optional[str], int, int) -> None
        """Initialize the Anthropic provider.

        Args:
            id: Unique provider identifier (default: "anthropic").
            api_key: Anthropic API key. If None, reads from ANTHROPIC_API_KEY env var.
            base_url: Optional custom API base URL.
            timeout: Request timeout in seconds (default: 60).
            max_retries: Maximum retry attempts (default: 3).
        """
        provider_id = id if id is not None else "anthropic"
        super(AnthropicProvider, self).__init__(
            id=provider_id,
            name="Anthropic",
            models=list(_CLAUDE_MODELS),
            timeout=timeout,
            max_retries=max_retries,
        )

        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = base_url

        # Initialize the Anthropic SDK client
        client_kwargs = {
            "api_key": self.api_key,
            "timeout": timeout,
            "max_retries": max_retries,
        }  # type: Dict[str, Any]
        if base_url is not None:
            client_kwargs["base_url"] = base_url

        self._client = anthropic.Anthropic(**client_kwargs)

    def _build_chat_params(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Dict[str, Any]
        """Build the parameters dict for an Anthropic messages.create call.

        Handles message conversion and merges provider-specific options.
        """
        system_prompt, anthropic_messages = _convert_messages(messages)

        params = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": options.pop("max_tokens", 4096),
        }  # type: Dict[str, Any]

        if system_prompt is not None:
            params["system"] = system_prompt

        # Map common options
        if "temperature" in options:
            params["temperature"] = options.pop("temperature")
        if "top_p" in options:
            params["top_p"] = options.pop("top_p")
        if "stop_sequences" in options:
            params["stop_sequences"] = options.pop("stop_sequences")

        # Handle tools parameter
        if "tools" in options:
            params["tools"] = options.pop("tools")
        if "tool_choice" in options:
            params["tool_choice"] = options.pop("tool_choice")

        return params

    @retry_with_backoff()
    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Send a chat completion request to Anthropic.

        Converts ChatMessage objects to Anthropic's message format,
        extracts system prompts as a separate parameter, and maps the
        response back to a ChatResponse dataclass.

        Args:
            messages: List of ChatMessage objects.
            model: Claude model identifier (e.g. "claude-sonnet-4-20250514").
            **options: Additional options (temperature, max_tokens, tools, etc.).

        Returns:
            ChatResponse with generated content and metadata.

        Raises:
            AuthenticationError: If API key is invalid.
            RateLimitError: If rate limit is exceeded.
            APIError: For other API errors.
            ModelNotFoundError: If the model does not exist.
        """
        try:
            params = self._build_chat_params(messages, model, **options)
            response = self._client.messages.create(**params)

            # Extract content text
            content = ""
            for block in response.content:
                if block.type == "text":
                    content += block.text

            # Build usage dict
            usage = {
                "prompt_tokens": getattr(response.usage, "input_tokens", 0),
                "completion_tokens": getattr(response.usage, "output_tokens", 0),
                "total_tokens": getattr(response.usage, "input_tokens", 0)
                + getattr(response.usage, "output_tokens", 0),
            }  # type: Dict[str, int]

            # Extract tool_calls from content blocks
            tool_calls = None  # type: Optional[List[Dict[str, Any]]]
            for block in response.content:
                if block.type == "tool_use":
                    if tool_calls is None:
                        tool_calls = []
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.input)
                                if isinstance(block.input, dict)
                                else block.input,
                            },
                        }
                    )

            # Determine finish reason
            stop_reason = getattr(response, "stop_reason", "stop")
            if stop_reason == "end_turn":
                finish_reason = "stop"
            elif stop_reason == "max_tokens":
                finish_reason = "length"
            elif stop_reason == "tool_use":
                finish_reason = "tool_calls"
            elif stop_reason == "stop_sequence":
                finish_reason = "stop"
            elif stop_reason == "refusal":
                finish_reason = "refusal"
            elif stop_reason == "model_context_window_exceeded":
                finish_reason = "length"
            else:
                # Unknown stop_reason - log warning and default to "stop"
                finish_reason = "stop"

            return ChatResponse(
                id=getattr(response, "id", ""),
                model=getattr(response, "model", model),
                content=content,
                tool_calls=tool_calls,
                usage=usage,
                finish_reason=finish_reason,
            )

        except anthropic.APIError as exc:
            _handle_error(exc)
        except Exception as exc:
            # Wrap unexpected errors as APIError
            raise APIError("Unexpected error during Anthropic chat: {}".format(str(exc)))

    @retry_with_backoff()
    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion from Anthropic, yielding text deltas.

        Uses Anthropic's streaming API to yield text chunks as they are
        generated. System prompts are extracted and passed separately.

        Args:
            messages: List of ChatMessage objects.
            model: Claude model identifier.
            **options: Additional options (temperature, max_tokens, etc.).

        Yields:
            Text chunks (str) as they are generated.

        Raises:
            AuthenticationError: If API key is invalid.
            RateLimitError: If rate limit is exceeded.
            APIError: For other API errors.
            ModelNotFoundError: If the model does not exist.
        """
        try:
            params = self._build_chat_params(messages, model, **options)

            with self._client.messages.stream(**params) as stream:
                for text_chunk in stream.text_stream:
                    if text_chunk:
                        yield text_chunk

        except anthropic.APIError as exc:
            _handle_error(exc)
        except Exception as exc:
            # Wrap unexpected errors as APIError
            raise APIError("Unexpected error during Anthropic stream: {}".format(str(exc)))

    def list_models(self):
        # type: () -> List[str]
        """List all known Claude models.

        Anthropic does not provide a models.list() API endpoint, so this
        returns a static list of known Claude model identifiers.

        Returns:
            List of Claude model name strings.
        """
        return list(_CLAUDE_MODELS)

    def count_tokens(self, text):
        # type: (str) -> int
        """Estimate the number of tokens in the given text.

        Uses a character-based estimation (~4 characters per token) since
        Anthropic's Python SDK does not expose a dedicated token counting
        endpoint in all versions.

        Args:
            text: The text to estimate token count for.

        Returns:
            Approximate token count.
        """
        if not text:
            return 0
        # Anthropic's Claude models average ~3.5-4 chars per token
        # Use 4 as a conservative estimate
        return max(1, len(text) // 4)

    def map_tools(self, tools):
        # type: (List[Any]) -> List[Dict[str, Any]]
        """Convert Tool objects to Anthropic tool schema format."""
        return _map_tools(tools)
