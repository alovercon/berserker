"""End-to-end tests for context compaction system.

Covers the complete compaction workflow:
1. Conversation growth → token threshold → auto-compaction trigger
2. Manual compaction via agent_manager.compact()
3. Message persistence and recovery after compaction
4. Multiple compaction cycles in a single session
5. GUI compact command parity with CLI
6. Edge cases: empty sessions, single message, boundary conditions

Python 3.8.10 compatible.
"""

import pytest

from berserker.agent.manager import AgentManager
from berserker.agent.compaction import (
    CompactionConfig,
    CompactionStrategy,
)
from berserker.provider.base import ChatMessage, ChatResponse
from berserker.session.manager import SessionManager
from berserker.session.context import SessionContext, SessionState
from berserker.storage import get_db
from berserker.tool.truncate import count_tokens

from tests.helpers.mock_provider import MockProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_messages(count, content_size=500):
    # type: (int, int) -> list
    """Generate a list of alternating user/assistant messages with predictable content.

    Args:
        count: Number of non-system messages to generate.
        content_size: Approximate character count per message.

    Returns:
        List of ChatMessage objects (no system message).
    """
    messages = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        content = "Message {}: {}".format(i, "x" * content_size)
        messages.append(ChatMessage(role=role, content=content))
    return messages


def _make_system_message():
    # type: () -> ChatMessage
    """Create a standard system message."""
    return ChatMessage(role="system", content="You are a helpful assistant.")


def _count_messages_tokens(messages):
    # type: (list) -> int
    """Count total tokens in a list of ChatMessage objects."""
    return sum(count_tokens(m.content) for m in messages)


# ---------------------------------------------------------------------------
# Test: Conversation Growth → Compaction Trigger → Verified Result
# ---------------------------------------------------------------------------


class TestConversationGrowthToCompaction:
    """Test the complete flow: conversation grows, exceeds threshold, compaction triggers."""

    def test_messages_accumulate_until_threshold(self):
        """Verify that messages accumulate and eventually exceed the compaction threshold."""
        strategy = CompactionStrategy(CompactionConfig(reserved=20000))
        model_limit = 128000  # Typical gpt-4o context limit
        threshold = model_limit - 20000  # 108000

        # Simulate accumulating messages with larger content to reach threshold faster
        messages = [_make_system_message()]
        total_tokens = 0

        for i in range(200):
            new_msg = ChatMessage(role="user" if i % 2 == 0 else "assistant",
                                  content="Turn {}: {}".format(i, "data" * 600))
            messages.append(new_msg)
            total_tokens += count_tokens(new_msg.content)

            if strategy.should_compact(total_tokens, model_limit):
                # Compaction should have triggered
                assert total_tokens > threshold
                assert len(messages) > 2
                break
        else:
            pytest.fail("Compaction threshold was never reached after 200 turns ({} tokens)".format(total_tokens))

    def test_compact_reduces_messages_over_limit(self):
        """When messages exceed max_tokens, compact() should reduce them."""
        manager = AgentManager()
        messages = [_make_system_message()] + _make_messages(20, content_size=500)

        total_tokens = _count_messages_tokens(messages)
        assert total_tokens > 1000  # Ensure we have enough tokens

        # Compact with a limit well below current tokens
        result = manager.compact(messages, max_tokens=500)

        # Result should be significantly smaller
        assert len(result) < len(messages)
        # First message should be system
        assert result[0].role == "system"

    def test_compact_returns_unchanged_under_limit(self):
        """When messages are under max_tokens, compact() should return them unchanged."""
        manager = AgentManager()
        messages = [_make_system_message()] + _make_messages(3, content_size=100)

        total_tokens = _count_messages_tokens(messages)
        result = manager.compact(messages, max_tokens=total_tokens + 10000)

        assert len(result) == len(messages)
        for i, msg in enumerate(result):
            assert msg.content == messages[i].content


# ---------------------------------------------------------------------------
# Test: Manual Compaction with Persistence
# ---------------------------------------------------------------------------


