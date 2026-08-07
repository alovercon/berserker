"""
OpenAI provider adapter for berserker.

Implements the Provider ABC using the official OpenAI Python SDK.
Supports both standard OpenAI and Azure OpenAI endpoints.

Python 3.8.10 compatible.
"""

import logging
import os
import openai
from typing import List, Iterator, Optional, Dict, Any

logger = logging.getLogger(__name__)

from berserker.provider.base import (
    Provider,
    ChatMessage,
    ChatResponse,
    AuthenticationError,
    RateLimitError,
    APIError,
    ModelNotFoundError,
    ContextLengthExceeded,
)


# ---------------------------------------------------------------------------
# Optional tiktoken import
# ---------------------------------------------------------------------------

try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True  # type: bool
except ImportError:
    tiktoken = None  # type: ignore[assignment]
    _TIKTOKEN_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_messages(messages):
    # type: (List[ChatMessage]) -> List[Dict[str, Any]]
    """Convert our ChatMessage list to OpenAI API message format.

    Args:
        messages: List of ChatMessage objects.

    Returns:
        List of dicts compatible with OpenAI chat completions API.
    """
    result = []  # type: List[Dict[str, Any]]
    for msg in messages:
        item = {
            "role": msg.role,
            "content": msg.content,
        }  # type: Dict[str, Any]

        # Add tool_call_id for tool messages
        if msg.tool_call_id is not None:
            item["tool_call_id"] = msg.tool_call_id

        # Add name if present
        if msg.name is not None:
            item["name"] = msg.name

        # Add tool_calls for assistant messages
        if msg.tool_calls is not None and len(msg.tool_calls) > 0:
            item["tool_calls"] = msg.tool_calls

        result.append(item)

    return result


def _map_response(response, model):
    # type: (Any, str) -> ChatResponse
    """Convert an OpenAI API response to our ChatResponse dataclass.

    Args:
        response: OpenAI ChatCompletion object.
        model: Model identifier string.

    Returns:
        ChatResponse with content and metadata.
    """
    choice = response.choices[0]
    message = choice.message

    # Extract content (may be None for tool-call-only responses)
    content = message.content if message.content is not None else ""

    # Extract tool_calls to unified format
    tool_calls = None  # type: Optional[List[Dict[str, Any]]]
    if hasattr(message, "tool_calls") and message.tool_calls is not None:
        tool_calls = []
        for tc in message.tool_calls:
            tool_calls.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
            )

    # Extract usage
    usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }  # type: Dict[str, int]
    if response.usage is not None:
        usage["prompt_tokens"] = response.usage.prompt_tokens
        usage["completion_tokens"] = response.usage.completion_tokens
        usage["total_tokens"] = response.usage.total_tokens

    return ChatResponse(
        id=response.id,
        model=model,
        content=content,
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=choice.finish_reason or "stop",
    )


def _map_tools(tools):
    # type: (List[Any]) -> List[Dict[str, Any]]
    """Convert Tool objects to OpenAI tool schema format.

    OpenAI format: {"type": "function", "function": {"name", "description", "parameters"}}

    Args:
        tools: List of Tool objects from ToolRegistry.

    Returns:
        List of OpenAI-compatible tool schema dicts.
    """
    result = []  # type: List[Dict[str, Any]]
    for tool in tools:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool.id,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return result


