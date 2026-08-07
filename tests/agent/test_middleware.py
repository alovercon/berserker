"""
Tests for berserker.agent.middleware module.

Covers:
- LoggingMiddleware: logs messages without modifying them.
- FilteringMiddleware: drops or holds messages matching a pattern.
- TransformMiddleware: transforms message content.
- MiddlewareChain: chains multiple middleware together.
"""

import logging
import pytest

from berserker.agent.messaging import AgentMessage
from berserker.agent.middleware import (
    LoggingMiddleware,
    FilteringMiddleware,
    TransformMiddleware,
    MiddlewareChain,
)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def make_message(content="test content", from_agent="build", to_agent="plan"):
    # type: (str, str, str) -> AgentMessage
    """Factory function to create test messages."""
    return AgentMessage(
        from_agent=from_agent,
        to_agent=to_agent,
        session_id="sess-1",
        content=content,
    )


# ---------------------------------------------------------------------------
# LoggingMiddleware Tests
# ---------------------------------------------------------------------------


class TestLoggingMiddleware:
    """Tests for LoggingMiddleware."""

    def test_process_returns_message(self):
        """Test that process returns the message unchanged."""
        mw = LoggingMiddleware()
        msg = make_message("Hello world")
        result = mw.process(msg)
        assert result is msg

    def test_process_does_not_modify_content(self):
        """Test that process does not alter message content."""
        mw = LoggingMiddleware()
        msg = make_message("Original content")
        mw.process(msg)
        assert msg.content == "Original content"

    def test_process_logs_message(self, caplog):
        """Test that process logs message details."""
        caplog.set_level(logging.INFO)
        mw = LoggingMiddleware()
        msg = make_message("Hello world")
        mw.process(msg)
        assert "build" in caplog.text
        assert "plan" in caplog.text
        assert "Hello world" in caplog.text

    def test_custom_logger_name(self, caplog):
        """Test using a custom logger name."""
        caplog.set_level(logging.INFO)
        mw = LoggingMiddleware("test.logger")
        msg = make_message("test")
        mw.process(msg)
        # Should still log, just under a different logger name
        assert "test" in caplog.text


# ---------------------------------------------------------------------------
# FilteringMiddleware Tests
# ---------------------------------------------------------------------------


class TestFilteringMiddleware:
    """Tests for FilteringMiddleware."""

    def test_drop_matching_message(self):
        """Test that matching messages are dropped (return None)."""
        mw = FilteringMiddleware(r"secret", action="drop")
        msg = make_message("This contains secret data")
        result = mw.process(msg)
        assert result is None

    def test_drop_non_matching_message(self):
        """Test that non-matching messages pass through."""
        mw = FilteringMiddleware(r"secret", action="drop")
        msg = make_message("This is normal data")
        result = mw.process(msg)
        assert result is msg

    def test_hold_matching_message(self):
        """Test that matching messages are held (marked in metadata)."""
        mw = FilteringMiddleware(r"secret", action="hold")
        msg = make_message("This contains secret data")
        result = mw.process(msg)
        assert result is msg
        assert result.metadata.get("_held") is True

    def test_hold_non_matching_message(self):
        """Test that non-matching messages pass through unmodified."""
        mw = FilteringMiddleware(r"secret", action="hold")
        msg = make_message("This is normal data")
        result = mw.process(msg)
        assert result is msg
        assert "_held" not in result.metadata

    def test_default_action_is_drop(self):
        """Test that default action is drop."""
        mw = FilteringMiddleware(r"secret")
        msg = make_message("secret data")
        result = mw.process(msg)
        assert result is None

    def test_invalid_action_raises(self):
        """Test that invalid action raises ValueError."""
        with pytest.raises(ValueError, match="Action must be"):
            FilteringMiddleware(r"secret", action="invalid")

    def test_invalid_regex_raises(self):
        """Test that invalid regex pattern raises re.error."""
        import re
        with pytest.raises(re.error):
            FilteringMiddleware(r"[invalid")

    def test_case_sensitive_by_default(self):
        """Test that pattern matching is case-sensitive by default."""
        mw = FilteringMiddleware(r"SECRET", action="drop")
        msg = make_message("This has secret data")  # lowercase
        result = mw.process(msg)
        assert result is msg  # Should pass through

    def test_case_insensitive_pattern(self):
        """Test case-insensitive pattern with inline flag."""
        mw = FilteringMiddleware(r"(?i)SECRET", action="drop")
        msg = make_message("This has secret data")
        result = mw.process(msg)
        assert result is None

    def test_empty_content_no_match(self):
        """Test that empty content does not match patterns."""
        mw = FilteringMiddleware(r"secret", action="drop")
        msg = make_message("")
        result = mw.process(msg)
        assert result is msg

    def test_pattern_matches_anywhere(self):
        """Test that pattern matches anywhere in content."""
        mw = FilteringMiddleware(r"error", action="drop")
        msg = make_message("An error occurred in the system")
        result = mw.process(msg)
        assert result is None


