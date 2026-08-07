"""
tests/test_auto_title_trigger — Tests for auto-title trigger integration.

Tests the trigger_auto_title function from session/title_gen.py.
"""

import pytest
from unittest.mock import MagicMock, patch, call


class TestTriggerAutoTitle:
    """Tests for the trigger_auto_title module-level helper."""

    def test_trigger_auto_title_calls_generate_async(self):
        """Test that trigger_auto_title calls generate_title_async."""
        from berserker.session.title_gen import trigger_auto_title, _title_triggered

        # Reset the triggered set for this test
        _title_triggered.clear()

        with patch("berserker.session.title_gen.SessionTitleGenerator") as mock_gen_cls:
            mock_gen = MagicMock()
            mock_gen_cls.return_value = mock_gen

            with patch("berserker.session.manager.session_manager") as mock_sm:
                mock_sm.load.return_value = {"id": "test-session-123", "title": ""}

                trigger_auto_title("test-session-123")

                mock_gen_cls.assert_called_once()
                mock_gen.generate_title_async.assert_called_once_with("test-session-123")

    def test_trigger_auto_title_skips_if_already_has_title(self):
        """Test that trigger_auto_title skips if session already has a title."""
        from berserker.session.title_gen import trigger_auto_title, _title_triggered

        # Reset the triggered set for this test
        _title_triggered.clear()

        with patch("berserker.session.title_gen.SessionTitleGenerator") as mock_gen_cls:
            with patch("berserker.session.manager.session_manager") as mock_sm:
                mock_sm.load.return_value = {"id": "test-session-456", "title": "Existing Title"}

                trigger_auto_title("test-session-456")

                mock_gen_cls.assert_not_called()

    def test_trigger_auto_title_skips_if_already_triggered(self):
        """Test that trigger_auto_title skips if already triggered for this session."""
        from berserker.session.title_gen import trigger_auto_title, _title_triggered

        # Reset the triggered set for this test
        _title_triggered.clear()

        # First trigger
        with patch("berserker.session.title_gen.SessionTitleGenerator") as mock_gen_cls:
            mock_gen = MagicMock()
            mock_gen_cls.return_value = mock_gen

            with patch("berserker.session.manager.session_manager") as mock_sm:
                mock_sm.load.return_value = {"id": "test-session-789", "title": ""}

                trigger_auto_title("test-session-789")
                mock_gen_cls.assert_called_once()

        # Second trigger should be skipped
        with patch("berserker.session.title_gen.SessionTitleGenerator") as mock_gen_cls2:
            trigger_auto_title("test-session-789")
            mock_gen_cls2.assert_not_called()

    def test_trigger_auto_title_handles_load_error_gracefully(self):
        """Test that trigger_auto_title handles session load errors gracefully."""
        from berserker.session.title_gen import trigger_auto_title, _title_triggered

        # Reset the triggered set for this test
        _title_triggered.clear()

        with patch("berserker.session.title_gen.SessionTitleGenerator") as mock_gen_cls:
            with patch("berserker.session.manager.session_manager") as mock_sm:
                mock_sm.load.side_effect = Exception("Database error")

                # Should not raise an exception
                trigger_auto_title("test-session-error")

                mock_gen_cls.assert_not_called()

    def test_trigger_auto_title_different_sessions_independent(self):
        """Test that different sessions are tracked independently."""
        from berserker.session.title_gen import trigger_auto_title, _title_triggered

        # Reset the triggered set for this test
        _title_triggered.clear()

        with patch("berserker.session.title_gen.SessionTitleGenerator") as mock_gen_cls:
            mock_gen = MagicMock()
            mock_gen_cls.return_value = mock_gen

            with patch("berserker.session.manager.session_manager") as mock_sm:
                mock_sm.load.return_value = {"id": "session-a", "title": ""}

                # Trigger for session A
                trigger_auto_title("session-a")
                assert mock_gen_cls.call_count == 1

                # Trigger for session B (should also trigger)
                mock_sm.load.return_value = {"id": "session-b", "title": ""}
                trigger_auto_title("session-b")
                assert mock_gen_cls.call_count == 2
