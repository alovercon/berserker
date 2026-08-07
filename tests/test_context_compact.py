"""Tests for context compaction: compact() and prune_messages()."""

import pytest

from berserker.provider.base import ChatMessage
from berserker.agent.manager import (
    AgentManager,
    PRUNE_PROTECT,
    PRUNE_MINIMUM,
    _DEFAULT_CONTEXT_LIMIT,
    _COMPACTION_BUFFER,
)
from berserker.tool.truncate import count_tokens


# ---------------------------------------------------------------------------
# compact() Tests
# ---------------------------------------------------------------------------


class TestCompact:
    """Test AgentManager.compact() method."""

    def test_under_limit_returns_unchanged(self):
        """Should return messages unchanged when under token limit."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="Hello!"),
        ]
        result = manager.compact(messages, max_tokens=100000)
        assert len(result) == 2
        assert result[0].content == "You are helpful."
        assert result[1].content == "Hello!"

    def test_few_messages_no_compaction(self):
        """Should not compact when there are only system + 1-2 messages."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System prompt"),
            ChatMessage(role="user", content="Hi"),
        ]
        # Even with a tiny limit, should not compact (need >2 non-system messages)
        result = manager.compact(messages, max_tokens=10)
        # Returns unchanged since not enough to compact
        assert len(result) == 2

    def test_compact_returns_result(self):
        """Should return a compacted list when over limit."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System prompt"),
            ChatMessage(role="user", content="Message 1"),
            ChatMessage(role="assistant", content="Response 1"),
            ChatMessage(role="user", content="Message 2"),
            ChatMessage(role="assistant", content="Response 2"),
        ]
        # With a very small max_tokens, should trigger compaction
        result = manager.compact(messages, max_tokens=10)
        # Should return at least system + summary (fallback: system + last 2)
        assert len(result) >= 2
        # First message should be system
        assert result[0].role == "system"

    def test_compact_preserves_system(self):
        """Should always preserve system messages."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="Important system config"),
            ChatMessage(role="user", content="A" * 1000),
            ChatMessage(role="assistant", content="B" * 1000),
            ChatMessage(role="user", content="C" * 1000),
        ]
        result = manager.compact(messages, max_tokens=100)
        assert result[0].role == "system"
        assert result[0].content == "Important system config"


# ---------------------------------------------------------------------------
# prune_messages() Tests
# ---------------------------------------------------------------------------


class TestPruneMessages:
    """Test AgentManager.prune_messages() method."""

    def test_no_pruning_under_minimum(self):
        """Should not prune when total content is below PRUNE_MINIMUM."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi there"),
            ChatMessage(role="tool", content="Small output", tool_call_id="call-1"),
        ]
        result = manager.prune_messages(messages)
        # All messages should be preserved (under PRUNE_MINIMUM)
        assert len(result) == len(messages)

    def test_protects_recent_tool_output(self):
        """Should protect the most recent tool output within PRUNE_PROTECT tokens."""
        manager = AgentManager()
        # Create enough tool output to trigger pruning
        large_content = "x" * (PRUNE_MINIMUM + 1000)  # Just over minimum
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Let me check"),
            ChatMessage(role="tool", content=large_content, tool_call_id="call-1"),
            ChatMessage(role="user", content="What did you find?"),
            ChatMessage(role="assistant", content="Found something"),
        ]
        result = manager.prune_messages(messages)
        # The last tool output should be protected (within PRUNE_PROTECT)
        # Find tool messages in result
        tool_msgs = [m for m in result if m.role == "tool"]
        # At least the recent one should be preserved
        assert len(tool_msgs) >= 1
        # The protected one should have full content
        if tool_msgs:
            assert (
                len(tool_msgs[-1].content) == len(large_content)
                or "[Tool output pruned" not in tool_msgs[-1].content
            )

    def test_prunes_old_tool_output(self):
        """Should prune old tool outputs when over PRUNE_MINIMUM."""
        manager = AgentManager()
        # Create multiple large tool outputs that exceed PRUNE_PROTECT total
        # Each ~30000 chars = ~7500 tokens estimate. 5 of them = ~37500 tokens
        # PRUNE_PROTECT = 40000, so the last few are protected but earlier ones get pruned
        large_content = "x" * 50000  # ~12500 tokens each
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Check 1"),
            ChatMessage(role="tool", content=large_content, tool_call_id="call-1"),
            ChatMessage(role="assistant", content="Check 2"),
            ChatMessage(role="tool", content=large_content, tool_call_id="call-2"),
            ChatMessage(role="assistant", content="Check 3"),
            ChatMessage(role="tool", content=large_content, tool_call_id="call-3"),
            ChatMessage(role="assistant", content="Check 4"),
            ChatMessage(role="tool", content=large_content, tool_call_id="call-4"),
            ChatMessage(role="assistant", content="Check 5"),
            ChatMessage(role="tool", content=large_content, tool_call_id="call-5"),
            ChatMessage(role="user", content="Done"),
        ]
        result = manager.prune_messages(messages)
        # Some tool messages should be pruned (earlier ones beyond PRUNE_PROTECT)
        tool_msgs = [m for m in result if m.role == "tool"]
        pruned_msgs = [m for m in tool_msgs if "[Tool output pruned" in m.content]
        assert len(pruned_msgs) >= 1

    def test_skips_skill_tool_output(self):
        """Should never prune tool outputs with name='skill'."""
        manager = AgentManager()
        large_content = "x" * 50000
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="tool", content=large_content, tool_call_id="call-1", name="skill"),
            ChatMessage(role="user", content="Hello"),
        ]
        result = manager.prune_messages(messages)
        # Skill tool output should never be pruned
        skill_msgs = [m for m in result if m.name == "skill"]
        assert len(skill_msgs) == 1
        assert "[Tool output pruned" not in skill_msgs[0].content

    def test_returns_new_list(self):
        """Should return a new list, not modify the original."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
        ]
        result = manager.prune_messages(messages)
        assert result is not messages


# ---------------------------------------------------------------------------
# Constants Tests
# ---------------------------------------------------------------------------


class TestCompactionConstants:
    """Verify compaction constants are reasonable."""

    def test_prune_protect_positive(self):
        """PRUNE_PROTECT should be a positive integer."""
        assert isinstance(PRUNE_PROTECT, int)
        assert PRUNE_PROTECT > 0

    def test_prune_minimum_positive(self):
        """PRUNE_MINIMUM should be a positive integer."""
        assert isinstance(PRUNE_MINIMUM, int)
        assert PRUNE_MINIMUM > 0

    def test_prune_minimum_less_than_protect(self):
        """PRUNE_MINIMUM should be less than PRUNE_PROTECT for effective pruning."""
        assert PRUNE_MINIMUM < PRUNE_PROTECT

    def test_default_context_limit(self):
        """Default context limit should be reasonable."""
        assert _DEFAULT_CONTEXT_LIMIT >= 16000  # At least 16K

    def test_compaction_buffer(self):
        """Compaction buffer should be positive."""
        assert _COMPACTION_BUFFER > 0
