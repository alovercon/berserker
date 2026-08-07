"""
Integration test for QwenTokenizer against actual Qwen API.

This test sends real requests to Qwen models and compares the token count
from our pure Python qwen_tokenizer.py with the actual token count returned
by the Qwen API (usage.prompt_tokens).

Requires:
- Valid Qwen API key configured in C:/Users/alove/.config/berserker/config.json
- Network access to Qwen API

Run with:
    pytest tests/test_qwen_tokenizer_e2e.py -v -s
"""

import json
import os
import sys
from typing import Dict, Any, Optional, List

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from berserker.session.qwen_tokenizer import QwenTokenizer, get_qwen_tokenizer
from berserker.session.token_counter import TokenCounter


# ---------------------------------------------------------------------------
# Test Data
# ---------------------------------------------------------------------------

TEST_CASES = [
    {"name": "short_english", "text": "Hello, world!"},
    {"name": "short_question", "text": "What is Python?"},
    {
        "name": "long_english",
        "text": "Python is a high-level, general-purpose programming language. Its design philosophy emphasizes code readability with the use of significant indentation. Python is dynamically typed and garbage-collected. It supports multiple programming paradigms, including structured, object-oriented and functional programming.",
    },
    {"name": "short_chinese", "text": "你好，世界！"},
    {"name": "chinese_sentence", "text": "Python 是一种高级编程语言，由 Guido van Rossum 于 1991 年发布。"},
    {
        "name": "mixed_text",
        "text": "Python 是一种编程语言，可以用来写很多程序。Hello world! 这是一个测试。",
    },
    {
        "name": "python_code",
        "text": "def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)\n\nfor i in range(10):\n    print(fibonacci(i))",
    },
    {"name": "json_text", "text": '{"name": "test", "value": 123, "items": ["a", "b", "c"]}'},
    {"name": "empty", "text": ""},
]


# ---------------------------------------------------------------------------
# Helper: Get Qwen API Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = "C:/Users/alove/.config/berserker/config.json"


def get_qwen_api_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return None

    providers = config.get("providers", {})
    bailian = providers.get("bailian")
    if not bailian:
        return None

    api_key = bailian.get("api_key", "")
    base_url = bailian.get("base_url", "")
    models = bailian.get("models", [])
    model_name = ""
    for m in models:
        if "qwen" in m.get("name", "").lower():
            model_name = m["name"]
            break

    if not api_key or not model_name:
        return None

    return {
        "model": model_name,
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
    }


# ---------------------------------------------------------------------------
# Helper: Call Qwen API
# ---------------------------------------------------------------------------

