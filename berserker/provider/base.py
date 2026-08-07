"""
Provider ABC (Abstract Base Class) and error hierarchy for berserker.

Matches the reference TypeScript provider interface with:
- Error hierarchy (ProviderError and subclasses)
- Unified ChatMessage/ChatResponse dataclasses (OpenAI-compatible)
- Provider ABC with abstract methods for chat, stream, list_models, count_tokens
- Retry logic with exponential backoff
"""

import abc
import time
import functools
from dataclasses import dataclass, field
from typing import List, Iterator, Optional, Dict, Any, Callable, TypeVar

# ---------------------------------------------------------------------------
# Error Hierarchy
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Finish Reason Constants
# ---------------------------------------------------------------------------
# Standardized finish_reason values used across all providers.
# Each provider maps its native stop/finish reason to one of these constants.

FINISH_STOP = "stop"  # Normal completion (model finished naturally)
FINISH_LENGTH = "length"  # Max tokens reached, response truncated
FINISH_TOOL_CALLS = "tool_calls"  # Model requested tool/function calls
FINISH_CONTENT_FILTER = "content_filter"  # Content blocked by safety/filter policy
FINISH_ERROR = "error"  # Error occurred during generation
FINISH_REFUSAL = "refusal"  # Model refused to generate (safety/policy)

# All valid finish_reason values
VALID_FINISH_REASONS = frozenset(
    [
        FINISH_STOP,
        FINISH_LENGTH,
        FINISH_TOOL_CALLS,
        FINISH_CONTENT_FILTER,
        FINISH_ERROR,
        FINISH_REFUSAL,
    ]
)


class ProviderError(Exception):
    """Base exception for all provider-related errors."""

    pass


class AuthenticationError(ProviderError):
    """Raised when API key is invalid or missing."""

    pass


class RateLimitError(ProviderError):
    """Raised when rate limit is exceeded.

    Attributes:
        retry_after: Seconds to wait before retrying (if provided by API).
    """

    def __init__(self, message, retry_after=None):
        # type: (str, Optional[float]) -> None
        super(RateLimitError, self).__init__(message)
        self.retry_after = retry_after


class APIError(ProviderError):
    """Generic API error.

    Attributes:
        status_code: HTTP status code from the API response.
        response_body: Raw response body from the API.
    """

    def __init__(self, message, status_code=None, response_body=None):
        # type: (str, Optional[int], Optional[str]) -> None
        super(APIError, self).__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ModelNotFoundError(ProviderError):
    """Raised when the requested model is not found."""

    pass


class ContextLengthExceeded(ProviderError):
    """Raised when the conversation exceeds the model's context window.

    The caller should compact/truncate messages and retry.

    Attributes:
        model: The model that rejected the request.
    """

    def __init__(self, message, model=None):
        # type: (str, Optional[str]) -> None
        super(ContextLengthExceeded, self).__init__(message)
        self.model = model


# ---------------------------------------------------------------------------
# Unified Message Format (OpenAI-compatible)
# ---------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """A single message in a chat conversation.

    Attributes:
        role: One of "system", "user", "assistant", "tool".
        content: The text content of the message.
        tool_calls: Optional list of tool call objects (for assistant messages).
        tool_call_id: Optional ID linking to a specific tool call (for tool messages).
        name: Optional name for the message sender.
        reasoning_content: Optional thinking-mode reasoning text (DeepSeek V4).
            Thinking-mode APIs require it to be passed back on assistant
            messages in subsequent tool-loop requests.
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    reasoning_content: Optional[str] = None


@dataclass
class ChatResponse:
    """Response from a chat completion.

    Attributes:
        id: Unique identifier for this response.
        model: The model that generated this response.
        content: The generated text content.
        usage: Token usage dict with keys: prompt_tokens, completion_tokens, total_tokens.
        finish_reason: Why the generation stopped. One of FINISH_STOP, FINISH_LENGTH,
            FINISH_TOOL_CALLS, FINISH_CONTENT_FILTER, FINISH_ERROR, FINISH_REFUSAL.
        reasoning_content: Thinking-mode reasoning text when present (DeepSeek V4).
    """

    id: str
    model: str
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    finish_reason: str = "stop"
    reasoning_content: Optional[str] = None


