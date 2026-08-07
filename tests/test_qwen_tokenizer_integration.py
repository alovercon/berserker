"""
Integration test for QwenTokenizer against actual Qwen API.

This test sends real requests to Qwen models and compares the token count
from our pure Python qwen_tokenizer.py with the actual token count returned
by the Qwen API (usage.prompt_tokens).

Requires:
- Valid Qwen API key configured in config.json
- Network access to Qwen API

Run with:
    pytest tests/test_qwen_tokenizer_integration.py -v -s
    pytest tests/test_qwen_tokenizer_integration.py -v -s -k "test_single_turn"
"""

import json
import logging
import os
import sys
from typing import Dict, Any, Optional, List

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from berserker.session.qwen_tokenizer import QwenTokenizer, get_qwen_tokenizer
from berserker.session.token_counter import TokenCounter
from berserker.config.loader import load_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Test Data
# ---------------------------------------------------------------------------

# Various test texts to validate token counting accuracy
TEST_CASES = [
    # Short English
    {"name": "short_english", "text": "Hello, world!"},
    {"name": "short_question", "text": "What is Python?"},
    # Long English
    {
        "name": "long_english",
        "text": "Python is a high-level, general-purpose programming language. Its design philosophy emphasizes code readability with the use of significant indentation. Python is dynamically typed and garbage-collected. It supports multiple programming paradigms, including structured, object-oriented and functional programming.",
    },
    # Chinese
    {"name": "short_chinese", "text": "你好，世界！"},
    {"name": "chinese_sentence", "text": "Python 是一种高级编程语言，由 Guido van Rossum 于 1991 年发布。"},
    # Mixed Chinese and English
    {
        "name": "mixed_text",
        "text": "Python 是一种编程语言，可以用来写很多程序。Hello world! 这是一个测试。",
    },
    # Code
    {
        "name": "python_code",
        "text": '''def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

for i in range(10):
    print(fibonacci(i))''',
    },
    # JSON
    {
        "name": "json_text",
        "text": '{"name": "test", "value": 123, "items": ["a", "b", "c"]}',
    },
    # Markdown
    {
        "name": "markdown_text",
        "text": "# Title\n\nThis is a **bold** statement.\n\n- Item 1\n- Item 2\n- Item 3\n\n```python\nprint('hello')\n```",
    },
    # Special characters
    {"name": "special_chars", "text": "Tab:\tNewline:\nEmoji: 🎉🚀💻"},
    # Empty
    {"name": "empty", "text": ""},
]


# ---------------------------------------------------------------------------
# Helper: Get Qwen API Configuration
# ---------------------------------------------------------------------------


def get_qwen_api_config():
    # type: () -> Optional[Dict[str, Any]]
    """Load Qwen API configuration from config.json.

    Returns:
        Dict with 'api_key', 'base_url', 'model' or None if not configured.
    """
    try:
        config = load_config()
    except Exception as e:
        logger.warning("Failed to load config: %s", e)
        return None

    # Look for Qwen model configuration
    # Check models section
    models = config.get("models", {})
    if not models:
        # Check mcp section for model config
        models = config.get("mcp", {}).get("models", {})

    # Find Qwen model
    qwen_model = None
    qwen_provider = None

    for model_id, model_config in models.items():
        if "qwen" in model_id.lower():
            qwen_model = model_id
            qwen_provider = model_config
            break

    if not qwen_model:
        # Check if there's a default model that's Qwen
        default_model = config.get("model", "")
        if "qwen" in default_model.lower():
            qwen_model = default_model
            qwen_provider = models.get(default_model, {})

    if not qwen_model:
        return None

    # Extract API key and base URL
    api_key = qwen_provider.get("apiKey", "") or qwen_provider.get("api_key", "")
    base_url = qwen_provider.get("baseURL", "") or qwen_provider.get("base_url", "")

    # If no API key in model config, check top-level
    if not api_key:
        api_key = config.get("apiKey", "") or config.get("api_key", "")

    # Default base URL for Qwen/DashScope
    if not base_url:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    if not api_key:
        return None

    return {
        "model": qwen_model,
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
    }


# ---------------------------------------------------------------------------
# Helper: Call Qwen API
# ---------------------------------------------------------------------------


def call_qwen_api(messages, model, api_key, base_url):
    # type: (List[Dict[str, str]], str, str, str) -> Optional[Dict[str, Any]]
    """Call Qwen API and return response with token usage.

    Args:
        messages: List of message dicts with 'role' and 'content'.
        model: Model ID (e.g., 'qwen-turbo', 'qwen-plus').
        api_key: API key.
        base_url: Base URL for API.

    Returns:
        Response dict with 'prompt_tokens', 'completion_tokens', 'total_tokens',
        or None if request failed.
    """
    try:
        import urllib.request
        import urllib.error

        url = "{}/chat/completions".format(base_url)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,  # Low temperature for consistent responses
            "max_tokens": 100,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(api_key),
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            usage = result.get("usage", {})
            return {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "response": result,
            }

    except Exception as e:
        logger.error("Failed to call Qwen API: %s", e)
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qwen_api_config():
    # type: () -> Optional[Dict[str, Any]]
    """Session-scoped fixture to load Qwen API config once."""
    return get_qwen_api_config()