def _translate_error(exc, model):
    # type: (Exception, str) -> Exception
    """Translate OpenAI SDK exceptions to our error hierarchy.

    Args:
        exc: The caught exception from the OpenAI SDK.
        model: The model identifier (for error messages).

    Returns:
        A berserker provider exception.
    """
    if isinstance(exc, openai.AuthenticationError):
        return AuthenticationError("OpenAI authentication failed: {}".format(str(exc)))

    if isinstance(exc, openai.RateLimitError):
        retry_after = None  # type: Optional[float]
        resp = getattr(exc, "response", None)
        if resp is not None:
            retry_after_header = resp.headers.get("retry-after")  # type: ignore[union-attr]
            if retry_after_header is not None:
                try:
                    retry_after = float(retry_after_header)
                except (ValueError, TypeError):
                    pass
        return RateLimitError(
            "OpenAI rate limit exceeded: {}".format(str(exc)),
            retry_after=retry_after,
        )

    if isinstance(exc, openai.BadRequestError):
        body = str(exc)
        body_lower = body.lower()
        # Check for context length exceeded
        if "context_length_exceeded" in body_lower or (
            "context" in body_lower and "length" in body_lower and "exceed" in body_lower
        ) or (
            "maximum context length" in body_lower
        ):
            return ContextLengthExceeded(
                "OpenAI context length exceeded: {}".format(str(exc)),
                model=model,
            )
        # Check if this is a model-not-found error
        if "model" in body_lower and (
            "not found" in body_lower or "does not exist" in body_lower
        ):
            return ModelNotFoundError("Model '{}' not found: {}".format(model, str(exc)))
        return APIError(
            "OpenAI bad request: {}".format(str(exc)),
            status_code=getattr(exc, "status_code", 400),
            response_body=str(exc),
        )

    if isinstance(exc, openai.NotFoundError):
        return ModelNotFoundError("Model '{}' not found: {}".format(model, str(exc)))

    if isinstance(exc, openai.APIStatusError):
        return APIError(
            "OpenAI API error: {}".format(str(exc)),
            status_code=getattr(exc, "status_code", None),
            response_body=str(exc),
        )

    if isinstance(exc, openai.APIConnectionError):
        return APIError(
            "OpenAI connection error: {}".format(str(exc)),
            status_code=None,
            response_body=str(exc),
        )

    if isinstance(exc, openai.Timeout):
        return APIError(
            "OpenAI request timed out: {}".format(str(exc)),
            status_code=None,
            response_body=str(exc),
        )

    # Fallback: re-raise as generic APIError
    return APIError(
        "OpenAI error: {}".format(str(exc)),
        status_code=None,
        response_body=str(exc),
    )


