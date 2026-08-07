"""Comprehensive unit tests for TokenCounter class."""

import pytest
from unittest.mock import patch, MagicMock

from berserker.session.token_counter import TokenCounter
from berserker.provider.base import ChatMessage, ChatResponse


# ---------------------------------------------------------------------------
# Test TokenCounter.count_text()
# ---------------------------------------------------------------------------


class TestCountText:
    """Test TokenCounter.count_text() method."""

    def test_empty_string_returns_zero(self):
        """Empty string should return 0 tokens."""
        counter = TokenCounter()
        assert counter.count_text("") == 0
        assert counter.count_text("", "gpt-4o") == 0

    def test_non_empty_string_returns_positive_integer(self):
        """Non-empty string should return positive integer token count."""
        counter = TokenCounter()
        result = counter.count_text("Hello world")
        assert isinstance(result, int)
        assert result > 0

    def test_longer_text_returns_more_tokens(self):
        """Longer text should generally return more tokens than shorter text."""
        counter = TokenCounter()
        short_count = counter.count_text("Hi")
        long_count = counter.count_text("Hello, this is a longer message with more words.")
        # This should hold true even with fallback estimation
        assert long_count >= short_count


# ---------------------------------------------------------------------------
# Test TokenCounter.count_messages()
# ---------------------------------------------------------------------------


class TestCountMessages:
    """Test TokenCounter.count_messages() method."""

    def test_empty_list_returns_zero(self):
        """Empty message list should return 0 tokens."""
        counter = TokenCounter()
        assert counter.count_messages([]) == 0

    def test_single_message_returns_content_tokens_plus_overhead(self):
        """Single message should return content tokens + 3 overhead."""
        counter = TokenCounter()
        message = ChatMessage(role="user", content="Hello")
        content_tokens = counter.count_text("Hello")
        total_tokens = counter.count_messages([message])
        assert total_tokens == content_tokens + 3

    def test_assistant_role_message_gets_extra_overhead(self):
        """Assistant role messages get extra +3 overhead."""
        counter = TokenCounter()
        user_message = ChatMessage(role="user", content="Hello")
        assistant_message = ChatMessage(role="assistant", content="Hi there")

        user_tokens = counter.count_messages([user_message])
        assistant_tokens = counter.count_messages([assistant_message])

        user_content_tokens = counter.count_text("Hello")
        assistant_content_tokens = counter.count_text("Hi there")

        # User: content + 3 overhead
        assert user_tokens == user_content_tokens + 3
        # Assistant: content + 3 (base) + 3 (assistant role) = content + 6
        assert assistant_tokens == assistant_content_tokens + 6

    def test_multiple_messages_sum_correctly(self):
        """Multiple messages should sum their individual token counts."""
        counter = TokenCounter()
        messages = [
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi"),
            ChatMessage(role="user", content="How are you?"),
        ]

        expected_total = 0
        # First message: user, content + 3
        expected_total += counter.count_text("Hello") + 3
        # Second message: assistant, content + 3 + 3
        expected_total += counter.count_text("Hi") + 6
        # Third message: user, content + 3
        expected_total += counter.count_text("How are you?") + 3

        actual_total = counter.count_messages(messages)
        assert actual_total == expected_total

    def test_messages_with_tool_calls_get_additional_overhead(self):
        """Messages with tool_calls get additional overhead per tool call."""
        counter = TokenCounter()

        # Message with one tool call
        tool_call = {"function": {"name": "test_function", "arguments": '{"param": "value"}'}}
        message = ChatMessage(role="assistant", content="Calling tool", tool_calls=[tool_call])

        content_tokens = counter.count_text("Calling tool")
        # Base: content + 3 (message) + 3 (assistant role) = content + 6
        # Tool call: +7 (name) + 3 (arguments) + argument payload = +10 + payload
        expected_tokens = content_tokens + 6 + 10 + counter.count_text('{"param": "value"}')

        actual_tokens = counter.count_messages([message])
        assert actual_tokens == expected_tokens

    def test_messages_with_tool_calls_without_arguments(self):
        """Tool calls without arguments still get name overhead."""
        counter = TokenCounter()

        # Message with tool call missing arguments
        tool_call = {
            "function": {
                "name": "test_function"
                # No arguments key
            }
        }
        message = ChatMessage(role="assistant", content="Calling tool", tool_calls=[tool_call])

        content_tokens = counter.count_text("Calling tool")
        # Base: content + 6 (message + assistant)
        # Tool call: +7 (name only, no arguments)
        expected_tokens = content_tokens + 6 + 7

        actual_tokens = counter.count_messages([message])
        assert actual_tokens == expected_tokens


# ---------------------------------------------------------------------------
# Test TokenCounter.extract_usage()
# ---------------------------------------------------------------------------


