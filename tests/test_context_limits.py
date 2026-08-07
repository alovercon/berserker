"""Tests for context limits: auto-compaction trigger, token limits, dynamic truncation."""

import pytest

from berserker.tool.truncate import (
    count_tokens,
    truncate_output,
    truncate_result,
    calculate_dynamic_max_tokens,
    DEFAULT_MAX_TOKENS,
    DEFAULT_COMPACTION_BUFFER,
    TRUNCATION_MARKER,
)


# ---------------------------------------------------------------------------
# count_tokens Tests
# ---------------------------------------------------------------------------


class TestCountTokens:
    """Test token counting."""

    def test_empty_string(self):
        """Empty string should have 0 or 1 tokens."""
        result = count_tokens("")
        assert result >= 0

    def test_short_string(self):
        """Short string should have reasonable token count."""
        result = count_tokens("hello world")
        assert result >= 1

    def test_longer_string(self):
        """Longer string should have more tokens."""
        short = count_tokens("hello")
        long_text = count_tokens("hello " * 100)
        assert long_text > short

    def test_estimate_fallback(self):
        """Fallback estimation should be ~len//4."""
        text = "x" * 1000
        result = count_tokens(text)
        # Should be approximately len//4 = 250
        assert result >= 1
        assert result <= 1000  # Should never exceed character count


# ---------------------------------------------------------------------------
# truncate_output Tests
# ---------------------------------------------------------------------------


class TestTruncateOutput:
    """Test truncate_output function."""

    def test_short_text_unchanged(self):
        """Short text should pass through unchanged."""
        text = "Hello, World!"
        result, truncated = truncate_output(text, max_tokens=1000)
        assert result == text
        assert truncated is False

    def test_long_text_truncated(self):
        """Long text should be truncated with marker."""
        text = "x" * 10000
        result, truncated = truncate_output(text, max_tokens=100)
        assert truncated is True
        assert len(result) < len(text)
        assert "[Output truncated" in result

    def test_truncation_marker_present(self):
        """Truncated output should include the truncation marker."""
        text = "a" * 5000
        result, truncated = truncate_output(text, max_tokens=50)
        assert truncated is True
        assert "truncated" in result.lower()

    def test_exact_fit_not_truncated(self):
        """Text that exactly fits should not be truncated."""
        # 100 chars ≈ 25 tokens, well under 100
        text = "x" * 100
        result, truncated = truncate_output(text, max_tokens=100)
        assert truncated is False
        assert result == text

    def test_very_small_max_tokens(self):
        """When max_tokens is tiny, should still return something."""
        text = "hello world this is a test"
        result, truncated = truncate_output(text, max_tokens=1)
        assert truncated is True
        assert isinstance(result, str)
        assert len(result) > 0

    def test_multiline_truncation(self):
        """Should truncate at line boundaries when possible."""
        lines = ["Line {}: some content here".format(i) for i in range(100)]
        text = "\n".join(lines)
        result, truncated = truncate_output(text, max_tokens=50)
        assert truncated is True
        assert len(result) < len(text)


# ---------------------------------------------------------------------------
# truncate_result Tests
# ---------------------------------------------------------------------------


class TestTruncateResult:
    """Test truncate_result function."""

    def test_short_output_unchanged(self):
        """Short output should pass through."""
        result = {"output": "hello", "metadata": {}}
        truncated = truncate_result(result, max_tokens=1000)
        assert truncated["output"] == "hello"
        assert truncated["metadata"].get("truncated") is not True

    def test_long_output_truncated(self):
        """Long output should be truncated."""
        result = {"output": "x" * 10000, "metadata": {}}
        truncated = truncate_result(result, max_tokens=100)
        assert len(truncated["output"]) < 10000
        assert truncated["metadata"].get("truncated") is True

    def test_adds_metadata_flag(self):
        """Should add truncated flag to metadata."""
        result = {"output": "x" * 10000}
        truncated = truncate_result(result, max_tokens=50)
        assert "metadata" in truncated
        assert truncated["metadata"].get("truncated") is True

    def test_preserves_other_metadata(self):
        """Should preserve existing metadata keys."""
        result = {"output": "x" * 10000, "metadata": {"file_path": "/tmp/test.py"}}
        truncated = truncate_result(result, max_tokens=50)
        assert truncated["metadata"].get("file_path") == "/tmp/test.py"
        assert truncated["metadata"].get("truncated") is True


# ---------------------------------------------------------------------------
# calculate_dynamic_max_tokens Tests
# ---------------------------------------------------------------------------


class TestCalculateDynamicMaxTokens:
    """Test calculate_dynamic_max_tokens function."""

    def test_no_context_info_returns_default(self):
        """Should return default_max when context_info is None."""
        result = calculate_dynamic_max_tokens(None, default_max=4096, default_buffer=20000)
        assert result == 4096

    def test_available_space(self):
        """Should calculate available space correctly."""
        context_info = {
            "current_tokens": 50000,
            "context_limit": 128000,
            "compaction_buffer": 20000,
        }
        result = calculate_dynamic_max_tokens(context_info, default_max=4096, default_buffer=20000)
        # 128000 - 50000 - 20000 = 58000, max(58000, 4096) = 58000
        assert result == 58000

    def test_minimum_is_default_max(self):
        """Should never return less than default_max."""
        context_info = {
            "current_tokens": 120000,
            "context_limit": 128000,
            "compaction_buffer": 20000,
        }
        result = calculate_dynamic_max_tokens(context_info, default_max=4096, default_buffer=20000)
        # 128000 - 120000 - 20000 = -12000, max(-12000, 4096) = 4096
        assert result == 4096

    def test_custom_buffer(self):
        """Should use custom buffer when provided."""
        context_info = {
            "current_tokens": 50000,
            "context_limit": 128000,
            "compaction_buffer": 10000,
        }
        result = calculate_dynamic_max_tokens(context_info, default_max=4096, default_buffer=20000)
        # 128000 - 50000 - 10000 = 68000
        assert result == 68000

    def test_partial_context_info(self):
        """Should handle partial context_info gracefully."""
        context_info = {"current_tokens": 50000}
        result = calculate_dynamic_max_tokens(context_info, default_max=4096, default_buffer=20000)
        # context_limit defaults to 0, so available = 0 - 50000 - 20000 = -70000
        # max(-70000, 4096) = 4096
        assert result == 4096


# ---------------------------------------------------------------------------
# Constants Tests
# ---------------------------------------------------------------------------


class TestTruncateConstants:
    """Verify truncation constants."""

    def test_default_max_tokens(self):
        """DEFAULT_MAX_TOKENS should be reasonable."""
        assert DEFAULT_MAX_TOKENS == 4096

    def test_default_compaction_buffer(self):
        """DEFAULT_COMPACTION_BUFFER should be positive."""
        assert DEFAULT_COMPACTION_BUFFER == 20000

    def test_truncation_marker_format(self):
        """TRUNCATION_MARKER should contain max placeholder."""
        assert "{max}" in TRUNCATION_MARKER
        assert "truncated" in TRUNCATION_MARKER.lower()
