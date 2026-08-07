"""
OpenAI-compatible provider base class for berserker.

Provides a reusable base for any provider that implements the OpenAI chat completions API.
Subclasses only need to override __init__ (to set base_url + default models) and
 optionally list_models() (for providers without a models.list() endpoint).

Python 3.8.10 compatible: uses type comments, Optional, no match/case, no str.removeprefix().
"""

import logging
import ssl
from typing import List, Iterator, Optional, Dict, Any

logger = logging.getLogger(__name__)
from openai import OpenAI
from openai import AuthenticationError as OpenAIAuthError
from openai import RateLimitError as OpenAIRateLimitError
from openai import BadRequestError as OpenAIBadRequestError
from openai import APIError as OpenAIAPIError

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


def _map_openai_error(exc):
    # type: (Exception) -> Exception
    """Map OpenAI SDK exceptions to berserker provider error hierarchy."""
    if isinstance(exc, OpenAIAuthError):
        return AuthenticationError(str(exc))
    if isinstance(exc, OpenAIRateLimitError):
        retry_after = None  # type: Optional[float]
        resp = getattr(exc, "response", None)
        if resp is not None:
            retry_after_header = resp.headers.get("retry-after")
            if retry_after_header is not None:
                try:
                    retry_after = float(retry_after_header)
                except (ValueError, TypeError):
                    pass
        return RateLimitError(str(exc), retry_after=retry_after)
    if isinstance(exc, OpenAIBadRequestError):
        msg_lower = str(exc).lower()
        # Context window exceeded — the caller should compact and retry.
        # Non-matching BadRequestErrors fall through to the generic
        # OpenAIAPIError branch below (keeps model-not-found handling).
        if (
            "context length" in msg_lower
            or "maximum context" in msg_lower
            or "context_length" in msg_lower
            or "too many tokens" in msg_lower
            or "reduce the length" in msg_lower
        ):
            return ContextLengthExceeded(str(exc))
    if isinstance(exc, OpenAIAPIError):
        status_code = getattr(exc, "status_code", None)  # type: Optional[int]
        response_body = None  # type: Optional[str]
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                response_body = resp.text
            except Exception as e:
                logger.warning("Failed to get response body from OpenAI-compatible error: %s", e)
                pass
        # Check for model-not-found pattern in the error message
        msg = str(exc)
        if "model" in msg.lower() and (
            "not found" in msg.lower() or "does not exist" in msg.lower()
        ):
            return ModelNotFoundError(msg)
        return APIError(msg, status_code=status_code, response_body=response_body)
    # Re-raise unknown exceptions as-is
    return exc


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


