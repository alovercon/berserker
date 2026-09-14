"""
Precise token counter with model-specific encoding support.

Implements TokenCounter class for accurate token counting across different LLM models.
Uses tiktoken when available, falls back to language-aware estimation otherwise.
For Qwen models, uses pure Python BPE tokenizer (no Rust extension required).

Python 3.8.10 compatible: uses type comments, Optional, Dict, List, no match/case.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Any, List, Optional

# Import ChatMessage and ChatResponse from provider base
from berserker.provider.base import ChatMessage, ChatResponse

# Import language-aware fallback estimator
from berserker.session.fallback_estimator import estimate_tokens

# Import Qwen tokenizer (pure Python, no Rust dependency)
from berserker.session.qwen_tokenizer import get_qwen_tokenizer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# Large-text fast-path threshold (chars). Beyond this, full tokenization
# buys nothing for context-budget decisions but costs real CPU: the
# pure-Python Qwen BPE path runs ~2s/MB (measured, win32 Py3.8). Route
# oversized payloads to the language-aware estimator (regex-based, ~ms).
_LARGE_TEXT_CHARS = 200000


class TokenCounter:
    """Precise token counter with model-specific encoding support."""

    # Model to encoding mapping for different LLM providers
    MODEL_ENCODING_MAP = {
        "gpt-4o": "o200k_base",
        "gpt-4o-mini": "o200k_base",
        "gpt-3.5-turbo": "cl100k_base",
        "gpt-3.5-turbo-16k": "cl100k_base",
        "gpt-4": "cl100k_base",
        "gpt-4-turbo": "cl100k_base",
        "claude-3-opus": "cl100k_base",
        "claude-3-sonnet": "cl100k_base",
        "claude-3-haiku": "cl100k_base",
    }

    # Class-level cache for tiktoken encodings
    _encoding_cache = {}  # type: Dict[str, Any]

    @staticmethod
    def _is_qwen_model(model):
        # type: (str) -> bool
        """Check if the model is a Qwen series model."""
        model_lower = model.lower()
        return (
            "qwen" in model_lower
            or "qwq" in model_lower
            or "qwen-turbo" in model_lower
            or "qwen-plus" in model_lower
            or "qwen-max" in model_lower
        )

    # Path to full Qwen vocab file (downloaded from HuggingFace)
    QWEN_VOCAB_PATH = os.path.join(
        os.path.expanduser("~"), ".cache", "berserker", "qwen.tiktoken"
    )

    @classmethod
    def _get_qwen_token_count(cls, text):
        # type: (str) -> int
        """Count tokens using Qwen tokenizer with full vocab if available."""
        try:
            # Use full vocab if available, otherwise fall back to embedded vocab
            vocab_path = cls.QWEN_VOCAB_PATH if os.path.exists(cls.QWEN_VOCAB_PATH) else None
            tokenizer = get_qwen_tokenizer(vocab_path=vocab_path, force_reload=False)
            return tokenizer.count_tokens(text)
        except Exception as e:
            logger.debug("Failed to use Qwen tokenizer: %s", e)
            return estimate_tokens(text)
    @classmethod
    def get_encoding(cls, model):
        # type: (str) -> Any
        """Get tiktoken encoding for the specified model.

        Args:
            model: Model name to get encoding for.

        Returns:
            Tiktoken encoding object, or None if unavailable.
        """
        if model in cls._encoding_cache:
            return cls._encoding_cache[model]

        try:
            import tiktoken

            # Get encoding name from model mapping, fallback to cl100k_base
            encoding_name = cls.MODEL_ENCODING_MAP.get(model, "cl100k_base")
            encoding = tiktoken.get_encoding(encoding_name)
            cls._encoding_cache[model] = encoding
            return encoding
        except Exception as e:
            logger.debug("Failed to load tiktoken encoding for model %s: %s", model, e)
            cls._encoding_cache[model] = None
            return None

    def count_text(self, text, model="gpt-4o"):
        # type: (str, str) -> int
        """Count tokens in text using model-specific encoding.

        Args:
            text: Text to count tokens for.
            model: Model name to use for encoding (default: "gpt-4o").

        Returns:
            Token count, falls back to language-aware estimation if tiktoken unavailable.
        """
        if not text:
            return 0

        # Large-text fast path: budget decisions don't need exact BPE counts
        # for huge tool outputs; skip the expensive tokenizers entirely.
        if len(text) > _LARGE_TEXT_CHARS:
            return estimate_tokens(text)

        # Check if this is a Qwen model
        if self._is_qwen_model(model):
            return self._get_qwen_token_count(text)

        encoding = self.get_encoding(model)
        if encoding is not None:
            try:
                return len(encoding.encode(text))
            except Exception as e:
                logger.debug("Failed to encode text with tiktoken: %s", e)

        # Fallback to language-aware estimation
        return estimate_tokens(text)

    def count_messages(self, messages, model="gpt-4o"):
        # type: (List[ChatMessage], str) -> int
        """Count total tokens for a message list including overhead.

        Args:
            messages: List of ChatMessage objects.
            model: Model name to use for encoding (default: "gpt-4o").

        Returns:
            Total token count including message overhead and content tokens.
        """
        total_tokens = 0

        for message in messages:
            # Per-message overhead: +3 tokens per message
            total_tokens += 3

            # Assistant role overhead: +3 tokens for assistant role
            if message.role == "assistant":
                total_tokens += 3

            # Content tokens
            if message.content:
                total_tokens += self.count_text(message.content, model)

            # Thinking-mode reasoning tokens (assistant messages)
            reasoning_content = getattr(message, "reasoning_content", None)
            if reasoning_content:
                total_tokens += self.count_text(reasoning_content, model)

            # Tool call overhead
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    # +7 tokens per tool call name
                    if isinstance(tool_call, dict) and "function" in tool_call:
                        function_info = tool_call["function"]
                        if isinstance(function_info, dict) and "name" in function_info:
                            total_tokens += 7
                            # +3 tokens per tool call argument
                            if "arguments" in function_info:
                                total_tokens += 3
                                # Count the actual argument payload tokens
                                arguments = function_info["arguments"]
                                if arguments:
                                    total_tokens += self.count_text(str(arguments), model)

        return total_tokens

    def extract_usage(self, response):
        # type: (ChatResponse) -> Dict[str, int]
        """Extract usage dict from ChatResponse.

        Args:
            response: ChatResponse object to extract usage from.

        Returns:
            Usage dict with keys: prompt_tokens, completion_tokens, total_tokens.
        """
        usage = response.usage
        if not usage:
            return {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }

        return {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