# ---------------------------------------------------------------------------
# TransformMiddleware Tests
# ---------------------------------------------------------------------------


class TestTransformMiddleware:
    """Tests for TransformMiddleware."""

    def test_transform_uppercase(self):
        """Test transforming content to uppercase."""
        mw = TransformMiddleware(lambda s: s.upper())
        msg = make_message("hello world")
        result = mw.process(msg)
        assert result.content == "HELLO WORLD"

    def test_transform_strip(self):
        """Test stripping whitespace from content."""
        mw = TransformMiddleware(lambda s: s.strip())
        msg = make_message("  hello world  ")
        result = mw.process(msg)
        assert result.content == "hello world"

    def test_transform_replace(self):
        """Test replacing text in content."""
        mw = TransformMiddleware(lambda s: s.replace("old", "new"))
        msg = make_message("This is old text")
        result = mw.process(msg)
        assert result.content == "This is new text"

    def test_transform_empty_content(self):
        """Test transforming empty content."""
        mw = TransformMiddleware(lambda s: s.upper())
        msg = make_message("")
        result = mw.process(msg)
        assert result.content == ""

    def test_transform_returns_message(self):
        """Test that transform returns the same message object."""
        mw = TransformMiddleware(lambda s: s)
        msg = make_message("test")
        result = mw.process(msg)
        assert result is msg

    def test_transform_preserves_metadata(self):
        """Test that transform preserves message metadata."""
        mw = TransformMiddleware(lambda s: s.upper())
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="hello",
            metadata={"key": "value"},
        )
        result = mw.process(msg)
        assert result.metadata == {"key": "value"}


# ---------------------------------------------------------------------------
# MiddlewareChain Tests
# ---------------------------------------------------------------------------


class TestMiddlewareChain:
    """Tests for MiddlewareChain."""

    def test_empty_chain_passes_through(self):
        """Test that an empty chain passes messages through."""
        chain = MiddlewareChain()
        msg = make_message("test")
        result = chain.process(msg)
        assert result is msg

    def test_single_middleware(self):
        """Test chain with a single middleware."""
        chain = MiddlewareChain()
        chain.add(TransformMiddleware(lambda s: s.upper()))
        msg = make_message("hello")
        result = chain.process(msg)
        assert result.content == "HELLO"

    def test_multiple_middleware_order(self):
        """Test that middleware executes in registration order."""
        chain = MiddlewareChain()
        chain.add(TransformMiddleware(lambda s: s + " world"))
        chain.add(TransformMiddleware(lambda s: s.upper()))
        msg = make_message("hello")
        result = chain.process(msg)
        assert result.content == "HELLO WORLD"

    def test_chain_stops_on_drop(self):
        """Test that chain stops processing when middleware drops message."""
        chain = MiddlewareChain()
        chain.add(TransformMiddleware(lambda s: s.upper()))
        chain.add(FilteringMiddleware(r"SECRET", action="drop"))
        chain.add(TransformMiddleware(lambda s: s + " EXTRA"))

        msg = make_message("this is secret data")
        result = chain.process(msg)
        assert result is None

    def test_chain_passes_when_no_match(self):
        """Test that chain passes through when no middleware drops."""
        chain = MiddlewareChain()
        chain.add(TransformMiddleware(lambda s: s.upper()))
        chain.add(FilteringMiddleware(r"SECRET", action="drop"))
        chain.add(TransformMiddleware(lambda s: s + " EXTRA"))

        msg = make_message("this is normal data")
        result = chain.process(msg)
        assert result.content == "THIS IS NORMAL DATA EXTRA"

    def test_chain_len(self):
        """Test chain length reporting."""
        chain = MiddlewareChain()
        assert len(chain) == 0
        chain.add(LoggingMiddleware())
        assert len(chain) == 1
        chain.add(FilteringMiddleware(r"test"))
        assert len(chain) == 2

    def test_chain_with_logging(self, caplog):
        """Test chain with logging middleware."""
        caplog.set_level(logging.INFO)
        chain = MiddlewareChain()
        chain.add(LoggingMiddleware())
        chain.add(TransformMiddleware(lambda s: s.upper()))
        msg = make_message("hello")
        result = chain.process(msg)
        assert result.content == "HELLO"
        assert "hello" in caplog.text

    def test_chain_hold_then_transform(self):
        """Test chain with hold filter followed by transform."""
        chain = MiddlewareChain()
        chain.add(FilteringMiddleware(r"secret", action="hold"))
        chain.add(TransformMiddleware(lambda s: s.upper()))

        msg = make_message("this is secret")
        result = chain.process(msg)
        assert result is not None
        assert result.content == "THIS IS SECRET"
        assert result.metadata.get("_held") is True
