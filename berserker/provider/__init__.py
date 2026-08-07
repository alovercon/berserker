"""
berserker.provider - LLM provider abstractions.

Re-exports all public symbols from berserker.provider.base.
"""

from berserker.provider.base import (
    # Error hierarchy
    ProviderError,
    AuthenticationError,
    RateLimitError,
    APIError,
    ModelNotFoundError,
    # Unified message format
    ChatMessage,
    ChatResponse,
    # Provider ABC
    Provider,
    # Retry logic
    retry_with_backoff,
)

__all__ = [
    # Errors
    "ProviderError",
    "AuthenticationError",
    "RateLimitError",
    "APIError",
    "ModelNotFoundError",
    # Messages
    "ChatMessage",
    "ChatResponse",
    # ABC
    "Provider",
    # Retry
    "retry_with_backoff",
]