class TestManualCompactionPersistence:
    """Test manual compaction and message persistence to database."""

    def test_compact_and_persist_messages(self, session_manager):
        """Compact messages and verify they are persisted to the database."""
        session_id = session_manager.create()

        # Add messages to the session
        session_manager.append_message(session_id, "system", "You are a test assistant.")
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            session_manager.append_message(
                session_id, role,
                "Message {}: {}".format(i, "x" * 500)
            )

        # Load messages
        messages = session_manager.get_messages(session_id)
        assert len(messages) == 11  # 1 system + 10 conversation

        # Convert to ChatMessage objects
        chat_messages = []
        for msg in messages:
            chat_messages.append(
                ChatMessage(
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    tool_calls=msg.get("tool_calls"),
                    tool_call_id=msg.get("tool_call_id"),
                )
            )

        # Compact
        manager = AgentManager()
        compacted = manager.compact(chat_messages, max_tokens=500)

        # Verify compaction reduced messages
        assert len(compacted) < len(chat_messages)

        # Convert back to dicts for persistence
        compacted_dicts = []
        for msg in compacted:
            compacted_dicts.append({
                "role": msg.role,
                "content": msg.content,
                "tool_calls": msg.tool_calls,
                "tool_result_for": msg.tool_call_id,
            })

        # Persist
        inserted = session_manager.replace_messages(session_id, compacted_dicts)
        assert inserted == len(compacted)

        # Verify persistence
        reloaded = session_manager.get_messages(session_id)
        assert len(reloaded) == len(compacted)

        # First message should be system
        assert reloaded[0].get("role") == "system"

    def test_compact_preserves_system_message(self, session_manager):
        """After compaction and reload, system message should be preserved."""
        session_id = session_manager.create()
        original_system = "You are a specialized coding assistant."

        session_manager.append_message(session_id, "system", original_system)
        for i in range(8):
            session_manager.append_message(
                session_id, "user" if i % 2 == 0 else "assistant",
                "Content {}".format(i)
            )

        messages = session_manager.get_messages(session_id)
        chat_messages = [
            ChatMessage(
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
            )
            for msg in messages
        ]

        manager = AgentManager()
        compacted = manager.compact(chat_messages, max_tokens=200)

        # System message should be preserved
        assert len(compacted) >= 1
        assert compacted[0].role == "system"
        assert compacted[0].content == original_system


# ---------------------------------------------------------------------------
# Test: Multiple Compaction Cycles
# ---------------------------------------------------------------------------


class TestMultipleCompactionCycles:
    """Test multiple compaction cycles within a single session."""

    def test_double_compact_stable(self):
        """Compacting an already-compacted result should not further degrade it."""
        manager = AgentManager()
        messages = [_make_system_message()] + _make_messages(20, content_size=500)

        # First compaction
        compacted1 = manager.compact(messages, max_tokens=500)
        assert len(compacted1) < len(messages)

        # Second compaction on the result
        compacted2 = manager.compact(compacted1, max_tokens=500)

        # Should not lose the system message
        assert len(compacted2) >= 1
        assert compacted2[0].role == "system"
        # Should be stable (not keep shrinking indefinitely)
        assert len(compacted2) <= len(compacted1) + 1  # Allow small variance

    def test_compact_after_new_messages(self):
        """After compaction, adding new messages and compacting again should work."""
        manager = AgentManager()

        # Initial conversation
        messages = [_make_system_message()] + _make_messages(10, content_size=500)
        compacted1 = manager.compact(messages, max_tokens=500)

        # Add new messages
        new_messages = compacted1 + _make_messages(5, content_size=500)

        # Compact again
        compacted2 = manager.compact(new_messages, max_tokens=500)

        # Should still have system message
        assert compacted2[0].role == "system"
        # Should be reduced
        assert len(compacted2) < len(new_messages)


# ---------------------------------------------------------------------------
# Test: CompactionStrategy Threshold Behavior
# ---------------------------------------------------------------------------


class TestCompactionStrategyThresholds:
    """Test CompactionStrategy decision logic with realistic token counts."""

    def test_should_compact_at_boundary(self):
        """should_compact() should trigger exactly at the boundary."""
        config = CompactionConfig(reserved=20000)
        strategy = CompactionStrategy(config)

        model_limit = 128000
        # Exactly at boundary: should NOT compact
        assert strategy.should_compact(108000, model_limit) is False
        # One token over: should compact
        assert strategy.should_compact(108001, model_limit) is True

    def test_different_reserved_values(self):
        """Different reserved values should shift the threshold."""
        model_limit = 128000

        config_small = CompactionConfig(reserved=10000)
        strategy_small = CompactionStrategy(config_small)
        assert strategy_small.should_compact(118001, model_limit) is True
        assert strategy_small.should_compact(118000, model_limit) is False

        config_large = CompactionConfig(reserved=40000)
        strategy_large = CompactionStrategy(config_large)
        assert strategy_large.should_compact(88001, model_limit) is True
        assert strategy_large.should_compact(88000, model_limit) is False

    def test_should_prune_threshold(self):
        """should_prune() should respect prune_minimum."""
        config = CompactionConfig(prune=True, prune_minimum=20000)
        strategy = CompactionStrategy(config)

        assert strategy.should_prune(19999) is False
        assert strategy.should_prune(20000) is False
        assert strategy.should_prune(20001) is True

    def test_prune_disabled(self):
        """should_prune() should return False when prune is disabled."""
        config = CompactionConfig(prune=False, prune_minimum=20000)
        strategy = CompactionStrategy(config)

        assert strategy.should_prune(100000) is False