class OpenAICompatibleProvider(Provider):
    """Base class for OpenAI-compatible API providers.

    Subclasses need only define __init__ calling super().__init__() with
    the appropriate base_url and model list. Example (~10 lines):

        class GroqProvider(OpenAICompatibleProvider):
            def __init__(self, api_key=None, **kwargs):
                super().__init__(
                    id='groq',
                    name='Groq',
                    base_url='https://api.groq.com/openai/v1',
                    api_key=api_key,
                    models=['llama-3.1-70b', 'llama-3.1-8b'],
                    **kwargs
                )

    Attributes:
        base_url: The API base URL for the OpenAI-compatible endpoint.
        api_key: API key for authentication.
        extra_headers: Optional dict of additional HTTP headers.
    """

    def __init__(
        self,
        id,  # type: str
        name,  # type: str
        base_url,  # type: str
        api_key=None,  # type: Optional[str]
        models=None,  # type: Optional[List[str]]
        timeout=60,  # type: int
        max_retries=3,  # type: int
        extra_headers=None,  # type: Optional[Dict[str, str]]
    ):
        # type: (...) -> None
        super(OpenAICompatibleProvider, self).__init__(
            id=id,
            name=name,
            models=models,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.base_url = base_url
        self.api_key = api_key
        self.extra_headers = extra_headers

        # Build the OpenAI SDK client
        client_kwargs = {
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }  # type: Dict[str, Any]
        if self.api_key is not None and self.api_key != "":
            client_kwargs["api_key"] = self.api_key
        else:
            # Some services (Ollama, LM Studio, etc.) don't require a real key.
            # Pass a placeholder so the OpenAI SDK doesn't complain.
            client_kwargs["api_key"] = "not-needed"
        if self.extra_headers is not None:
            client_kwargs["default_headers"] = self.extra_headers

        # Fix SSL/TLS compatibility for Python 3.8.10 win32
        # Create a custom httpx client with relaxed SSL settings
        try:
            import httpx
            # Create SSL context that's compatible with older Python versions
            ssl_context = ssl.create_default_context()
            # Some servers require specific TLS versions
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            
            # Create custom httpx transport with SSL context
            # httpx uses 'verify' parameter for SSL context
            transport = httpx.HTTPTransport(verify=ssl_context)
            httpx_client = httpx.Client(transport=transport)
            
            # Pass custom httpx client to OpenAI SDK
            client_kwargs["http_client"] = httpx_client
            logger.debug("Created custom httpx client with SSL context for %s", self.base_url)
        except Exception as e:
            logger.warning("Failed to create custom SSL transport: %s. Using default.", e)

        self._client = OpenAI(**client_kwargs)

    def _build_messages(self, messages):
        # type: (List[ChatMessage]) -> List[Dict[str, Any]]
        """Convert ChatMessage list to OpenAI API message dicts."""
        result = []  # type: List[Dict[str, Any]]
        logger.debug("[BUILD_MSGS] Building %d messages for API call:", len(messages))
        for i, msg in enumerate(messages):
            content = msg.content or ""
            d = {"role": msg.role, "content": content}  # type: Dict[str, Any]
            has_tc = msg.tool_calls is not None and len(msg.tool_calls) > 0 if msg.tool_calls else False
            has_tid = msg.tool_call_id is not None and msg.tool_call_id != ""
            if has_tc:
                d["tool_calls"] = msg.tool_calls
            if has_tid:
                d["tool_call_id"] = msg.tool_call_id
            if msg.name is not None:
                d["name"] = msg.name
            # Thinking mode (DeepSeek V4): reasoning_content must round-trip
            # on assistant messages or the API 400s the next tool-loop call.
            if msg.role == "assistant":
                rc = getattr(msg, "reasoning_content", None)
                if rc:
                    d["reasoning_content"] = rc
            # Log ALL messages with key tool-related fields
            extra = ""
            if msg.role == "assistant":
                extra = " tc={}".format(has_tc)
            elif msg.role == "tool":
                extra = " tid={} name={}".format(
                    msg.tool_call_id, msg.name if msg.name else "-")
            logger.debug(
                "[BUILD_MSGS] [%d] role=%-10s clen=%d content=%.80s%s",
                i, msg.role, len(content), content, extra,
            )
            # Warning: tool message without tool_call_id
            if msg.role == "tool" and not has_tid:
                logger.error(
                    "[BUILD_MSGS_ERR] messages[%d]: tool role but tool_call_id is None/empty!", i)
            result.append(d)
        return result
    def _build_options(self, model, **options):
        # type: (str, **Any) -> Dict[str, Any]
        """Build the kwargs dict for the OpenAI API call."""
        kwargs = {"model": model}  # type: Dict[str, Any]
        kwargs.update(options)
        return kwargs

    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Send a chat completion request and return the response.

        Args:
            messages: List of ChatMessage objects forming the conversation.
            model: Model identifier to use.
            **options: Additional provider-specific options (temperature, top_p, etc.).

        Returns:
            ChatResponse with the generated content and metadata.

        Raises:
            AuthenticationError: If the API key is invalid.
            RateLimitError: If rate limit is exceeded.
            APIError: On generic API errors.
            ModelNotFoundError: If the requested model does not exist.
        """
        try:
            api_messages = self._build_messages(messages)
            api_options = self._build_options(model, **options)

            logger.debug(
                "Sending chat request: base_url=%s, model=%s, messages=%d, timeout=%s, max_retries=%s",
                self.base_url, model, len(api_messages), self.timeout, self.max_retries,
            )

            response = self._client.chat.completions.create(messages=api_messages, **api_options)

            choice = response.choices[0]
            content = ""
            if choice.message is not None and choice.message.content is not None:
                content = choice.message.content

            # Extract tool_calls to unified format
            tool_calls = None  # type: Optional[List[Dict[str, Any]]]
            if hasattr(choice.message, "tool_calls") and choice.message.tool_calls is not None:
                tool_calls = []
                for tc in choice.message.tool_calls:
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

            # Leaked tool-markup recovery: when the API leaves native
            # tool-call markup in the content (DeepSeek DSML, Qwen XML —
            # long context, malformed markers, or a proxy that skipped
            # conversion), parse it back into tool_calls.
            finish_reason = choice.finish_reason if choice.finish_reason else "stop"
            if tool_calls is None:
                from berserker.provider.tool_markup import (
                    contains_tool_markup,
                    parse_tool_markup,
                )

                if contains_tool_markup(content):
                    cleaned, recovered = parse_tool_markup(content)
                    if recovered:
                        logger.warning(
                            "[tool_markup] API returned tool markup as plain content; "
                            "recovered %d tool call(s)",
                            len(recovered),
                        )
                        content = cleaned
                        tool_calls = recovered
                        finish_reason = "tool_calls"

            usage = {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            }  # type: Dict[str, int]

            return ChatResponse(
                id=response.id,
                model=response.model,
                content=content,
                tool_calls=tool_calls,
                usage=usage,
                finish_reason=finish_reason,
                reasoning_content=getattr(choice.message, "reasoning_content", None),
            )
        except Exception as exc:
            logger.error(
                "Chat request failed: base_url=%s, model=%s, error_type=%s, error=%s",
                self.base_url, model, type(exc).__name__, str(exc),
            )
            import traceback
            logger.debug("Full traceback: %s", traceback.format_exc())
            raise _map_openai_error(exc)

    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion, yielding text chunks.

        Args:
            messages: List of ChatMessage objects forming the conversation.
            model: Model identifier to use.
            **options: Additional provider-specific options.

        Yields:
            Text chunks as they are generated.

        Raises:
            AuthenticationError: If the API key is invalid.
            RateLimitError: If rate limit is exceeded.
            APIError: On generic API errors.
            ModelNotFoundError: If the requested model does not exist.
        """
        try:
            api_messages = self._build_messages(messages)
            api_options = self._build_options(model, **options)

            response_stream = self._client.chat.completions.create(
                messages=api_messages, stream=True, **api_options
            )

            for chunk in response_stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta is not None and delta.content is not None:
                        yield delta.content
        except Exception as exc:
            logger.error(
                "Chat request failed: base_url=%s, model=%s, error_type=%s, error=%s",
                self.base_url, model, type(exc).__name__, str(exc),
            )
            import traceback
            logger.debug("Full traceback: %s", traceback.format_exc())
            raise _map_openai_error(exc)

    def list_models(self):
        # type: () -> List[str]
        """List all available models for this provider.

        Tries the OpenAI models.list() API first. If the provider does not
        support this endpoint, falls back to the static models list provided
        at initialization.

        Returns:
            List of model name strings.
        """
        try:
            response = self._client.models.list()
            models = [m.id for m in response.data]
            if models:
                return models
        except Exception as e:
            # Provider doesn't support models.list() — fall back to static list
            logger.warning("models.list() not supported, using static list: %s", e)
            pass

        if not self.models:
            return []
        # self.models may contain dicts (config objects) or strings
        # Extract model name from config objects
        result = []  # type: List[str]
        for m in self.models:
            if isinstance(m, dict):
                name = m.get("name", "")
                if name:
                    result.append(name)
            elif isinstance(m, str):
                result.append(m)
        return result

    def map_tools(self, tools):
        # type: (List[Any]) -> List[Dict[str, Any]]
        """Convert Tool objects to OpenAI-compatible tool schema.

        Args:
            tools: List of Tool objects from ToolRegistry.

        Returns:
            List of OpenAI-compatible tool schema dicts.
        """
        return _map_tools(tools)

    def count_tokens(self, text):
        # type: (str) -> int
        """Estimate token count using character-based heuristic.

        Most OpenAI-compatible providers do not expose a token counting endpoint.
        This uses the common approximation of ~4 characters per token.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Approximate token count.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    def close(self):
        # type: () -> None
        """Close the underlying OpenAI SDK client and release connections."""
        if self._client is not None:
            self._client.close()

    def __repr__(self):
        # type: () -> str
        return "<OpenAICompatibleProvider id={} name={} base_url={}>".format(
            self.id, self.name, self.base_url
        )
