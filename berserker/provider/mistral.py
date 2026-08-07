"""
Mistral AI provider for berserker.

Uses mistralai SDK. Supports mistral-large, mistral-small, codestral,
and open-mistral-nemo models.
"""

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

# Wrap mistralai import — file must be importable even without mistralai
try:
    from mistralai import Mistral  # type: ignore[import-not-found]
    from mistralai.models import SDKError  # type: ignore[import-not-found]

    _MISTRAL_AVAILABLE = True
except ImportError:
    _MISTRAL_AVAILABLE = False
    Mistral = None  # type: ignore[assignment]
    SDKError = Exception  # type: ignore[assignment]

# Known Mistral models
_MISTRAL_MODELS = [
    "mistral-large-latest",
    "mistral-small-latest",
    "codestral-latest",
    "open-mistral-nemo",
    "mistral-large-2411",
    "mistral-large-2407",
    "mistral-small-2409",
    "open-mistral-nemo-2407",
    "codestral-2405",
    "ministral-8b-latest",
    "ministral-3b-latest",
]


def _map_mistral_error(exc):
    # type: (Exception) -> Exception
    """Map mistralai SDK exceptions to our error hierarchy."""
    # Check for SDKError from mistralai
    if SDKError is not None and isinstance(exc, SDKError):
        # Try to extract status code and message
        status_code = getattr(exc, "status_code", None)
        message = str(exc)

        if status_code is not None:
            if status_code == 401 or status_code == 403:
                return AuthenticationError(message)
            if status_code == 429:
                return RateLimitError(message)
            if status_code >= 500:
                return APIError(message, status_code=status_code)
            if status_code == 404:
                return ModelNotFoundError(message)
            return APIError(message, status_code=status_code)

        # No status code — check message for hints
        msg_lower = message.lower()
        if (
            "unauthorized" in msg_lower
            or "forbidden" in msg_lower
            or "invalid api key" in msg_lower
        ):
            return AuthenticationError(message)
        if "rate limit" in msg_lower or "too many requests" in msg_lower:
            return RateLimitError(message)

        return APIError(message)

    return APIError(str(exc))


class MistralProvider(Provider):
    """Mistral AI provider using mistralai SDK."""

    def __init__(self, id=None, api_key=None, timeout=60, max_retries=3):
        # type: (Optional[str], Optional[str], int, int) -> None
        if not _MISTRAL_AVAILABLE:
            raise ImportError(
                "mistralai is required for MistralProvider. Install it with: pip install mistralai"
            )

        provider_id = id if id is not None else "mistral"
        super(MistralProvider, self).__init__(
            id=provider_id,
            name="Mistral AI",
            models=list(_MISTRAL_MODELS),
            timeout=timeout,
            max_retries=max_retries,
        )

        self.api_key = api_key

        if api_key is None:
            raise ValueError(
                "api_key is required for MistralProvider. "
                "Set it via the api_key parameter or MISTRAL_API_KEY env var."
            )

        self._client = Mistral(  # type: ignore[misc]
            api_key=api_key,
            timeout_ms=timeout * 1000,  # mistralai uses milliseconds
        )

    def _build_messages(self, messages):
        # type: (List[ChatMessage]) -> List[Dict[str, Any]]
        """Convert ChatMessage list to Mistral-compatible format."""
        formatted = []  # type: List[Dict[str, Any]]
        for msg in messages:
            msg_dict = {"role": msg.role, "content": msg.content}  # type: Dict[str, Any]
            if msg.tool_calls is not None:
                msg_dict["tool_calls"] = msg.tool_calls
            if msg.tool_call_id is not None:
                msg_dict["tool_call_id"] = msg.tool_call_id
            if msg.name is not None:
                msg_dict["name"] = msg.name
            formatted.append(msg_dict)
        return formatted

    @retry_with_backoff()
    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Send a chat completion request to Mistral AI."""
        try:
            formatted_messages = self._build_messages(messages)

            params = {
                "model": model,
                "messages": formatted_messages,
            }

            # Add optional parameters
            if "temperature" in options:
                params["temperature"] = options["temperature"]
            if "top_p" in options:
                params["top_p"] = options["top_p"]
            if "max_tokens" in options:
                params["max_tokens"] = options["max_tokens"]
            if "stop" in options:
                params["stop"] = options["stop"]
            if "random_seed" in options:
                params["random_seed"] = options["random_seed"]
            if "tools" in options:
                params["tools"] = options["tools"]
            if "tool_choice" in options:
                params["tool_choice"] = options["tool_choice"]

            response = self._client.chat.complete(**params)

            # Extract content from response
            content = ""
            if response.choices and len(response.choices) > 0:
                choice = response.choices[0]
                if choice.message is not None and choice.message.content is not None:
                    content = choice.message.content

            # Extract usage
            usage_data = {}  # type: Dict[str, int]
            if response.usage is not None:
                usage_data = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                }

            # Extract finish reason
            finish_reason = "stop"
            if response.choices and len(response.choices) > 0:
                choice = response.choices[0]
                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason

            # Extract response ID
            response_id = ""
            if response.id is not None:
                response_id = response.id

            return ChatResponse(
                id=response_id,
                model=model,
                content=content,
                usage=usage_data,
                finish_reason=finish_reason,
            )
        except Exception as exc:
            raise _map_mistral_error(exc)

    @retry_with_backoff()
    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion from Mistral AI."""
        try:
            formatted_messages = self._build_messages(messages)

            params = {
                "model": model,
                "messages": formatted_messages,
            }

            if "temperature" in options:
                params["temperature"] = options["temperature"]
            if "top_p" in options:
                params["top_p"] = options["top_p"]
            if "max_tokens" in options:
                params["max_tokens"] = options["max_tokens"]
            if "stop" in options:
                params["stop"] = options["stop"]
            if "random_seed" in options:
                params["random_seed"] = options["random_seed"]
            if "tools" in options:
                params["tools"] = options["tools"]
            if "tool_choice" in options:
                params["tool_choice"] = options["tool_choice"]

            stream = self._client.chat.stream(**params)

            for chunk in stream:
                if chunk.data is not None and chunk.data.choices:
                    delta = chunk.data.choices[0].delta
                    if delta is not None and delta.content is not None:
                        yield delta.content
        except Exception as exc:
            raise _map_mistral_error(exc)

    def list_models(self):
        # type: () -> List[str]
        """List all known Mistral AI models."""
        return list(_MISTRAL_MODELS)

    def count_tokens(self, text):
        # type: (str) -> int
        """Estimate token count using character-based heuristic.

        For Mistral models, ~4 characters per token is a reasonable estimate.
        """
        if not text:
            return 0
        # Mistral average: ~4 chars per token
        return max(1, len(text) // 4)