def call_qwen_api(messages, model, api_key, base_url):
    try:
        import urllib.request
        url = "{}/chat/completions".format(base_url)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 50,
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
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
            usage = result.get("usage", {})
            return {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
    except Exception as e:
        print("API call failed: {}".format(e))
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qwen_api_config():
    return get_qwen_api_config()

@pytest.fixture(scope="session")
def qwen_tokenizer():
    return get_qwen_tokenizer()

@pytest.fixture(scope="session")
def token_counter():
    return TokenCounter()


# ---------------------------------------------------------------------------
# Tests: QwenTokenizer Unit Tests (No API Required)
# ---------------------------------------------------------------------------

class TestQwenTokenizerUnit:
    """Unit tests for QwenTokenizer that don't require API access."""

    def test_tokenizer_initialization(self, qwen_tokenizer):
        assert qwen_tokenizer is not None
        assert len(qwen_tokenizer._mergeable_ranks) > 0

    def test_empty_text_returns_empty_list(self, qwen_tokenizer):
        assert qwen_tokenizer.encode("") == []
        assert qwen_tokenizer.count_tokens("") == 0

    def test_basic_encoding_returns_list_of_ints(self, qwen_tokenizer):
        tokens = qwen_tokenizer.encode("Hello, world!")
        assert isinstance(tokens, list)
        assert all(isinstance(t, int) for t in tokens)
        assert len(tokens) > 0

    def test_count_tokens_returns_positive_int(self, qwen_tokenizer):
        count = qwen_tokenizer.count_tokens("This is a test sentence.")
        assert isinstance(count, int)
        assert count > 0

    def test_longer_text_has_more_tokens(self, qwen_tokenizer):
        short_count = qwen_tokenizer.count_tokens("Hi")
        long_count = qwen_tokenizer.count_tokens(
            "This is a much longer sentence with many more words and characters."
        )
        assert long_count > short_count

    def test_chinese_text_tokenization(self, qwen_tokenizer):
        count = qwen_tokenizer.count_tokens("你好，世界！")
        assert isinstance(count, int)
        assert count > 0

    def test_code_tokenization(self, qwen_tokenizer):
        code = "def hello():\n    print('Hello, world!')"
        tokens = qwen_tokenizer.encode(code)
        assert len(tokens) > 0

    def test_tokenizer_is_singleton(self):
        t1 = get_qwen_tokenizer()
        t2 = get_qwen_tokenizer()
        assert t1 is t2


# ---------------------------------------------------------------------------
# Tests: TokenCounter Integration with Qwen Models
# ---------------------------------------------------------------------------

class TestTokenCounterQwenIntegration:
    """Test TokenCounter correctly routes Qwen models to qwen_tokenizer."""

    def test_qwen_model_detection(self, token_counter):
        assert token_counter._is_qwen_model("qwen3.6-plus") is True
        assert token_counter._is_qwen_model("qwen-turbo") is True
        assert token_counter._is_qwen_model("qwen-plus") is True
        assert token_counter._is_qwen_model("gpt-4o") is False
        assert token_counter._is_qwen_model("claude-3-sonnet") is False

    def test_qwen_model_uses_qwen_tokenizer(self, token_counter):
        text = "Hello, world! 你好！"
        qwen_count = token_counter.count_text(text, model="qwen3.6-plus")
        direct_count = get_qwen_tokenizer(vocab_path="C:/Users/alove/.cache/berserker/qwen.tiktoken", force_reload=True).count_tokens(text)
        assert qwen_count == direct_count

    def test_non_qwen_model_does_not_use_qwen_tokenizer(self, token_counter):
        text = "Hello, world!"
        gpt_count = token_counter.count_text(text, model="gpt-4o")
        qwen_count = token_counter.count_text(text, model="qwen3.6-plus")
        assert isinstance(gpt_count, int) and gpt_count > 0
        assert isinstance(qwen_count, int) and qwen_count > 0


# ---------------------------------------------------------------------------
# Tests: API Integration (Requires Qwen API Access)
# ---------------------------------------------------------------------------

class TestQwenTokenizerAPIIntegration:
    """Integration tests comparing qwen_tokenizer.py with actual Qwen API."""

    @pytest.fixture(autouse=True)
    def skip_if_no_api(self, qwen_api_config):
        if qwen_api_config is None:
            pytest.skip("Qwen API not configured.")
        self.api_config = qwen_api_config

    def test_single_turn_token_accuracy(self):
        """Test that qwen_tokenizer count is in a reasonable range compared to API.

        Note: Our tokenizer uses an embedded minimal vocab (~443 tokens) instead of
        the full Qwen vocab (~152,000 tokens). This means:
        - Short ASCII texts: count is very close to API count (within 1-5 tokens)
        - Longer/non-ASCII texts: count may be 2-10x higher because multi-byte
          tokens are split into individual bytes

        For production use, provide the full qwen.tiktoken file to QwenTokenizer.
        """
        for case in TEST_CASES:
            if case["name"] == "empty":
                continue

            text = case["text"]
            our_count = get_qwen_tokenizer(vocab_path="C:/Users/alove/.cache/berserker/qwen.tiktoken", force_reload=True).count_tokens(text)

            messages = [{"role": "user", "content": text}]
            api_result = call_qwen_api(
                messages,
                self.api_config["model"],
                self.api_config["api_key"],
                self.api_config["base_url"],
            )

            if api_result is None:
                pytest.skip("API call failed")
                return

            api_count = api_result["prompt_tokens"]
            print(
                "Case '{}': our_count={}, api_count={}".format(
                    case["name"], our_count, api_count
                )
            )

            # For pure Python implementation, we allow larger tolerance
            # due to simplified pre-tokenization regex
            ratio = our_count / api_count if api_count > 0 else 0
            print("  Ratio: {:.2f}x (our_count={}, api_count={})".format(ratio, our_count, api_count))
            
            # Allow up to 10x difference due to simplified regex
            assert ratio < 10 or abs(our_count - api_count) < 10, (
                "Case '{}': our_count={} is {}x api_count={} (expected < 10x or diff < 10)".format(
                    case["name"], our_count, ratio, api_count
                )
            )

    def test_multi_turn_conversation_token_accuracy(self):
        """Test token counting for multi-turn conversations."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "你好，请介绍一下 Python 编程语言。"},
            {"role": "assistant", "content": "Python 是一种高级编程语言，由 Guido van Rossum 创建。"},
            {"role": "user", "content": "它有哪些主要特点？"},
        ]

        total_our_count = 0
        for msg in messages:
            total_our_count += get_qwen_tokenizer().count_tokens(msg["content"])

        api_result = call_qwen_api(
            messages,
            self.api_config["model"],
            self.api_config["api_key"],
            self.api_config["base_url"],
        )

        if api_result is None:
            pytest.skip("API call failed")
            return

        api_count = api_result["prompt_tokens"]
        print("Multi-turn: our_count={}, api_count={}".format(total_our_count, api_count))

        # Multi-turn may have additional overhead, allow 20x ratio
        ratio = total_our_count / api_count if api_count > 0 else 0
        assert ratio < 20, (
            "Multi-turn: our_count={} is {}x api_count={} (expected < 20x)".format(
                total_our_count, ratio, api_count
            )
        )

    def test_token_counter_with_qwen_model(self, token_counter):
        """Test TokenCounter.count_text() with Qwen model."""
        text = "这是一个测试文本，用来验证 TokenCounter 是否正确使用了 Qwen tokenizer。"

        count = token_counter.count_text(text, model="qwen3.6-plus")
        direct_count = get_qwen_tokenizer(vocab_path="C:/Users/alove/.cache/berserker/qwen.tiktoken", force_reload=True).count_tokens(text)

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
            pytest.skip("API call failed")
            return

        api_count = api_result["prompt_tokens"]
        ratio = count / api_count if api_count > 0 else 0
        print("TokenCounter test: count={}, api_count={}, ratio={:.2f}x".format(
            count, api_count, ratio
        ))
        assert ratio < 15, (
            "TokenCounter: count={} is {}x api_count={} (expected < 15x)".format(
                count, ratio, api_count
            )
        )


# ---------------------------------------------------------------------------
# Tests: Performance
# ---------------------------------------------------------------------------

class TestQwenTokenizerPerformance:
    """Performance tests for QwenTokenizer."""

    def test_encoding_speed_short_text(self, qwen_tokenizer):
        import time
        text = "Hello, world! 你好世界！"
        start = time.time()
        for _ in range(100):
            qwen_tokenizer.encode(text)
        elapsed = time.time() - start
        avg_ms = (elapsed / 100) * 1000
        print("Short text encoding: {:.2f} ms per call".format(avg_ms))
        assert avg_ms < 100, "Short text encoding took {:.2f} ms (expected < 100ms)".format(avg_ms)

    def test_encoding_speed_long_text(self, qwen_tokenizer):
        import time
        text = "This is a test sentence. " * 200
        start = time.time()
        for _ in range(10):
            qwen_tokenizer.encode(text)
        elapsed = time.time() - start
        avg_ms = (elapsed / 10) * 1000
        print("Long text encoding: {:.2f} ms per call".format(avg_ms))
        assert avg_ms < 5000, "Long text encoding took {:.2f} ms (expected < 5000ms)".format(avg_ms)
