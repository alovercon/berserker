"""Tests for SessionTitleGenerator: title generation, fallback, truncation, async."""

import time
import pytest
from unittest.mock import MagicMock, patch

from berserker.session.title_gen import SessionTitleGenerator, MAX_TITLE_LENGTH


class TestGenerateTitleSuccess:
    """Test successful title generation."""

    def test_generate_title_success(self):
        """Mock agent returns title, verify it's saved."""
        session_manager = MagicMock()
        session_manager.load.return_value = {
            "id": "test-session-123",
            "title": "",
        }
        session_manager.get_messages.return_value = [
            {"role": "user", "content": "Fix the bug in auth.py"},
        ]

        agent_manager = MagicMock()
        agent_manager.execute.return_value = {
            "content": "Auth bug fix",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        gen = SessionTitleGenerator(session_manager, agent_manager)
        result = gen.generate_title("test-session-123")

        assert result == "Auth bug fix"
        session_manager.update_title.assert_called_once_with("test-session-123", "Auth bug fix")

    def test_generate_title_already_has_title(self):
        """Session with existing title, verify no regeneration."""
        session_manager = MagicMock()
        session_manager.load.return_value = {
            "id": "test-session-456",
            "title": "Existing Title",
        }

        agent_manager = MagicMock()

        gen = SessionTitleGenerator(session_manager, agent_manager)
        result = gen.generate_title("test-session-456")

        assert result == "Existing Title"
        agent_manager.execute.assert_not_called()
        session_manager.update_title.assert_not_called()

    def test_generate_title_no_messages(self):
        """Empty session, verify fallback title."""
        session_manager = MagicMock()
        session_manager.load.return_value = {
            "id": "abc123def456",
            "title": "",
        }
        session_manager.get_messages.return_value = []

        agent_manager = MagicMock()

        gen = SessionTitleGenerator(session_manager, agent_manager)
        result = gen.generate_title("abc123def456")

        assert result == "Conversation abc123de"
        session_manager.update_title.assert_called_once_with("abc123def456", "Conversation abc123de")

    def test_generate_title_agent_error(self):
        """Agent raises exception, verify fallback title."""
        session_manager = MagicMock()
        session_manager.load.return_value = {
            "id": "test-session-err",
            "title": "",
        }
        session_manager.get_messages.return_value = [
            {"role": "user", "content": "Help me"},
        ]

        agent_manager = MagicMock()
        agent_manager.execute.side_effect = Exception("Agent failed")

        gen = SessionTitleGenerator(session_manager, agent_manager)
        result = gen.generate_title("test-session-err")

        assert result == "Conversation test-ses"
        session_manager.update_title.assert_called_once_with("test-session-err", "Conversation test-ses")

    def test_title_truncated_to_50_chars(self):
        """Agent returns 80-char title, verify truncation."""
        long_title = "A" * 80
        session_manager = MagicMock()
        session_manager.load.return_value = {
            "id": "test-session-trunc",
            "title": "",
        }
        session_manager.get_messages.return_value = [
            {"role": "user", "content": "Do something"},
        ]

        agent_manager = MagicMock()
        agent_manager.execute.return_value = {
            "content": long_title,
            "usage": {"prompt_tokens": 10, "completion_tokens": 80, "total_tokens": 90},
        }

        gen = SessionTitleGenerator(session_manager, agent_manager)
        result = gen.generate_title("test-session-trunc")

        assert len(result) == MAX_TITLE_LENGTH
        assert result == "A" * MAX_TITLE_LENGTH
        session_manager.update_title.assert_called_once_with("test-session-trunc", "A" * MAX_TITLE_LENGTH)

    def test_generate_title_async(self):
        """Verify async returns immediately without blocking."""
        session_manager = MagicMock()
        session_manager.load.return_value = {
            "id": "test-session-async",
            "title": "",
        }
        session_manager.get_messages.return_value = [
            {"role": "user", "content": "Async test"},
        ]

        agent_manager = MagicMock()
        # Simulate slow agent
        def slow_execute(*args, **kwargs):
            time.sleep(0.5)
            return {"content": "Async Title", "usage": {}}

        agent_manager.execute.side_effect = slow_execute

        gen = SessionTitleGenerator(session_manager, agent_manager)

        start = time.time()
        gen.generate_title_async("test-session-async")
        elapsed = time.time() - start

        # Should return immediately (well under 0.5s sleep)
        assert elapsed < 0.1

        # Wait for thread to finish so update_title is called
        time.sleep(0.7)
        session_manager.update_title.assert_called_once_with("test-session-async", "Async Title")