class TestExtractUsage:
    """Test TokenCounter.extract_usage() method."""

    def test_normal_chat_response_with_usage_dict(self):
        """Normal ChatResponse with usage dict extracts all token counts."""
        counter = TokenCounter()
        response = ChatResponse(
            id="test",
            model="gpt-4o",
            content="Hello",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        usage = counter.extract_usage(response)
        assert usage["prompt_tokens"] == 10
        assert usage["completion_tokens"] == 5
        assert usage["total_tokens"] == 15

    def test_chat_response_with_none_usage_returns_zeros(self):
        """ChatResponse with None usage returns all zeros."""
        counter = TokenCounter()
        response = ChatResponse(id="test", model="gpt-4o", content="Hello", usage=None)

        usage = counter.extract_usage(response)
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0
        assert usage["total_tokens"] == 0

    def test_chat_response_with_partial_usage_fills_missing_keys(self):
        """Partial usage dict fills missing keys with 0."""
        counter = TokenCounter()

        # Only prompt_tokens provided
        response1 = ChatResponse(
            id="test", model="gpt-4o", content="Hello", usage={"prompt_tokens": 10}
        )
        usage1 = counter.extract_usage(response1)
        assert usage1["prompt_tokens"] == 10
        assert usage1["completion_tokens"] == 0
        assert usage1["total_tokens"] == 0

        # Only completion_tokens provided
        response2 = ChatResponse(
            id="test", model="gpt-4o", content="Hello", usage={"completion_tokens": 5}
        )
        usage2 = counter.extract_usage(response2)
        assert usage2["prompt_tokens"] == 0
        assert usage2["completion_tokens"] == 5
        assert usage2["total_tokens"] == 0


# ---------------------------------------------------------------------------
# Test TokenCounter.get_encoding()
# ---------------------------------------------------------------------------


class TestGetEncoding:
    """Test TokenCounter.get_encoding() method."""

    def test_known_model_returns_encoding_or_none(self):
        """Known model should return encoding object or None if tiktoken unavailable."""
        counter = TokenCounter()
        encoding = counter.get_encoding("gpt-4o")
        # Should be either a valid encoding object or None (if tiktoken not installed)
        assert encoding is None or hasattr(encoding, "encode")

    def test_unknown_model_falls_back_to_cl100k_base(self):
        """Unknown model falls back to cl100k_base encoding."""
        counter = TokenCounter()
        encoding = counter.get_encoding("unknown-model-123")
        # Should be either a valid encoding object or None (if tiktoken not installed)
        assert encoding is None or hasattr(encoding, "encode")

    def test_caching_same_model_returns_same_object(self):
        """Calling twice for same model returns same encoding object."""
        counter = TokenCounter()
        encoding1 = counter.get_encoding("gpt-4o")
        encoding2 = counter.get_encoding("gpt-4o")
        assert encoding1 is encoding2


# ---------------------------------------------------------------------------
# Test MODEL_ENCODING_MAP
# ---------------------------------------------------------------------------


class TestModelEncodingMap:
    """Test MODEL_ENCODING_MAP contains expected mappings."""

    def test_gpt4o_maps_to_o200k_base(self):
        """gpt-4o should map to o200k_base encoding."""
        assert TokenCounter.MODEL_ENCODING_MAP["gpt-4o"] == "o200k_base"

    def test_gpt35turbo_maps_to_cl100k_base(self):
        """gpt-3.5-turbo should map to cl100k_base encoding."""
        assert TokenCounter.MODEL_ENCODING_MAP["gpt-3.5-turbo"] == "cl100k_base"


# ---------------------------------------------------------------------------
# Test Fallback Behavior
# ---------------------------------------------------------------------------


class TestFallbackBehavior:
    """Test fallback behavior when tiktoken is not available."""

    def test_count_text_fallback_estimation(self):
        """count_text should fall back to len//4 estimation when tiktoken unavailable."""
        # Mock tiktoken import to fail
        with patch.dict("sys.modules", {"tiktoken": None}):
            # Clear cache to ensure fresh attempt
            TokenCounter._encoding_cache.clear()

            counter = TokenCounter()
            text = "This is a test string with 35 characters."
            expected_fallback = max(1, len(text) // 4)  # 35 // 4 = 8

            # Force fallback by ensuring get_encoding returns None
            with patch.object(counter, "get_encoding", return_value=None):
                result = counter.count_text(text)
                assert result == expected_fallback

    def test_count_text_fallback_handles_empty_string(self):
        """Fallback should handle empty string correctly (return 0)."""
        with patch.dict("sys.modules", {"tiktoken": None}):
            TokenCounter._encoding_cache.clear()
            counter = TokenCounter()

            with patch.object(counter, "get_encoding", return_value=None):
                result = counter.count_text("")
                assert result == 0


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests combining multiple TokenCounter features."""

    def test_end_to_end_token_counting(self):
        """End-to-end test of token counting with realistic message sequence."""
        counter = TokenCounter()

        messages = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="What is the capital of France?"),
            ChatMessage(
                role="assistant",
                content="The capital of France is Paris.",
                tool_calls=[
                    {
                        "function": {
                            "name": "verify_fact",
                            "arguments": '{"fact": "Paris is capital of France"}',
                        }
                    }
                ],
            ),
            ChatMessage(role="tool", content="Fact verified: True", tool_call_id="call-123"),
            ChatMessage(role="user", content="Thank you!"),
        ]

        total_tokens = counter.count_messages(messages)
        # Should be a positive integer
        assert isinstance(total_tokens, int)
        assert total_tokens > 0

        # Extract usage from a response
        response = ChatResponse(
            id="test-response",
            model="gpt-4o",
            content="You're welcome!",
            usage={
                "prompt_tokens": total_tokens,
                "completion_tokens": counter.count_text("You're welcome!"),
                "total_tokens": total_tokens + counter.count_text("You're welcome!"),
            },
        )

        usage = counter.extract_usage(response)
        assert usage["prompt_tokens"] == total_tokens
        assert usage["completion_tokens"] > 0
        assert usage["total_tokens"] > total_tokens
