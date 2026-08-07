"""MockProvider for testing — implements Provider ABC with configurable responses."""

from typing import List, Iterator, Optional, Dict, Any
from berserker.provider.base import Provider, ChatMessage, ChatResponse


class MockProvider(Provider):
    """A mock LLM provider that returns pre-configured responses.

    Usage:
        provider = MockProvider(responses=[
            ChatResponse(id="1", model="test", content="Hello"),
            ChatResponse(id="2", model="test", content="World"),
        ])
        # Or with tool_calls:
        provider = MockProvider(responses=[
            ChatResponse(
                id="1", model="test", content="",
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path": "test.py"}'}}]
            ),
        ])
    """

    def __init__(
        self, responses=None, id="mock", name="MockProvider", models=None, timeout=60, max_retries=0
    ):
        # type: (Optional[List[ChatResponse]], str, str, Optional[List[str]], int, int) -> None
        super(MockProvider, self).__init__(
            id=id,
            name=name,
            models=models or ["mock-model"],
            timeout=timeout,
            max_retries=max_retries,
        )
        self._responses = responses or []  # type: List[ChatResponse]
        self._call_index = 0  # type: int
        self.chat_calls = []  # type: List[Dict[str, Any]]  # Records all chat() calls
        self.stream_calls = []  # type: List[Dict[str, Any]]  # Records all stream() calls

    def chat(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> ChatResponse
        """Return the next pre-configured response, or a default empty response."""
        self.chat_calls.append({"messages": messages, "model": model, "options": options})
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp
        # Default fallback response
        return ChatResponse(
            id="mock-{}".format(self._call_index),
            model=model,
            content="",
            finish_reason="stop",
        )

    def stream(self, messages, model, **options):
        # type: (List[ChatMessage], str, **Any) -> Iterator[str]
        """Yield chunks from the next pre-configured response content."""
        self.stream_calls.append({"messages": messages, "model": model, "options": options})
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            # Yield content in small chunks
            content = resp.content
            chunk_size = 10
            for i in range(0, len(content), chunk_size):
                yield content[i : i + chunk_size]
        else:
            yield ""

    def list_models(self):
        # type: () -> List[str]
        """Return the provider's model list."""
        return list(self.models)

    def count_tokens(self, text):
        # type: (str) -> int
        """Return a simple character-based token estimate."""
        return len(text) // 4

    def reset(self):
        # type: () -> None
        """Reset call index and call history for reuse in multiple tests."""
        self._call_index = 0
        self.chat_calls = []
        self.stream_calls = []

    def set_responses(self, responses):
        # type: (List[ChatResponse]) -> None
        """Replace the response queue for the next round of calls."""
        self._responses = responses
        self._call_index = 0