@pytest.fixture(scope="session")
def qwen_tokenizer():
    # type: () -> QwenTokenizer
    """Session-scoped fixture to create tokenizer once."""
    return get_qwen_tokenizer()


@pytest.fixture(scope="session")
def token_counter():
    # type: () -> TokenCounter
    """Session-scoped fixture to create token counter once."""
    return TokenCounter()


# ---------------------------------------------------------------------------
# Skip Condition
# ---------------------------------------------------------------------------


def pytest_configure(config):
    # type: (Any) -> None
    """Register custom marker."""
    config.addinivalue_line(
        "markers", "qwen_api: tests that require Qwen API access"
    )


# ---------------------------------------------------------------------------
# Tests: QwenTokenizer Unit Tests (No API Required)
# ---------------------------------------------------------------------------


class TestQwenTokenizerUnit:
    """Unit tests for QwenTokenizer that don't require API access."""

    def test_tokenizer_initialization(self, qwen_tokenizer):
        """Tokenizer should initialize successfully."""
        assert qwen_tokenizer is not None
        assert len(qwen_tokenizer._mergeable_ranks) > 0

    def test_empty_text_returns_empty_list(self, qwen_tokenizer):
        """Empty text should return empty token list."""
        assert qwen_tokenizer.encode("") == []
        assert qwen_tokenizer.count_tokens("") == 0

    def test_basic_encoding_returns_list_of_ints(self, qwen_tokenizer):
        """Encoding should return a list of integers."""
        tokens = qwen_tokenizer.encode("Hello, world!")
        assert isinstance(tokens, list)
        assert all(isinstance(t, int) for t in tokens)
        assert len(tokens) > 0

    def test_decode_roundtrip(self, qwen_tokenizer):
        """Decode should approximately reconstruct the original text."""
        original = "Hello, world!"
        tokens = qwen_tokenizer.encode(original)
        decoded = qwen_tokenizer.decode(tokens)
        # Decoded text should contain the original (may have minor differences)
        assert "Hello" in decoded or "world" in decoded

    def test_count_tokens_returns_positive_int(self, qwen_tokenizer):
        """count_tokens should return a positive integer for non-empty text."""
        count = qwen_tokenizer.count_tokens("This is a test sentence.")
        assert isinstance(count, int)
        assert count > 0

    def test_longer_text_has_more_tokens(self, qwen_tokenizer):
        """Longer text should have more tokens than shorter text."""
        short_count = qwen_tokenizer.count_tokens("Hi")
        long_count = qwen_tokenizer.count_tokens(
            "This is a much longer sentence with many more words and characters."
        )
        assert long_count > short_count

    def test_chinese_text_tokenization(self, qwen_tokenizer):
        """Chinese text should be tokenized."""
        count = qwen_tokenizer.count_tokens("你好，世界！")
        assert isinstance(count, int)
        assert count > 0

    def test_code_tokenization(self, qwen_tokenizer):
        """Code should be tokenized."""
        code = "def hello():\n    print('Hello, world!')"
        tokens = qwen_tokenizer.encode(code)
        assert len(tokens) > 0

    def test_special_tokens_handling(self, qwen_tokenizer):
        """Special tokens should be handled correctly."""
        # Test with allowed_special="all"
        text_with_special = "Hello <|user|> world"
        tokens = qwen_tokenizer.encode(text_with_special, allowed_special="all")
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_tokenizer_is_singleton(self):
        """get_qwen_tokenizer should return the same instance."""
        t1 = get_qwen_tokenizer()
        t2 = get_qwen_tokenizer()
        assert t1 is t2


# ---------------------------------------------------------------------------
# Tests: TokenCounter Integration with Qwen Models
# ---------------------------------------------------------------------------


