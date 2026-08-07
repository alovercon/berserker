"""
Azure OpenAI provider for berserker.

Uses openai.AzureOpenAI from the openai SDK.
Endpoint format: https://{resource}.openai.azure.com/
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

# Known Azure OpenAI models
_AZURE_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-4",
    "gpt-35-turbo",
    "gpt-35-turbo-16k",
    "text-embedding-ada-002",
    "text-embedding-3-small",
    "text-embedding-3-large",
]


def _map_openai_error(exc):
    # type: (Exception) -> Exception
    """Map openai SDK exceptions to our error hierarchy."""
    # Import here to avoid circular imports and allow graceful degradation
    try:
        import openai  # type: ignore[import-not-found]
    except ImportError:
        return APIError(str(exc))

    if isinstance(exc, openai.AuthenticationError):
        return AuthenticationError(str(exc))
    if isinstance(exc, openai.PermissionDeniedError):
        return AuthenticationError(str(exc))
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
        return RateLimitError(str(exc), retry_after=retry_after)
    if isinstance(exc, openai.APIStatusError):
        status_code = getattr(exc, "status_code", None)
        if status_code is not None and status_code >= 500:
            return APIError(str(exc), status_code=status_code)
        if status_code == 404:
            return ModelNotFoundError(str(exc))
        return APIError(str(exc), status_code=status_code)
    if isinstance(exc, openai.APIConnectionError):
        return APIError(str(exc))
    if isinstance(exc, openai.Timeout):
        return APIError("Request timed out: {}".format(str(exc)))

    return APIError(str(exc))


class AzureOpenAIProvider(Provider):
    """Azure OpenAI provider using openai.AzureOpenAI SDK.

    Connects to Azure OpenAI service via the standard openai SDK.
    """

    def __init__(
        self,
        id=None,
        azure_endpoint=None,
        api_key=None,
        api_version="2024-06-01",
        timeout=60,
        max_retries=3,
    ):
        # type: (Optional[str], Optional[str], Optional[str], str, int, int) -> None
        try:
            import openai  # type: ignore[import-not-found]
        except ImportError:
            raise ImportError(
                "openai is required for AzureOpenAIProvider. Install it with: pip install openai"
            )

        provider_id = id if id is not None else "azure"
        super(AzureOpenAIProvider, self).__init__(
            id=provider_id,
            name="Azure OpenAI",
            models=list(_AZURE_MODELS),
            timeout=timeout,
            max_retries=max_retries,
        )

        self.api_version = api_version
        self.azure_endpoint = azure_endpoint
        self.api_key = api_key

        # Build endpoint URL if not provided
        endpoint = azure_endpoint  # type: Optional[str]
        if endpoint is None:
            raise ValueError(
                "azure_endpoint is required for AzureOpenAIProvider. "
                "Format: https://{resource}.openai.azure.com/"
            )

        # Ensure endpoint has trailing slash for consistency
        if not endpoint.endswith("/"):
            endpoint = endpoint + "/"

        self._client = openai.AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout,
            max_retries=max_retries,
        )

    def _build_messages(self, messages):
        # type: (List[ChatMessage]) -> List[Dict[str, Any]]
        """Convert ChatMessage list to OpenAI-compatible format."""
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
        """Send a chat completion request to Azure OpenAI."""
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
            if "frequency_penalty" in options:
                params["frequency_penalty"] = options["frequency_penalty"]
            if "presence_penalty" in options:
                params["presence_penalty"] = options["presence_penalty"]
            if "tools" in options:
                params["tools"] = options["tools"]
            if "tool_choice" in options:
                params["tool_choice"] = options["tool_choice"]

            response = self._client.chat.completions.create(**params)

            choice = response.choices[0]
            content = ""
            if choice.message is not None and choice.message.content is not None:
                content = choice.message.content

            usage_data = {}  # type: Dict[str, int]
            if response.usage is not None:
                usage_data = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            return ChatResponse(
                id=response.id,
                model=model,
                content=content,
                usage=usage_data,
                finish_reason=choice.finish_reason or "stop",
            )
        except Exception as exc:
            raise _map_openai_error(exc)

    @retry_with_backoff()
    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion from Azure OpenAI."""
        try:
            formatted_messages = self._build_messages(messages)

            params = {
                "model": model,
                "messages": formatted_messages,
                "stream": True,
            }

            if "temperature" in options:
                params["temperature"] = options["temperature"]
            if "top_p" in options:
                params["top_p"] = options["top_p"]
            if "max_tokens" in options:
                params["max_tokens"] = options["max_tokens"]
            if "stop" in options:
                params["stop"] = options["stop"]
            if "frequency_penalty" in options:
                params["frequency_penalty"] = options["frequency_penalty"]
            if "presence_penalty" in options:
                params["presence_penalty"] = options["presence_penalty"]
            if "tools" in options:
                params["tools"] = options["tools"]
            if "tool_choice" in options:
                params["tool_choice"] = options["tool_choice"]

            stream = self._client.chat.completions.create(**params)

            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta is not None and delta.content is not None:
                        yield delta.content
        except Exception as exc:
            raise _map_openai_error(exc)

    def list_models(self):
        # type: () -> List[str]
        """List all known Azure OpenAI models.

        Azure doesn't have a models.list() endpoint, so we return
        a static list of commonly deployed models.
        """
        return list(_AZURE_MODELS)

    def count_tokens(self, text):
        # type: (str) -> int
        """Estimate token count using character-based heuristic.

        For GPT models, ~4 characters per token is a reasonable estimate.
        """
        if not text:
            return 0
        # GPT average: ~4 chars per token
        return max(1, len(text) // 4)