# ---------------------------------------------------------------------------
# Retry Logic (Exponential Backoff)
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])


def retry_with_backoff(max_retries=None, is_retryable=None):
    # type: (Optional[int], Optional[Callable[[Exception], bool]]) -> Callable[[F], F]
    """Decorator that retries a function on retryable errors with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (default: 3).
        is_retryable: Optional callable that takes an exception and returns True
                      if the error is retryable. Default checks for RateLimitError
                      and APIError with status >= 500.

    Returns:
        Decorated function with retry logic.

    Backoff formula: min(2^attempt * 0.5, 30) seconds.
    """
    if max_retries is None:
        max_retries = 3

    def default_is_retryable(exc):
        # type: (Exception) -> bool
        if isinstance(exc, RateLimitError):
            return True
        if isinstance(exc, APIError):
            return exc.status_code is not None and exc.status_code >= 500
        return False

    checker = is_retryable if is_retryable is not None else default_is_retryable

    def decorator(func):
        # type: (F) -> F
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # type: (Any, Any) -> Any
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if not checker(exc):
                        raise
                    if attempt >= max_retries:
                        raise
                    # Calculate backoff: min(2^attempt * 0.5, 30)
                    backoff = min((2**attempt) * 0.5, 30.0)
                    # Use retry_after from RateLimitError if available
                    if isinstance(exc, RateLimitError) and exc.retry_after is not None:
                        backoff = max(backoff, exc.retry_after)
                    time.sleep(backoff)
            # Should never reach here, but satisfy type checker
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------


class Provider(abc.ABC):
    """Abstract base class for LLM providers.

    All concrete providers (OpenAI, Anthropic, etc.) must inherit from this
    class and implement all abstract methods.

    Concrete properties:
        id: Unique identifier for the provider instance.
        name: Human-readable provider name.
        models: List of supported model names.
        timeout: Request timeout in seconds (default: 60).
        max_retries: Maximum retry attempts (default: 3).
    """

    def __init__(self, id, name, models=None, timeout=60, max_retries=3):
        # type: (str, str, Optional[List[str]], int, int) -> None
        self.id = id
        self.name = name
        self.models = models if models is not None else []
        self.timeout = timeout
        self.max_retries = max_retries

    @abc.abstractmethod
    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Send a chat completion request and return the response.

        Args:
            messages: List of ChatMessage objects forming the conversation.
            model: Model identifier to use.
            **options: Additional provider-specific options (temperature, top_p, etc.).

        Returns:
            ChatResponse with the generated content and metadata.
        """
        pass

    @abc.abstractmethod
    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion, yielding text chunks.

        Args:
            messages: List of ChatMessage objects forming the conversation.
            model: Model identifier to use.
            **options: Additional provider-specific options.

        Yields:
            Text chunks as they are generated.
        """
        pass

    @abc.abstractmethod
    def list_models(self):
        # type: () -> List[str]
        """List all available models for this provider.

        Returns:
            List of model name strings.
        """
        pass

    @abc.abstractmethod
    def count_tokens(self, text):
        # type: (str) -> int
        """Count the number of tokens in the given text.

        Args:
            text: The text to tokenize and count.

        Returns:
            Approximate token count.
        """
        pass

    def map_tools(self, tools):
        # type: (List[Any]) -> List[Dict[str, Any]]
        """Convert Tool objects to provider-specific tool schema format.

        Subclasses should override this to convert Tool objects to their
        API's tool declaration format. Default returns empty list (no tools).

        Args:
            tools: List of Tool objects from ToolRegistry.

        Returns:
            List of provider-compatible tool schema dicts.
        """
        return []

    def __repr__(self):
        # type: () -> str
        return "<Provider id={} name={} models={}>".format(self.id, self.name, self.models)
