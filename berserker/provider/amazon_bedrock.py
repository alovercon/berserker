"""
Amazon Bedrock provider for berserker.

Uses boto3 SDK with the default AWS credential chain
(env vars, ~/.aws/credentials, IAM role). Supports Claude and Titan models.
"""

import json
import time
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

# Wrap boto3 import — file must be importable even without boto3
try:
    import boto3  # type: ignore[import-not-found]
    import botocore.exceptions  # type: ignore[import-not-found]

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False
    boto3 = None  # type: ignore[assignment]
    botocore = None  # type: ignore[assignment]

# Known Bedrock model IDs
_BEDROCK_MODELS = [
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-sonnet-20240229-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "amazon.titan-text-express-v1",
]


def _map_boto3_error(exc):
    # type: (Exception) -> Exception
    """Map boto3/botocore exceptions to our error hierarchy."""
    if botocore is None:
        return APIError(str(exc))

    if isinstance(exc, botocore.exceptions.ClientError):
        resp = exc.response  # type: ignore[union-attr]
        code = resp.get("Error", {}).get("Code", "")
        message = resp.get("Error", {}).get("Message", str(exc))
        status_code = resp.get("ResponseMetadata", {}).get("HTTPStatusCode")

        if code in (
            "UnrecognizedClientException",
            "AccessDeniedException",
            "UnrecognizedClient",
            "InvalidSignatureException",
        ):
            return AuthenticationError(message)
        if code in (
            "ThrottlingException",
            "ThrottledException",
            "ProvisionedThroughputExceededException",
        ):
            return RateLimitError(message)
        if status_code is not None and status_code >= 500:
            return APIError(message, status_code=status_code)
        return APIError(message, status_code=status_code)

    if isinstance(exc, botocore.exceptions.ParamValidationError):
        return APIError(str(exc))

    return APIError(str(exc))


