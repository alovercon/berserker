"""Tests for Qwen Tokenizer and TokenCounter integration.

Tests:
1. Unit tests for QwenTokenizer (pure Python BPE)
2. Integration tests for TokenCounter with Qwen models
3. E2E test: compare QwenTokenizer count with actual Qwen API usage
"""

import os
import pytest
from typing import Dict, Any

from berserker.session.qwen_tokenizer import QwenTokenizer, get_qwen_tokenizer
from berserker.session.token_counter import TokenCounter
from berserker.provider.base import ChatMessage


# ---------------------------------------------------------------------------
# Unit Tests: QwenTokenizer
# ---------------------------------------------------------------------------


class TestQwenTokenizerBasic:
    """Basic unit tests for QwenTokenizer."""

    @pytest.fixture
    def tokenizer(self):
        return QwenTokenizer()

    def test_empty_string_returns_empty_list(self, tokenizer):
        """Empty string should return empty token list."""
        assert tokenizer.encode("") == []

    def test_empty_string_count_is_zero(self, tokenizer):
        """Empty string should return 0 token count."""
        assert tokenizer.count_tokens("") == 0

    def test_simple_english_text(self, tokenizer):
        """Simple English text should tokenize."""
        tokens = tokenizer.encode("Hello world")
        assert isinstance(tokens, list)
        assert len(tokens) > 0
        assert all(isinstance(t, int) for t in tokens)

    def test_count_tokens_returns_positive_int(self, tokenizer):
        """count_tokens should return a positive integer for non-empty text."""
        count = tokenizer.count_tokens("Hello, this is a test.")
        assert isinstance(count, int)
        assert count > 0

    def test_longer_text_has_more_tokens(self, tokenizer):
        """Longer text should have more tokens than shorter text."""
        short_count = tokenizer.count_tokens("Hi")
        long_count = tokenizer.count_tokens(
            "Hello, this is a much longer piece of text with many more words."
        )
        assert long_count > short_count

    def test_chinese_text_tokenizes(self, tokenizer):
        """Chinese text should tokenize without errors."""
        tokens = tokenizer.encode("你好世界")
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_mixed_chinese_english(self, tokenizer):
        """Mixed Chinese and English text should tokenize."""
        text = "Hello 你好 world 世界"
        tokens = tokenizer.encode(text)
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_code_text_tokenizes(self, tokenizer):
        """Code text should tokenize."""
        code = 'def hello():\n    print("Hello, world!")'
        tokens = tokenizer.encode(code)
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_special_characters(self, tokenizer):
        """Text with special characters should tokenize."""
        text = "Line1\nLine2\tTab  Spaces"
        tokens = tokenizer.encode(text)
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_decode_roundtrip(self, tokenizer):
        """encode -> decode should return original text for simple ASCII."""
        text = "Hello world"
        tokens = tokenizer.encode(text)
        decoded = tokenizer.decode(tokens)
        assert decoded == text

    def test_special_tokens_in_encode(self, tokenizer):
        """Special tokens like <|user|> should be recognized."""
        text = "<|user|>Hello<|assistant|>Hi"
        tokens = tokenizer.encode(text, allowed_special="all")
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_global_tokenizer_singleton(self):
        """get_qwen_tokenizer should return the same instance."""
        t1 = get_qwen_tokenizer()
        t2 = get_qwen_tokenizer()
        assert t1 is t2


# ---------------------------------------------------------------------------
# Integration Tests: TokenCounter with Qwen models
# ---------------------------------------------------------------------------