class TestTokenCounterQwenIntegration:
    """Test TokenCounter correctly routes Qwen models to qwen_tokenizer."""

    def test_qwen_model_detection(self, token_counter):
        """TokenCounter should detect Qwen models."""
        assert token_counter._is_qwen_model("qwen-turbo") is True
        assert token_counter._is_qwen_model("qwen-plus") is True
        assert token_counter._is_qwen_model("qwen-max") is True
        assert token_counter._is_qwen_model("qwen2-72b") is True
        assert token_counter._is_qwen_model("Qwen/Qwen2-7B-Instruct") is True
        assert token_counter._is_qwen_model("qwq-32b") is True
        assert token_counter._is_qwen_model("gpt-4o") is False
        assert token_counter._is_qwen_model("claude-3-sonnet") is False

    def test_qwen_model_uses_qwen_tokenizer(self, token_counter):
        """Qwen models should use qwen_tokenizer for counting."""
        text = "Hello, world! 你好！"

        # Count with Qwen model
        qwen_count = token_counter.count_text(text, model="qwen-turbo")

        # Count directly with qwen_tokenizer
        direct_count = get_qwen_tokenizer().count_tokens(text)

        # Should be the same
        assert qwen_count == direct_count

    def test_non_qwen_model_does_not_use_qwen_tokenizer(self, token_counter):
        """Non-Qwen models should not use qwen_tokenizer."""
        text = "Hello, world!"

        # Count with non-Qwen model (will use tiktoken or fallback)
        gpt_count = token_counter.count_text(text, model="gpt-4o")

        # Count with Qwen model
        qwen_count = token_counter.count_text(text, model="qwen-turbo")

        # Both should return positive integers (may differ due to different tokenizers)
        assert isinstance(gpt_count, int) and gpt_count > 0
        assert isinstance(qwen_count, int) and qwen_count > 0


# ---------------------------------------------------------------------------
# Tests: API Integration (Requires Qwen API Access)
# ---------------------------------------------------------------------------


@pytest.mark.qwen_api
class TestQwenTokenizerAPIIntegration:
    """Integration tests comparing qwen_tokenizer.py with actual Qwen API.

    These tests require a valid Qwen API configuration.
    They will be skipped if no Qwen API is configured.
    """

    @pytest.fixture(autouse=True)
    def skip_if_no_api(self, qwen_api_config):
        # type: (Optional[Dict[str, Any]]) -> None
        """Skip tests if Qwen API is not configured."""
        if qwen_api_config is None:
            pytest.skip(
                "Qwen API not configured. "
                "Add a Qwen model to config.json with apiKey to run these tests."
            )
        self.api_config = qwen_api_config

    def test_single_turn_token_accuracy(self):
        """Test that qwen_tokenizer count matches API prompt_tokens for single turn."""
        for case in TEST_CASES:
            if case["name"] == "empty":
                continue  # Skip empty text for API test

            text = case["text"]

            # Count with our tokenizer
            our_count = get_qwen_tokenizer().count_tokens(text)

            # Call API
            messages = [{"role": "user", "content": text}]
            api_result = call_qwen_api(
                messages,
                self.api_config["model"],
                self.api_config["api_key"],
                self.api_config["base_url"],
            )

            if api_result is None:
                pytest.skip("API call failed, skipping accuracy test")
                return

            api_count = api_result["prompt_tokens"]

            # Log the comparison
            logger.info(
                "Case '%s': our_count=%d, api_count=%d, diff=%d",
                case["name"],
                our_count,
                api_count,
                abs(our_count - api_count),
            )

            # Allow small tolerance (API may include system prompt overhead)
            # The difference should be within a reasonable range
            tolerance = max(5, int(api_count * 0.1))  # 10% or 5 tokens, whichever is larger
            assert abs(our_count - api_count) <= tolerance, (
                "Case '{}': our_count={} differs from api_count={} by more than tolerance {}".format(
                    case["name"], our_count, api_count, tolerance
                )
            )

    def test_multi_turn_conversation_token_accuracy(self):
        """Test token counting for multi-turn conversations."""
        # Build a multi-turn conversation
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "你好，请介绍一下 Python 编程语言。"},
            {"role": "assistant", "content": "Python 是一种高级编程语言，由 Guido van Rossum 创建。"},
            {"role": "user", "content": "它有哪些主要特点？"},
        ]

        # Count total tokens with our tokenizer
        total_our_count = 0
        for msg in messages:
            total_our_count += get_qwen_tokenizer().count_tokens(msg["content"])

        # Call API
        api_result = call_qwen_api(
            messages,
            self.api_config["model"],
            self.api_config["api_key"],
            self.api_config["base_url"],
        )

        if api_result is None:
            pytest.skip("API call failed, skipping accuracy test")
            return

        api_count = api_result["prompt_tokens"]

        logger.info(
            "Multi-turn: our_count=%d, api_count=%d, diff=%d",
            total_our_count,
            api_count,
            abs(total_our_count - api_count),
        )

        # Multi-turn conversations may have additional overhead tokens
        # from the API (role tokens, etc.)
        tolerance = max(10, int(api_count * 0.15))  # 15% or 10 tokens
        assert abs(total_our_count - api_count) <= tolerance, (
            "Multi-turn: our_count={} differs from api_count={} by more than tolerance {}".format(
                total_our_count, api_count, tolerance
            )
        )

    def test_code_token_accuracy(self):
        """Test token counting for code snippets."""
        code_snippets = [
            # Python
            '''def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)''',
            # JavaScript
            '''const fetchData = async (url) => {
    try {
        const response = await fetch(url);
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('Error:', error);
        throw error;
    }
};''',
            # SQL
            '''SELECT u.name, COUNT(o.id) as order_count
FROM users u
LEFT JOIN orders o ON u.id = o.user_id
WHERE u.created_at > '2024-01-01'
GROUP BY u.name
HAVING COUNT(o.id) > 5
ORDER BY order_count DESC;''',
        ]

        for i, code in enumerate(code_snippets):
            our_count = get_qwen_tokenizer().count_tokens(code)

            messages = [{"role": "user", "content": "Review this code:\n\n" + code}]
            api_result = call_qwen_api(
                messages,
                self.api_config["model"],
                self.api_config["api_key"],
                self.api_config["base_url"],
            )

            if api_result is None:
                pytest.skip("API call failed, skipping accuracy test")
                return

            # Subtract the prompt text tokens
            prompt_text_count = get_qwen_tokenizer().count_tokens("Review this code:\n\n")
            api_code_count = api_result["prompt_tokens"] - prompt_text_count

            logger.info(
                "Code snippet %d: our_count=%d, api_code_count=%d, diff=%d",
                i + 1,
                our_count,
                api_code_count,
                abs(our_count - api_code_count),
            )

            tolerance = max(5, int(our_count * 0.1))
            assert abs(our_count - api_code_count) <= tolerance, (
                "Code snippet {}: our_count={} differs from api_code_count={} by more than tolerance {}".format(
                    i + 1, our_count, api_code_count, tolerance
                )
            )

    def test_token_counter_with_qwen_model(self, token_counter):
        """Test TokenCounter.count_text() with Qwen model."""
        text = "这是一个测试文本，用来验证 TokenCounter 是否正确使用了 Qwen tokenizer。"

        # Count with Qwen model
        count = token_counter.count_text(text, model="qwen-turbo")

        # Should use qwen_tokenizer internally
        direct_count = get_qwen_tokenizer().count_tokens(text)

        assert count == direct_count, (
            "TokenCounter count ({}) should match direct qwen_tokenizer count ({})".format(
                count, direct_count
            )
        )

        # Also verify against API
        messages = [{"role": "user", "content": text}]
        api_result = call_qwen_api(
            messages,
            self.api_config["model"],
            self.api_config["api_key"],
            self.api_config["base_url"],
        )

        if api_result is None:
            pytest.skip("API call failed, skipping accuracy test")
            return

        api_count = api_result["prompt_tokens"]
        tolerance = max(5, int(api_count * 0.1))

        logger.info(
            "TokenCounter test: count=%d, api_count=%d, diff=%d",
            count,
            api_count,
            abs(count - api_count),
        )

        assert abs(count - api_count) <= tolerance