class AmazonBedrockProvider(Provider):
    """Amazon Bedrock provider using boto3 SDK.

    Uses the default AWS credential chain (environment variables,
    ~/.aws/credentials, IAM role). No hardcoded credentials.
    """

    def __init__(self, id=None, region_name=None, timeout=60, max_retries=3):
        # type: (Optional[str], Optional[str], int, int) -> None
        if not _BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for AmazonBedrockProvider. Install it with: pip install boto3"
            )

        provider_id = id if id is not None else "bedrock"
        super(AmazonBedrockProvider, self).__init__(
            id=provider_id,
            name="Amazon Bedrock",
            models=list(_BEDROCK_MODELS),
            timeout=timeout,
            max_retries=max_retries,
        )
        self.region_name = region_name

        # Build boto3 session using default credential chain
        session_kwargs = {}  # type: Dict[str, Any]
        if region_name is not None:
            session_kwargs["region_name"] = region_name

        self._session = boto3.Session(**session_kwargs)  # type: ignore[union-attr]
        self._bedrock_runtime = self._session.client(
            "bedrock-runtime",
            config=botocore.config.Config(  # type: ignore[union-attr]
                read_timeout=timeout,
                connect_timeout=timeout,
                retries={"max_attempts": max_retries},
            ),
        )

    def _build_claude_messages(self, messages):
        # type: (List[ChatMessage]) -> tuple
        """Convert ChatMessage list to Anthropic/Claude Bedrock format."""
        formatted = []  # type: List[Dict[str, Any]]
        system_prompt = None  # type: Optional[str]

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            elif msg.role == "user":
                formatted.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                formatted.append({"role": "assistant", "content": msg.content})
            elif msg.role == "tool":
                # Bedrock Claude treats tool results as user messages
                formatted.append(
                    {
                        "role": "user",
                        "content": msg.content,
                    }
                )

        return formatted, system_prompt

    def _build_titan_prompt(self, messages):
        # type: (List[ChatMessage]) -> str
        """Convert ChatMessage list to Amazon Titan prompt format."""
        parts = []  # type: List[str]
        for msg in messages:
            if msg.role == "system":
                parts.append("System: {}".format(msg.content))
            elif msg.role == "user":
                parts.append("User: {}".format(msg.content))
            elif msg.role == "assistant":
                parts.append("Assistant: {}".format(msg.content))
            elif msg.role == "tool":
                parts.append("Tool: {}".format(msg.content))
        parts.append("Assistant:")
        return "\n".join(parts)

    def _is_claude_model(self, model):
        # type: (str) -> bool
        """Check if the model is an Anthropic Claude model."""
        return model.startswith("anthropic.claude")

    def _is_titan_model(self, model):
        # type: (str) -> bool
        """Check if the model is an Amazon Titan model."""
        return model.startswith("amazon.titan")

    @retry_with_backoff()
    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Send a chat completion request to Bedrock."""
        if model not in self.models:
            raise ModelNotFoundError(
                "Model '{}' is not supported by Amazon Bedrock provider. "
                "Supported models: {}".format(model, self.models)
            )

        try:
            if self._is_claude_model(model):
                return self._chat_claude(messages, model, **options)
            elif self._is_titan_model(model):
                return self._chat_titan(messages, model, **options)
            else:
                return self._chat_claude(messages, model, **options)
        except Exception as exc:
            raise _map_boto3_error(exc)

    def _chat_claude(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Chat with Claude models via Bedrock."""
        formatted_messages, system_prompt = self._build_claude_messages(messages)

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": options.get("max_tokens", 4096),
            "messages": formatted_messages,
        }

        if system_prompt is not None:
            body["system"] = system_prompt

        if "temperature" in options:
            body["temperature"] = options["temperature"]
        if "top_p" in options:
            body["top_p"] = options["top_p"]
        if "top_k" in options:
            body["top_k"] = options["top_k"]

        response = self._bedrock_runtime.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

        response_body = json.loads(response["body"].read())
        content = ""
        if response_body.get("content"):
            for item in response_body["content"]:
                if item.get("type") == "text":
                    content += item.get("text", "")

        usage = response_body.get("usage", {})
        return ChatResponse(
            id=response.get("ResponseMetadata", {}).get("RequestId", ""),
            model=model,
            content=content,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
            finish_reason=response_body.get("stop_reason", "stop"),
        )

    def _chat_titan(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Chat with Amazon Titan models via Bedrock."""
        prompt = self._build_titan_prompt(messages)

        body = {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": options.get("max_tokens", 4096),
            },
        }

        if "temperature" in options:
            body["textGenerationConfig"]["temperature"] = options["temperature"]
        if "top_p" in options:
            body["textGenerationConfig"]["topP"] = options["top_p"]

        response = self._bedrock_runtime.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

        response_body = json.loads(response["body"].read())
        result = response_body.get("results", [{}])[0]
        content = result.get("outputText", "")

        token_count = result.get("tokenCount", {})
        return ChatResponse(
            id=response.get("ResponseMetadata", {}).get("RequestId", ""),
            model=model,
            content=content,
            usage={
                "prompt_tokens": token_count.get("inputTokenCount", 0),
                "completion_tokens": token_count.get("outputTokenCount", 0),
                "total_tokens": token_count.get("inputTokenCount", 0)
                + token_count.get("outputTokenCount", 0),
            },
            finish_reason=result.get("completionReason", "stop"),
        )

    @retry_with_backoff()
    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream a chat completion from Bedrock."""
        if model not in self.models:
            raise ModelNotFoundError(
                "Model '{}' is not supported by Amazon Bedrock provider. "
                "Supported models: {}".format(model, self.models)
            )

        try:
            if self._is_claude_model(model):
                for chunk in self._stream_claude(messages, model, **options):
                    yield chunk
            elif self._is_titan_model(model):
                for chunk in self._stream_titan(messages, model, **options):
                    yield chunk
            else:
                for chunk in self._stream_claude(messages, model, **options):
                    yield chunk
        except Exception as exc:
            raise _map_boto3_error(exc)

    def _stream_claude(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream from Claude models via Bedrock response stream."""
        formatted_messages, system_prompt = self._build_claude_messages(messages)

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": options.get("max_tokens", 4096),
            "messages": formatted_messages,
        }

        if system_prompt is not None:
            body["system"] = system_prompt

        if "temperature" in options:
            body["temperature"] = options["temperature"]
        if "top_p" in options:
            body["top_p"] = options["top_p"]
        if "top_k" in options:
            body["top_k"] = options["top_k"]

        response = self._bedrock_runtime.invoke_model_with_response_stream(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

        stream = response.get("body")
        if stream is None:
            return

        for event in stream:
            chunk = event.get("chunk")
            if chunk is None:
                continue

            payload = json.loads(chunk.get("bytes").decode("utf-8"))
            event_type = payload.get("type", "")

            if event_type == "content_block_delta":
                delta = payload.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        yield text

    def _stream_titan(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Stream from Amazon Titan models via Bedrock response stream."""
        prompt = self._build_titan_prompt(messages)

        body = {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": options.get("max_tokens", 4096),
            },
        }

        if "temperature" in options:
            body["textGenerationConfig"]["temperature"] = options["temperature"]
        if "top_p" in options:
            body["textGenerationConfig"]["topP"] = options["top_p"]

        response = self._bedrock_runtime.invoke_model_with_response_stream(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

        stream = response.get("body")
        if stream is None:
            return

        for event in stream:
            chunk = event.get("chunk")
            if chunk is None:
                continue

            payload = json.loads(chunk.get("bytes").decode("utf-8"))
            result = payload.get("result", {})
            text = result.get("outputText", "")
            if text:
                yield text

    def list_models(self):
        # type: () -> List[str]
        """List all known Bedrock models."""
        return list(_BEDROCK_MODELS)

    def count_tokens(self, text):
        # type: (str) -> int
        """Estimate token count using character-based heuristic.

        For Claude models, ~4 characters per token is a reasonable estimate.
        """
        if not text:
            return 0
        # Claude average: ~3.5-4 chars per token
        return max(1, len(text) // 4)