def _get_tiktoken_encoding(model):
    # type: (str) -> Optional[Any]
    """Get a tiktoken encoding for the given model.

    Args:
        model: Model identifier string.

    Returns:
        tiktoken Encoding object, or None if unavailable.
    """
    if not _TIKTOKEN_AVAILABLE or tiktoken is None:
        return None

    try:
        # Try to get encoding for the specific model
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # Fall back to cl100k_base (used by gpt-4, gpt-3.5-turbo, etc.)
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.warning("tiktoken cl100k_base fallback failed: %s", e)
            return None
    except Exception as e:
        logger.warning("tiktoken encoding_for_model failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Model filtering helper
# ---------------------------------------------------------------------------


def _is_chat_model(model_id):
    # type: (str) -> bool
    """Check if a model ID represents a chat-capable model.

    Args:
        model_id: Model identifier string.

    Returns:
        True if the model is chat-capable.
    """
    # Exclude known non-chat model patterns
    non_chat_patterns = [
        "text-embedding",
        "tts",
        "whisper",
        "dall-e",
        "dalle",
        "moderation",
        "babbage",
        "davinci",
        "curie",
        "ada",
        "ft:",  # fine-tuned models (prefix check)
    ]

    model_lower = model_id.lower()
    for pattern in non_chat_patterns:
        if pattern in model_lower:
            return False

    # Include known chat model patterns
    chat_patterns = [
        "gpt-4",
        "gpt-3.5",
        "o1",
        "o3",
        "o4",
    ]
    for pattern in chat_patterns:
        if pattern in model_lower:
            return True

    # If it doesn't match exclusion patterns, assume it might be chat-capable
    # (for custom/fine-tuned models on compatible endpoints)
    return True


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class OpenAIProvider(Provider):
    """OpenAI provider using the official OpenAI Python SDK.

    Supports any OpenAI-compatible API endpoint (OpenAI, Groq, Together, etc.)
    by configuring base_url.

    Usage:
        provider = OpenAIProvider(api_key="sk-...")
        response = provider.chat([ChatMessage(role="user", content="Hello")], "gpt-4o")
    """

    def __init__(self, id=None, api_key=None, base_url=None, timeout=60, max_retries=3):
        # type: (Optional[str], Optional[str], Optional[str], int, int) -> None
        """Initialize the OpenAI provider.

        Args:
            id: Unique provider identifier (default: "openai").
            api_key: OpenAI API key (default: OPENAI_API_KEY env var).
            base_url: Custom API base URL (default: OpenAI default).
            timeout: Request timeout in seconds (default: 60).
            max_retries: Maximum retry attempts (default: 3).
        """
        provider_id = id if id is not None else "openai"
        resolved_api_key = (
            api_key if (api_key is not None and api_key != "") else os.environ.get("OPENAI_API_KEY")
        )
        if resolved_api_key is None or resolved_api_key == "":
            resolved_api_key = "not-needed"

        super(OpenAIProvider, self).__init__(
            id=provider_id,
            name="OpenAI",
            timeout=timeout,
            max_retries=max_retries,
        )

        self.base_url = base_url
        self._client = openai.OpenAI(
            api_key=resolved_api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Send a chat completion request.

        Args:
            messages: List of ChatMessage objects.
            model: Model identifier (e.g. "gpt-4o", "gpt-3.5-turbo").
            **options: Additional options (temperature, top_p, max_tokens, tools, etc.).

        Returns:
            ChatResponse with the generated content.

        Raises:
            AuthenticationError: If API key is invalid.
            RateLimitError: If rate limit is exceeded.
            ModelNotFoundError: If model does not exist.
            APIError: For other API errors.
        """
        try:
            mapped = _map_messages(messages)
            params = {"model": model, "messages": mapped}  # type: Dict[str, Any]

            # Pass tools if provided
            if "tools" in options:
                params["tools"] = options.pop("tools")

            # Merge remaining options
            params.update(options)

            response = self._client.chat.completions.create(**params)
            return _map_response(response, model)
        except Exception as exc:
            raise _translate_error(exc, model)

    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion, yielding text chunks.

        Args:
            messages: List of ChatMessage objects.
            model: Model identifier.
            **options: Additional options (temperature, top_p, etc.).

        Yields:
            Text chunks as they are generated.

        Raises:
            AuthenticationError: If API key is invalid.
            RateLimitError: If rate limit is exceeded.
            ModelNotFoundError: If model does not exist.
            APIError: For other API errors.
        """
        try:
            mapped = _map_messages(messages)
            response = self._client.chat.completions.create(
                model=model, messages=mapped, stream=True, **options
            )

            for chunk in response:
                if chunk.choices is None:
                    continue
                for choice in chunk.choices:
                    delta = choice.delta
                    if delta is None:
                        continue
                    if delta.content is not None and delta.content != "":
                        yield delta.content
        except Exception as exc:
            raise _translate_error(exc, model)

    def list_models(self):
        # type: () -> List[str]
        """List all available chat-capable models.

        Filters out embedding, fine-tune, and other non-chat models.

        Returns:
            List of model ID strings.
        """
        try:
            models = self._client.models.list()
            result = []  # type: List[str]
            for m in models.data:
                model_id = m.id
                # Filter to chat-capable models
                # Exclude embeddings, fine-tunes, whisper, dall-e, tts, etc.
                if self._is_chat_model(model_id):
                    result.append(model_id)
            return result
        except Exception as exc:
            raise _translate_error(exc, "list_models")

    def _is_chat_model(self, model_id):
        # type: (str) -> bool
        """Check if a model ID represents a chat-capable model.

        Args:
            model_id: Model identifier string.

        Returns:
            True if the model is chat-capable.
        """
        return _is_chat_model(model_id)

    def count_tokens(self, text):
        # type: (str) -> int
        """Count tokens using tiktoken (if available) or character estimation.

        Args:
            text: The text to count tokens for.

        Returns:
            Approximate token count.
        """
        if not text:
            return 0

        # Try tiktoken first
        encoding = _get_tiktoken_encoding("gpt-4o")
        if encoding is not None:
            try:
                return len(encoding.encode(text))
            except Exception as e:
                logger.warning("tiktoken encoding failed for openai count_tokens: %s", e)
                pass

        # Fallback: ~4 chars per token estimation
        return max(1, len(text) // 4)

    def map_tools(self, tools):
        # type: (List[Any]) -> List[Dict[str, Any]]
        """Convert Tool objects to OpenAI tool schema format."""
        return _map_tools(tools)


# ---------------------------------------------------------------------------
# AzureOpenAIProvider
# ---------------------------------------------------------------------------


class AzureOpenAIProvider(Provider):
    """Azure OpenAI provider using the Azure OpenAI SDK.

    Requires azure_endpoint and api_version in addition to api_key.

    Usage:
        provider = AzureOpenAIProvider(
            azure_endpoint="https://my-resource.openai.azure.com/",
            api_version="2024-02-01",
            api_key="...",
        )
    """

    def __init__(
        self,
        azure_endpoint=None,
        api_version=None,
        api_key=None,
        id=None,
        timeout=60,
        max_retries=3,
    ):
        # type: (Optional[str], Optional[str], Optional[str], Optional[str], int, int) -> None
        """Initialize the Azure OpenAI provider.

        Args:
            azure_endpoint: Azure OpenAI resource endpoint URL.
            api_version: Azure OpenAI API version (e.g. "2024-02-01").
            api_key: Azure OpenAI API key (default: AZURE_OPENAI_API_KEY env var).
            id: Unique provider identifier (default: "azure-openai").
            timeout: Request timeout in seconds (default: 60).
            max_retries: Maximum retry attempts (default: 3).

        Raises:
            ValueError: If azure_endpoint or api_version is missing.
        """
        if azure_endpoint is None:
            raise ValueError("azure_endpoint is required for AzureOpenAIProvider")
        if api_version is None:
            raise ValueError("api_version is required for AzureOpenAIProvider")

        provider_id = id if id is not None else "azure-openai"
        resolved_api_key = (
            api_key
            if (api_key is not None and api_key != "")
            else os.environ.get("AZURE_OPENAI_API_KEY")
        )
        if resolved_api_key is None or resolved_api_key == "":
            resolved_api_key = "not-needed"

        super(AzureOpenAIProvider, self).__init__(
            id=provider_id,
            name="Azure OpenAI",
            timeout=timeout,
            max_retries=max_retries,
        )

        self.azure_endpoint = azure_endpoint
        self.api_version = api_version
        self._client = openai.AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            api_key=resolved_api_key,
            timeout=timeout,
            max_retries=max_retries,
        )

    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Send a chat completion request to Azure OpenAI.

        Args:
            messages: List of ChatMessage objects.
            model: Azure deployment name (e.g. "gpt-4o").
            **options: Additional options (temperature, top_p, tools, etc.).

        Returns:
            ChatResponse with the generated content.
        """
        try:
            mapped = _map_messages(messages)
            params = {"model": model, "messages": mapped}  # type: Dict[str, Any]

            if "tools" in options:
                params["tools"] = options.pop("tools")

            params.update(options)

            response = self._client.chat.completions.create(**params)
            return _map_response(response, model)
        except Exception as exc:
            raise _translate_error(exc, model)

    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion from Azure OpenAI.

        Args:
            messages: List of ChatMessage objects.
            model: Azure deployment name.
            **options: Additional options.

        Yields:
            Text chunks as they are generated.
        """
        try:
            mapped = _map_messages(messages)
            response = self._client.chat.completions.create(
                model=model, messages=mapped, stream=True, **options
            )

            for chunk in response:
                if chunk.choices is None:
                    continue
                for choice in chunk.choices:
                    delta = choice.delta
                    if delta is None:
                        continue
                    if delta.content is not None and delta.content != "":
                        yield delta.content
        except Exception as exc:
            raise _translate_error(exc, model)

    def list_models(self):
        # type: () -> List[str]
        """List available Azure OpenAI deployments.

        Note: Azure OpenAI uses the models.list() API which returns
        the models available to the resource.

        Returns:
            List of model/deployment ID strings.
        """
        try:
            models = self._client.models.list()
            result = []  # type: List[str]
            for m in models.data:
                model_id = m.id
                if self._is_chat_model(model_id):
                    result.append(model_id)
            return result
        except Exception as exc:
            raise _translate_error(exc, "list_models")

    def _is_chat_model(self, model_id):
        # type: (str) -> bool
        """Check if a model ID represents a chat-capable model."""
        return _is_chat_model(model_id)

    def count_tokens(self, text):
        # type: (str) -> int
        """Count tokens using tiktoken or character estimation."""
        # Reuse the same logic as OpenAIProvider
        if not text:
            return 0
        encoding = _get_tiktoken_encoding("gpt-4o")
        if encoding is not None:
            try:
                return len(encoding.encode(text))
            except Exception as e:
                logger.warning("tiktoken encoding failed for azure count_tokens: %s", e)
                pass
        return max(1, len(text) // 4)

    def map_tools(self, tools):
        # type: (List[Any]) -> List[Dict[str, Any]]
        """Convert Tool objects to OpenAI tool schema format."""
        return _map_tools(tools)