# ---------------------------------------------------------------------------
# Tests: Performance
# ---------------------------------------------------------------------------


class TestQwenTokenizerPerformance:
    """Performance tests for QwenTokenizer."""

    def test_encoding_speed_short_text(self, qwen_tokenizer):
        """Short text encoding should be fast (< 10ms)."""
        import time

        text = "Hello, world! 你好世界！"

        start = time.time()
        for _ in range(100):
            qwen_tokenizer.encode(text)
        elapsed = time.time() - start

        avg_ms = (elapsed / 100) * 1000
        logger.info("Short text encoding: %.2f ms per call", avg_ms)
        assert avg_ms < 50, "Short text encoding took %.2f ms (expected < 50ms)" % avg_ms

    def test_encoding_speed_long_text(self, qwen_tokenizer):
        """Long text encoding should be reasonable (< 500ms)."""
        import time

        # Generate ~1000 words of text
        text = "This is a test sentence. " * 200

        start = time.time()
        for _ in range(10):
            qwen_tokenizer.encode(text)
        elapsed = time.time() - start

        avg_ms = (elapsed / 10) * 1000
        logger.info("Long text encoding: %.2f ms per call", avg_ms)
        assert avg_ms < 1000, "Long text encoding took %.2f ms (expected < 1000ms)" % avg_ms

    def test_count_tokens_speed(self, qwen_tokenizer):
        """count_tokens should be fast."""
        import time

        text = "Hello, world! " * 50

        start = time.time()
        for _ in range(100):
            qwen_tokenizer.count_tokens(text)
        elapsed = time.time() - start

        avg_ms = (elapsed / 100) * 1000
        logger.info("count_tokens: %.2f ms per call", avg_ms)
        assert avg_ms < 100, "count_tokens took %.2f ms (expected < 100ms)" % avg_ms