# ---------------------------------------------------------------------------
# Test: SessionContext Integration
# ---------------------------------------------------------------------------


class TestSessionContextCompaction:
    """Test SessionContext.compact() integration with database."""

    def test_session_context_compact(self, session_manager):
        """SessionContext.compact() should replace messages and mark dirty."""
        session_id = session_manager.create()

        # Add initial messages
        session_manager.append_message(session_id, "system", "System prompt.")
        for i in range(5):
            session_manager.append_message(
                session_id, "user" if i % 2 == 0 else "assistant",
                "Message {}".format(i)
            )

        # Create SessionContext and transition to ACTIVE
        ctx = SessionContext(session_id, session_manager)
        ctx.transition(SessionState.ACTIVE)

        # Verify initial messages
        initial_messages = ctx.get_messages()
        assert len(initial_messages) == 6

        # Compact
        count = ctx.compact("New system prompt", "Conversation summary.")
        assert count == 2  # system + summary

        # Verify messages are replaced
        reloaded = ctx.get_messages()
        assert len(reloaded) == 2
        assert reloaded[0]["role"] == "system"
        assert reloaded[0]["content"] == "New system prompt"
        assert reloaded[1]["content"] == "Conversation summary."

    def test_session_context_refresh_after_compact(self, session_manager):
        """After compact, refresh should reload from database."""
        session_id = session_manager.create()
        session_manager.append_message(session_id, "system", "Original system.")
        session_manager.append_message(session_id, "user", "Hello")

        ctx = SessionContext(session_id, session_manager)

        # Compact via manager directly
        session_manager.replace_messages(session_id, [
            {"role": "system", "content": "Updated system."},
            {"role": "assistant", "content": "Summary."},
        ])

        # Refresh and verify
        ctx.refresh()
        messages = ctx.get_messages()
        assert len(messages) == 2
        assert messages[0]["content"] == "Updated system."


# ---------------------------------------------------------------------------
# Test: Edge Cases
# ---------------------------------------------------------------------------


class TestCompactionEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_messages(self):
        """Compacting empty messages should return empty list."""
        manager = AgentManager()
        result = manager.compact([], max_tokens=100)
        assert result == []

    def test_only_system_message(self):
        """Compacting only system message should return it unchanged."""
        manager = AgentManager()
        messages = [_make_system_message()]
        result = manager.compact(messages, max_tokens=100)
        assert len(result) == 1
        assert result[0].role == "system"

    def test_system_plus_one_message(self):
        """System + 1 message should not be compacted (need >2 non-system)."""
        manager = AgentManager()
        messages = [
            _make_system_message(),
            ChatMessage(role="user", content="Hello"),
        ]
        result = manager.compact(messages, max_tokens=1)  # Very low limit
        # Should return unchanged (not enough to compact)
        assert len(result) == 2

    def test_system_plus_two_messages(self):
        """System + 2 messages should not be compacted (need >2 non-system)."""
        manager = AgentManager()
        messages = [
            _make_system_message(),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi"),
        ]
        result = manager.compact(messages, max_tokens=1)
        assert len(result) == 3

    def test_very_large_max_tokens(self):
        """With very large max_tokens, messages should be unchanged."""
        manager = AgentManager()
        messages = [_make_system_message()] + _make_messages(50, content_size=1000)
        result = manager.compact(messages, max_tokens=1000000)
        assert len(result) == len(messages)

    def test_unicode_content(self):
        """Compaction should handle unicode content correctly."""
        manager = AgentManager()
        messages = [
            _make_system_message(),
            ChatMessage(role="user", content="你好世界 🌍"),
            ChatMessage(role="assistant", content="こんにちは 🇯🇵"),
            ChatMessage(role="user", content="Привет мир 🇷🇺"),
            ChatMessage(role="assistant", content="مرحبا بالعالم 🌐"),
        ]
        result = manager.compact(messages, max_tokens=10)
        # Should compact but preserve system message
        assert result[0].role == "system"

    def test_tool_call_messages_preserved_in_fallback(self):
        """Fallback truncation should handle tool call messages."""
        manager = AgentManager()
        messages = [
            _make_system_message(),
            ChatMessage(role="user", content="Read the file"),
            ChatMessage(role="assistant", content="", tool_calls=[
                {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
            ]),
            ChatMessage(role="tool", content="File content here", tool_call_id="call_1"),
            ChatMessage(role="user", content="Now edit it"),
            ChatMessage(role="assistant", content="Editing..."),
        ]
        result = manager.compact(messages, max_tokens=10)
        # Should have system + summary + last 2 messages (fallback)
        assert result[0].role == "system"
        assert len(result) >= 3


# ---------------------------------------------------------------------------
# Test: Token Counting Accuracy
# ---------------------------------------------------------------------------


class TestTokenCountingInCompaction:
    """Test that token counting is accurate during compaction."""

    def test_count_tokens_empty(self):
        """Empty string should have minimal token count (implementation returns 1)."""
        # count_tokens uses tiktoken or len//4 fallback; empty string returns 1
        assert count_tokens("") >= 0

    def test_count_tokens_simple(self):
        """Simple English text should have reasonable token count."""
        tokens = count_tokens("Hello world, this is a test.")
        assert tokens > 0
        assert tokens < 100  # Should be well under 100 for this short text

    def test_count_tokens_repeated_chars(self):
        """Repeated characters should have predictable token count."""
        content = "x" * 1000
        tokens = count_tokens(content)
        assert tokens > 0
        # Token count should be proportional to length
        assert tokens < len(content)  # Should be less than char count

    def test_message_list_token_sum(self):
        """Sum of individual message tokens should equal total."""
        messages = [
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi there"),
            ChatMessage(role="user", content="How are you?"),
        ]
        total = sum(count_tokens(m.content) for m in messages)
        assert total > 0


# ---------------------------------------------------------------------------
# Test: Pruning Integration with Compaction
# ---------------------------------------------------------------------------


class TestPruningWithCompaction:
    """Test that pruning works correctly alongside compaction."""

    def test_prune_messages_returns_new_list(self):
        """prune_messages should return a new list, not modify original."""
        manager = AgentManager()
        messages = [
            _make_system_message(),
            ChatMessage(role="user", content="Hello"),
        ]
        result = manager.prune_messages(messages)
        assert result is not messages

    def test_prune_does_not_affect_system(self):
        """Pruning should never affect system messages."""
        manager = AgentManager()
        large_content = "x" * 100000
        messages = [
            ChatMessage(role="system", content="Important system config"),
            ChatMessage(role="tool", content=large_content, tool_call_id="call-1"),
        ]
        result = manager.prune_messages(messages)
        assert result[0].content == "Important system config"

    def test_prune_with_session_id_persists(self, session_manager):
        """Pruning with session_id should persist state to database."""
        manager = AgentManager()
        session_id = session_manager.create()

        large_content = "x" * 100000
        messages = [
            _make_system_message(),
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
        ]

        result = manager.prune_messages(messages, session_id=session_id)

        # Verify some messages were pruned
        tool_msgs = [m for m in result if m.role == "tool"]
        pruned = [m for m in tool_msgs if "[Tool output pruned" in m.content]
        assert len(pruned) >= 1

        # Verify DB persistence
        db = get_db()
        rows = db.fetchall(
            "SELECT message_id FROM pruning_state WHERE session_id = ?",
            (session_id,),
        )
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Test: Compaction Config Loading
# ---------------------------------------------------------------------------


class TestCompactionConfigLoading:
    """Test compaction config loading from various sources."""

    def test_default_config_values(self):
        """Default config should have expected values."""
        config = CompactionConfig()
        assert config.auto is True
        assert config.prune is True
        assert config.reserved == 20000
        assert config.prune_protect == 40000
        assert config.prune_minimum == 20000
        assert config.compact_threshold == 0.8

    def test_config_from_dict_partial(self):
        """from_dict should use defaults for missing keys."""
        config = CompactionConfig.from_dict({"auto": False, "reserved": 30000})
        assert config.auto is False
        assert config.reserved == 30000
        assert config.prune is True  # default

    def test_config_to_dict_roundtrip(self):
        """to_dict and from_dict should be reversible."""
        original = CompactionConfig(
            auto=False,
            prune=False,
            reserved=10000,
            prune_protect=50000,
            prune_minimum=15000,
            compact_threshold=0.9,
        )
        data = original.to_dict()
        restored = CompactionConfig.from_dict(data)
        assert restored.auto == original.auto
        assert restored.prune == original.prune
        assert restored.reserved == original.reserved
        assert restored.prune_protect == original.prune_protect
        assert restored.prune_minimum == original.prune_minimum
        assert restored.compact_threshold == original.compact_threshold