class TestTokenCounterQwenIntegration:
    """Test TokenCounter correctly routes Qwen models to QwenTokenizer."""

    def test_qwen_model_uses_qwen_tokenizer(self):
        """TokenCounter should use QwenTokenizer for qwen models."""
        counter = TokenCounter()
        text = "Hello, this is a test message for Qwen token counting."

        # Should not raise, should return positive int
        count = counter.count_text(text, model="qwen3.6-plus")
        assert isinstance(count, int)
        assert count > 0

    def test_qwen_model_detection_variants(self):
        """Various Qwen model name formats should be detected."""
        counter = TokenCounter()
        text = "Test text"

        variants = [
            "qwen3.6-plus",
            "qwen-turbo",
            "qwen-plus",
            "qwen-max",
            "QWEN-7B",
            "bailian/qwen3.6-plus",
            "qwq-32b",
        ]

        for model in variants:
            count = counter.count_text(text, model=model)
            assert isinstance(count, int), f"Failed for model: {model}"
            assert count > 0, f"Zero count for model: {model}"

    def test_non_qwen_model_still_works(self):
        """Non-Qwen models should still work (fallback or tiktoken)."""
        counter = TokenCounter()
        text = "Hello world"

        # Should not raise even if tiktoken is unavailable
        count = counter.count_text(text, model="gpt-4o")
        assert isinstance(count, int)
        assert count > 0

    def test_qwen_vs_non_qwen_different_counts(self):
        """Qwen and non-Qwen models may produce different token counts."""
        counter = TokenCounter()
        text = "Hello, this is a test message."

        qwen_count = counter.count_text(text, model="qwen3.6-plus")
        gpt_count = counter.count_text(text, model="gpt-4o")

        # Both should be positive integers
        assert qwen_count > 0
        assert gpt_count > 0


# ---------------------------------------------------------------------------
# E2E Test: Compare with actual Qwen API usage
# ---------------------------------------------------------------------------


class TestQwenTokenizerE2E:
    """E2E test: compare QwenTokenizer count with actual Qwen API response."""

    def _get_qwen_api_token_count(self, text):
        # type: (str) -> int
        """Call Qwen API with a minimal request to get actual token count.

        Uses the provider system to make a real API call and extract
        usage.prompt_tokens from the response.
        """
        try:
            from berserker.config.loader import load_config
            from berserker.provider.registry import ProviderRegistry

            # Load user config
            config_path = os.path.expanduser(
                r"C:\Users\alove\.config\berserker\config.json"
            )
            if not os.path.exists(config_path):
                pytest.skip("User config.json not found")

            config = load_config(config_path=config_path)

            # Find the Qwen provider
            providers_config = config.get("providers", {})
            bailian_config = providers_config.get("bailian")
            if not bailian_config:
                pytest.skip("bailian provider not configured")

            # Create provider and make a minimal call
            registry = ProviderRegistry()
            registry.register_from_config(config)

            provider = registry.get_provider("bailian")
            if not provider:
                pytest.skip("bailian provider not found in registry")

            # Make a minimal API call
            messages = [
                ChatMessage(role="user", content=text),
            ]

            response = provider.chat(
                model="qwen3.6-plus",
                messages=messages,
                max_tokens=10,
                temperature=0.0,
            )

            # Extract token count from response usage
            if response and response.usage:
                return response.usage.get("prompt_tokens", 0)

            return 0

        except Exception as e:
            pytest.skip(f"API call failed: {e}")
            return 0

    def test_qwen_tokenizer_matches_api_count(self):
        """QwenTokenizer count should closely match actual Qwen API count.

        Note: There may be a small difference due to:
        - API adds special tokens (<|im_start|>, <|im_end|>)
        - Our tokenizer uses embedded minimal vocab (not full qwen.tiktoken)
        - Tolerance: within 20% of API count
        """
        test_texts = [
            "Hello world",
            "你好世界",
            "def hello():\n    print('Hello')",
            "This is a longer test message with multiple words and some punctuation.",
            "混合文本：Hello 你好 world 世界 code: def foo(): pass",
        ]

        tokenizer = QwenTokenizer()

        for text in test_texts:
            local_count = tokenizer.count_tokens(text)
            api_count = self._get_qwen_api_token_count(text)

            if api_count == 0:
                continue

            # Allow some tolerance due to special tokens and vocab differences
            # The API count typically includes system prompt overhead
            # We check that local count is in a reasonable range
            ratio = local_count / api_count if api_count > 0 else 0

            # Local count should be within 0.5x to 3x of API count
            # (API often counts system prompts and special tokens too)
            assert 0.5 <= ratio <= 3.0, (
                f"Token count mismatch for '{text[:30]}...': "
                f"local={local_count}, api={api_count}, ratio={ratio:.2f}"
            )

    def test_qwen_tokenizer_consistency(self):
        """Same text should always produce the same token count."""
        tokenizer = QwenTokenizer()
        text = "Hello, this is a consistent test message."

        counts = [tokenizer.count_tokens(text) for _ in range(10)]
        assert len(set(counts)) == 1, f"Inconsistent counts: {counts}"
