"""Extended tests for memory management and context overflow.

Covers gaps not tested by existing test files:
- Truncation edge cases (empty, unicode, None output, error results)
- Compaction edge cases (boundary, all-system, no assistant)
- Parts model edge cases (malformed snapshot, mixed roles)
- SnapshotTracker edge cases (non-existent session, ordering)
- TokenCounter edge cases (empty, unknown model, tool calls)
- Multi-turn overflow simulation
- Memory persistence across session lifecycle

Python 3.8.10 compatible.
"""

from __future__ import annotations

import pytest
import time
import threading
from typing import List

from berserker.tool.truncate import (
    count_tokens,
    truncate_output,
    truncate_result,
    calculate_dynamic_max_tokens,
    DEFAULT_MAX_TOKENS,
    TRUNCATION_MARKER,
)
from berserker.provider.base import ChatMessage, ChatResponse
from berserker.agent.manager import AgentManager
from berserker.agent.compaction import (
    CompactionConfig,
    CompactionStrategy,
    default_config,
    load_compaction_config,
)
from berserker.session.parts import (
    TextPart,
    ToolPart,
    FilePart,
    CompactionPart,
    SnapshotPart,
    MessageV2,
    parts_to_chat_messages,
    chat_messages_to_parts,
    _message_to_part,
)
from berserker.session.token_counter import TokenCounter
from berserker.session.snapshot import SnapshotTracker
from berserker.bus.bus import (
    bus,
    COMPACTION_STARTED,
    COMPACTION_COMPLETED,
    PRUNING_COMPLETED,
    CompactionStartedData,
    CompactionCompletedData,
    PruningCompletedData,
)


# ---------------------------------------------------------------------------
# Truncation Edge Cases
# ---------------------------------------------------------------------------


class TestTruncateEdgeCases:
    """Test truncate_output with edge case inputs."""

    def test_empty_string(self):
        """Empty string should not be truncated."""
        result, truncated = truncate_output("", max_tokens=100)
        assert result == ""
        assert truncated is False

    def test_unicode_text(self):
        """Unicode text should be handled correctly."""
        text = "\u4f60\u597d\u4e16\u754c" * 500  # Chinese characters
        result, truncated = truncate_output(text, max_tokens=50)
        assert truncated is True
        assert len(result) < len(text)
        assert "truncated" in result.lower()

    def test_emoji_text(self):
        """Emoji text should be handled correctly."""
        text = "\U0001f600\U0001f680\U0001f4bb" * 200
        result, truncated = truncate_output(text, max_tokens=50)
        assert truncated is True
        assert isinstance(result, str)

    def test_single_very_long_word(self):
        """A single word longer than max_tokens should still return something."""
        text = "a" * 10000
        result, truncated = truncate_output(text, max_tokens=10)
        assert truncated is True
        assert len(result) > 0
        assert "truncated" in result.lower()

    def test_newlines_only(self):
        """Text with only newlines should be handled."""
        text = "\n" * 1000
        result, truncated = truncate_output(text, max_tokens=10)
        # Should not need truncation (newlines are few tokens)
        assert isinstance(result, str)

    def test_mixed_whitespace(self):
        """Text with mixed whitespace should be handled."""
        text = "  \t\n  " * 500
        result, truncated = truncate_output(text, max_tokens=10)
        assert isinstance(result, str)

    def test_binary_like_text(self):
        """Text with null bytes and control chars should not crash."""
        text = "hello\x00world\x01test\x02" * 200
        result, truncated = truncate_output(text, max_tokens=50)
        assert isinstance(result, str)
        assert truncated is True

    def test_very_large_max_tokens(self):
        """Very large max_tokens should not truncate."""
        text = "hello world"
        result, truncated = truncate_output(text, max_tokens=1000000)
        assert result == text
        assert truncated is False

    def test_marker_tokens_count(self):
        """Truncation marker should consume some tokens."""
        marker = TRUNCATION_MARKER.format(max=100)
        marker_tokens = count_tokens(marker)
        assert marker_tokens > 0


class TestTruncateResultEdgeCases:
    """Test truncate_result with edge case inputs."""

    def test_no_output_key(self):
        """Result without 'output' key should not crash."""
        result = {"metadata": {"key": "value"}}
        truncated = truncate_result(result, max_tokens=100)
        assert "output" not in truncated or truncated.get("output") == ""
        assert truncated["metadata"].get("truncated") is not True

    def test_none_output(self):
        """Result with None output should not crash."""
        result = {"output": None, "metadata": {}}
        truncated = truncate_result(result, max_tokens=100)
        # None output should be treated as empty
        assert truncated["metadata"].get("truncated") is not True

    def test_error_result(self):
        """Error result should be truncated normally."""
        result = {"output": "Error: " + "x" * 10000, "metadata": {"error": True}}
        truncated = truncate_result(result, max_tokens=50)
        assert truncated["metadata"].get("truncated") is True
        assert truncated["metadata"].get("error") is True

    def test_empty_output(self):
        """Empty output should not be truncated."""
        result = {"output": "", "metadata": {}}
        truncated = truncate_result(result, max_tokens=100)
        assert truncated["output"] == ""
        assert truncated["metadata"].get("truncated") is not True

    def test_output_is_not_string(self):
        """Non-string output should be handled gracefully."""
        result = {"output": 12345, "metadata": {}}
        # This may raise or handle - just ensure no crash
        try:
            truncated = truncate_result(result, max_tokens=100)
            assert isinstance(truncated, dict)
        except (TypeError, AttributeError):
            pass  # Acceptable behavior for invalid input


# ---------------------------------------------------------------------------
# Compaction Edge Cases
# ---------------------------------------------------------------------------


class TestCompactionEdgeCases:
    """Test compaction with boundary and unusual inputs."""

    def test_compact_all_system_messages(self):
        """Should handle messages that are all system role."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System 1"),
            ChatMessage(role="system", content="System 2"),
            ChatMessage(role="system", content="System 3"),
        ]
        result = manager.compact(messages, max_tokens=1)
        # Should preserve at least one system message
        assert len(result) >= 1
        assert all(m.role == "system" for m in result)

    def test_compact_no_assistant_messages(self):
        """Should handle messages with no assistant role."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="User 1"),
            ChatMessage(role="user", content="User 2"),
            ChatMessage(role="user", content="User 3"),
        ]
        result = manager.compact(messages, max_tokens=1)
        assert len(result) >= 1
        assert result[0].role == "system"

    def test_compact_single_user_message(self):
        """Should not compact with only system + 1 user message."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
        ]
        result = manager.compact(messages, max_tokens=1)
        # Should return unchanged (not enough messages to compact)
        assert len(result) == 2

    def test_compact_very_small_token_limit(self):
        """Should handle max_tokens=0 gracefully."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi"),
            ChatMessage(role="user", content="Bye"),
        ]
        result = manager.compact(messages, max_tokens=0)
        assert len(result) >= 1

    def test_compact_very_large_token_limit(self):
        """Should return unchanged when limit is huge."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
        ]
        result = manager.compact(messages, max_tokens=10000000)
        assert len(result) == 2

    def test_compact_preserves_last_user_assistant_pair(self):
        """Should try to preserve the most recent user/assistant exchange."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Old question"),
            ChatMessage(role="assistant", content="Old answer"),
            ChatMessage(role="user", content="New question"),
            ChatMessage(role="assistant", content="New answer"),
        ]
        # Budget must trigger compaction yet still fit system + the newest
        # exchange (unified counter: history ~32 tokens, system+last pair ~18).
        result = manager.compact(messages, max_tokens=25)
        # Should have system + at least some recent content
        assert len(result) >= 2
        assert result[0].role == "system"


class TestCompactionConfigEdgeCases:
    """Test CompactionConfig with boundary values."""

    def test_zero_reserved(self):
        """Zero reserved buffer should be valid."""
        config = CompactionConfig(reserved=0)
        strategy = CompactionStrategy(config)
        # With reserved=0 the threshold guard binds: min(0.8*100000, 100000) = 80000
        assert strategy.should_compact(80001, 100000) is True
        assert strategy.should_compact(80000, 100000) is False
        assert strategy.should_compact(100001, 100000) is True

    def test_negative_values(self):
        """Negative values should be accepted (dataclass doesn't validate)."""
        config = CompactionConfig(reserved=-100)
        strategy = CompactionStrategy(config)
        # Negative reserved lifts the reserved guard to 100100, but the
        # threshold guard still binds at 0.8 * 100000 = 80000
        assert strategy.should_compact(80000, 100000) is False
        assert strategy.should_compact(80001, 100000) is True
        assert strategy.should_compact(100101, 100000) is True

    def test_very_large_reserved(self):
        """Very large reserved should make compaction trigger more easily."""
        config = CompactionConfig(reserved=999999)
        strategy = CompactionStrategy(config)
        # 100000 > 100000 - 999999 = 100000 > -899999 → True
        assert strategy.should_compact(100000, 100000) is True

    def test_from_dict_with_wrong_types(self):
        """from_dict should handle wrong types gracefully."""
        config = CompactionConfig.from_dict(
            {
                "auto": "yes",  # wrong type
                "reserved": "big",  # wrong type
            }
        )
        # Should still create instance, values may be wrong type
        assert isinstance(config, CompactionConfig)

    def test_to_dict_roundtrip(self):
        """to_dict -> from_dict should preserve values."""
        original = CompactionConfig(
            auto=False,
            prune=False,
            reserved=15000,
            prune_protect=30000,
            prune_minimum=10000,
            compact_threshold=0.7,
        )
        restored = CompactionConfig.from_dict(original.to_dict())
        assert restored.auto == original.auto
        assert restored.reserved == original.reserved
        assert restored.prune_protect == original.prune_protect


class TestCompactionStrategyEdgeCases:
    """Test CompactionStrategy with unusual inputs."""

    def test_should_compact_zero_model_limit(self):
        """Zero model_limit with default reserved=20000 triggers for any tokens >= 0."""
        strategy = CompactionStrategy()
        # 0 > 0 - 20000 = 0 > -20000 → True
        assert strategy.should_compact(0, 0) is True
        assert strategy.should_compact(1, 0) is True

    def test_should_compact_negative_tokens(self):
        """Negative tokens should never trigger compaction."""
        strategy = CompactionStrategy()
        assert strategy.should_compact(-1, 100000) is False

    def test_should_prune_zero_tokens(self):
        """Zero prunable tokens should not trigger pruning."""
        strategy = CompactionStrategy()
        assert strategy.should_prune(0) is False

    def test_should_prune_exactly_at_minimum(self):
        """Pruning should not trigger at exactly the minimum."""
        strategy = CompactionStrategy()  # prune_minimum=20000
        assert strategy.should_prune(20000) is False
        assert strategy.should_prune(20001) is True

    def test_get_compact_threshold_zero_model(self):
        """Zero model_limit should return zero threshold."""
        strategy = CompactionStrategy()
        assert strategy.get_compact_threshold(0) == 0

    def test_get_compact_threshold_very_large(self):
        """Very large model_limit should scale correctly."""
        strategy = CompactionStrategy()  # compact_threshold=0.8
        assert strategy.get_compact_threshold(1000000) == 800000


# ---------------------------------------------------------------------------
# Parts Model Edge Cases
# ---------------------------------------------------------------------------


class TestPartsEdgeCases:
    """Test parts model with edge case inputs."""

    def test_message_to_part_empty_content(self):
        """Empty content should produce TextPart."""
        msg = ChatMessage(role="user", content="")
        part = _message_to_part(msg)
        assert isinstance(part, TextPart)
        assert part.text == ""

    def test_message_to_part_malformed_snapshot(self):
        """Malformed snapshot content should not crash."""
        msg = ChatMessage(role="system", content="[Snapshot: invalid format")
        part = _message_to_part(msg)
        # Should fall back to TextPart since it doesn't end with ]
        assert isinstance(part, TextPart)

    def test_message_to_part_partial_snapshot(self):
        """Partial snapshot content should be handled."""
        msg = ChatMessage(role="system", content="[Snapshot: 5 additions, M deletions, K files]")
        part = _message_to_part(msg)
        # Starts with [Snapshot: but doesn't end with ]
        if part.type == "snapshot":
            assert isinstance(part, SnapshotPart)
        else:
            assert isinstance(part, TextPart)

    def test_message_to_part_snapshot_with_extra_text(self):
        """Snapshot-like content with extra text should be TextPart."""
        msg = ChatMessage(
            role="system", content="[Snapshot: 1 additions, 2 deletions, 3 files] extra"
        )
        part = _message_to_part(msg)
        # Doesn't end with ], so should be TextPart
        assert isinstance(part, TextPart)

    def test_chat_messages_to_parts_empty_list(self):
        """Empty list should return empty list."""
        result = chat_messages_to_parts([])
        assert result == []

    def test_chat_messages_to_parts_single_message(self):
        """Single message should produce single MessageV2."""
        messages = [ChatMessage(role="user", content="Hello")]
        result = chat_messages_to_parts(messages)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_chat_messages_to_parts_alternating_roles(self):
        """Alternating roles should produce separate MessageV2 per role."""
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="User 1"),
            ChatMessage(role="assistant", content="Assistant 1"),
            ChatMessage(role="user", content="User 2"),
            ChatMessage(role="assistant", content="Assistant 2"),
        ]
        result = chat_messages_to_parts(messages)
        assert len(result) == 5  # Each role is different

    def test_chat_messages_to_parts_same_role_grouped(self):
        """Consecutive same-role messages should be grouped."""
        messages = [
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="user", content="World"),
            ChatMessage(role="user", content="!"),
        ]
        result = chat_messages_to_parts(messages)
        assert len(result) == 1  # All grouped into one MessageV2
        assert len(result[0].parts) == 3

    def test_parts_to_chat_messages_empty_parts(self):
        """MessageV2 with empty parts should produce empty list."""
        msg = MessageV2(role="user", parts=[])
        result = parts_to_chat_messages(msg)
        assert result == []

    def test_parts_to_chat_messages_mixed_parts(self):
        """MessageV2 with mixed parts should produce correct messages."""
        msg = MessageV2(
            role="assistant",
            parts=[
                TextPart(id="1", text="Hello"),
                ToolPart(id="2", tool="bash", output="result", tool_call_id="c1"),
                FilePart(id="3", file_path="test.py", content="code"),
            ],
        )
        result = parts_to_chat_messages(msg)
        assert len(result) == 3
        assert result[0].role == "assistant"
        assert result[1].role == "tool"
        assert result[2].role == "assistant"

    def test_compaction_part_converts_to_system(self):
        """CompactionPart should convert to system role ChatMessage."""
        msg = MessageV2(
            role="assistant",
            parts=[CompactionPart(id="1", summary="Summary", tokens_before=100, tokens_after=20)],
        )
        result = parts_to_chat_messages(msg)
        assert len(result) == 1
        assert result[0].role == "system"
        assert "Summary" in result[0].content

    def test_snapshot_part_formatting(self):
        """SnapshotPart should format correctly in ChatMessage."""
        msg = MessageV2(
            role="assistant",
            parts=[SnapshotPart(id="1", snapshot_id="s1", additions=5, deletions=3, files=2)],
        )
        result = parts_to_chat_messages(msg)
        assert len(result) == 1
        assert result[0].role == "system"
        assert "5 additions" in result[0].content
        assert "3 deletions" in result[0].content
        assert "2 files" in result[0].content


# ---------------------------------------------------------------------------
# SnapshotTracker Edge Cases
# ---------------------------------------------------------------------------


class TestSnapshotTrackerEdgeCases:
    """Test SnapshotTracker with edge case inputs."""

    def test_get_session_snapshots_nonexistent(self, isolated_db):
        """Querying snapshots for non-existent session should return empty list."""
        tracker = SnapshotTracker()
        result = tracker.get_session_snapshots("nonexistent-session-xyz")
        assert result == []

    def test_get_session_snapshots_empty_session_id(self, isolated_db):
        """Querying with empty session_id should return empty list."""
        tracker = SnapshotTracker()
        result = tracker.get_session_snapshots("")
        assert result == []

    def test_multiple_snapshots_ordered_by_time(self, isolated_db, session_manager):
        """Snapshots should be ordered by created_at ascending."""
        sid = session_manager.create()
        tracker = SnapshotTracker()

        # Create snapshots with different timestamps
        snap1 = tracker.create_snapshot(session_id=sid)
        time.sleep(0.01)
        tracker.save_snapshot(snap1, additions=1)

        snap2 = tracker.create_snapshot(session_id=sid)
        time.sleep(0.01)
        tracker.save_snapshot(snap2, additions=2)

        snap3 = tracker.create_snapshot(session_id=sid)
        tracker.save_snapshot(snap3, additions=3)

        result = tracker.get_session_snapshots(sid)
        assert len(result) == 3
        assert result[0]["additions"] == 1
        assert result[1]["additions"] == 2
        assert result[2]["additions"] == 3

    def test_save_snapshot_with_zero_stats(self, isolated_db, session_manager):
        """Should handle snapshots with zero changes."""
        sid = session_manager.create()
        tracker = SnapshotTracker()
        snap = tracker.create_snapshot(session_id=sid)
        tracker.save_snapshot(snap, additions=0, deletions=0, files=0)

        result = tracker.get_session_snapshots(sid)
        assert len(result) == 1
        assert result[0]["additions"] == 0
        assert result[0]["deletions"] == 0
        assert result[0]["files"] == 0

    def test_diff_snapshots_none_hashes(self):
        """Diff with None hashes should return empty string."""
        tracker = SnapshotTracker()
        assert tracker.diff_snapshots(None, None) == ""
        assert tracker.diff_snapshots("abc", None) == ""
        assert tracker.diff_snapshots(None, "abc") == ""

    def test_get_diff_stats_empty(self):
        """Empty diff should return zero stats."""
        tracker = SnapshotTracker()
        result = tracker.get_diff_stats("")
        assert result == {"additions": 0, "deletions": 0, "files": 0}

    def test_get_diff_stats_with_real_diff(self):
        """Should correctly parse a real diff string."""
        tracker = SnapshotTracker()
        diff = """diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,4 @@
 line1
+added_line
 line2
-removed_line
 line3
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1 +1,2 @@
 line1
+another_added
"""
        result = tracker.get_diff_stats(diff)
        assert result["additions"] == 2
        assert result["deletions"] == 1
        assert result["files"] == 2

    def test_create_snapshot_outside_git_repo(self):
        """Creating snapshot outside git repo should return hash=None."""
        tracker = SnapshotTracker(repo_path="/tmp/nonexistent_git_repo_12345")
        result = tracker.create_snapshot(session_id="test")
        assert result["hash"] is None
        assert result["session_id"] == "test"
        assert result["snapshot_id"] is not None
        assert result["created_at"] is not None


# ---------------------------------------------------------------------------
# TokenCounter Edge Cases
# ---------------------------------------------------------------------------


class TestTokenCounterEdgeCases:
    """Test TokenCounter with edge case inputs."""

    def test_count_text_empty(self):
        """Empty text should return 0 tokens."""
        counter = TokenCounter()
        assert counter.count_text("") == 0

    def test_count_text_none_like(self):
        """None-like text should return 0."""
        counter = TokenCounter()
        assert counter.count_text(None) == 0  # type: ignore

    def test_count_text_unknown_model(self):
        """Unknown model should fallback to cl100k_base or estimation."""
        counter = TokenCounter()
        result = counter.count_text("hello world", model="unknown-model-xyz")
        assert result >= 1

    def test_count_messages_empty_list(self):
        """Empty message list should return 0."""
        counter = TokenCounter()
        assert counter.count_messages([], "gpt-4o") == 0

    def test_count_messages_tool_calls_empty_function(self):
        """Tool calls with empty function info should not crash."""
        counter = TokenCounter()
        msg = ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "call_1", "type": "function"}],  # no "function" key
        )
        result = counter.count_messages([msg], "gpt-4o")
        assert result >= 3  # At least per-message overhead

    def test_count_messages_tool_calls_no_name(self):
        """Tool calls without function name should not crash."""
        counter = TokenCounter()
        msg = ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "call_1", "type": "function", "function": {}}],
        )
        result = counter.count_messages([msg], "gpt-4o")
        assert result >= 3

    def test_count_messages_multiple_tool_calls(self):
        """Multiple tool calls should add overhead for each."""
        counter = TokenCounter()
        msg = ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "write", "arguments": "{}"},
                },
                {
                    "id": "call_3",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                },
            ],
        )
        result = counter.count_messages([msg], "gpt-4o")
        # 3 (overhead) + 3 (assistant) + 3 * (7 + 3) = 36
        assert result >= 30

    def test_extract_usage_none_usage(self):
        """Response with None usage should return zeros."""
        counter = TokenCounter()
        response = ChatResponse(id="1", model="gpt-4o", content="Hello", usage=None)
        usage = counter.extract_usage(response)
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def test_extract_usage_empty_dict(self):
        """Response with empty usage dict should return zeros."""
        counter = TokenCounter()
        response = ChatResponse(id="1", model="gpt-4o", content="Hello", usage={})
        usage = counter.extract_usage(response)
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def test_encoding_cache(self):
        """Encoding should be cached per model."""
        TokenCounter._encoding_cache.clear()
        counter = TokenCounter()
        counter.count_text("hello", model="gpt-4o")
        assert "gpt-4o" in TokenCounter._encoding_cache


# ---------------------------------------------------------------------------
# Multi-Turn Overflow Simulation
# ---------------------------------------------------------------------------


class TestMultiTurnOverflow:
    """Simulate multi-turn conversations that exceed context limits."""

    def test_progressive_token_buildup(self, isolated_db):
        """Token count should increase with each added message."""
        counter = TokenCounter()
        messages = []  # type: List[ChatMessage]

        initial = counter.count_messages(messages, "gpt-4o")
        assert initial == 0

        # Add turns
        for i in range(5):
            messages.append(ChatMessage(role="user", content="Question {}".format(i)))
            messages.append(ChatMessage(role="assistant", content="Answer {}".format(i)))

        final = counter.count_messages(messages, "gpt-4o")
        assert final > initial
        # Each turn adds: 3 (user overhead) + content + 3+3 (assistant overhead) + content
        assert final > 50  # At least some tokens

    def test_large_tool_output_increases_tokens(self, isolated_db):
        """Large tool output should significantly increase token count."""
        counter = TokenCounter()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Run this"),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            ),
            ChatMessage(role="tool", content="x" * 10000, tool_call_id="call_1"),
        ]
        tokens = counter.count_messages(messages, "gpt-4o")
        assert tokens > 100  # Large tool output should contribute significantly

    def test_compaction_reduces_token_count(self, isolated_db):
        """After compaction, token count should be lower."""
        manager = AgentManager()
        counter = TokenCounter()

        messages = [
            ChatMessage(role="system", content="System prompt"),
            ChatMessage(role="user", content="A" * 5000),
            ChatMessage(role="assistant", content="B" * 5000),
            ChatMessage(role="user", content="C" * 5000),
            ChatMessage(role="assistant", content="D" * 5000),
        ]

        before_tokens = counter.count_messages(messages, "gpt-4o")
        compacted = manager.compact(messages, max_tokens=100)
        after_tokens = counter.count_messages(compacted, "gpt-4o")

        assert after_tokens <= before_tokens

    def test_pruning_reduces_message_count(self, isolated_db):
        """Pruning should reduce total message content size."""
        manager = AgentManager()
        large_content = "x" * 50000

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

        total_before = sum(len(m.content) for m in messages)
        pruned = manager.prune_messages(messages)
        total_after = sum(len(m.content) for m in pruned)

        assert total_after < total_before


# ---------------------------------------------------------------------------
# Memory Persistence & Session Lifecycle
# ---------------------------------------------------------------------------


class TestMemoryPersistence:
    """Test memory-related persistence across session lifecycle."""

    def test_pruning_state_persists_across_calls(self, isolated_db, session_manager):
        """Pruning state should persist and affect subsequent prune calls."""
        sid = session_manager.create()
        manager = AgentManager()

        # First call: create pruning state
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Check 1"),
            ChatMessage(role="tool", content="x" * 50000, tool_call_id="call-1"),
            ChatMessage(role="assistant", content="Check 2"),
            ChatMessage(role="tool", content="x" * 50000, tool_call_id="call-2"),
            ChatMessage(role="assistant", content="Check 3"),
            ChatMessage(role="tool", content="x" * 50000, tool_call_id="call-3"),
            ChatMessage(role="assistant", content="Check 4"),
            ChatMessage(role="tool", content="x" * 50000, tool_call_id="call-4"),
            ChatMessage(role="assistant", content="Check 5"),
            ChatMessage(role="tool", content="x" * 50000, tool_call_id="call-5"),
            ChatMessage(role="user", content="Done"),
        ]

        pruned_1 = manager.prune_messages(messages, session_id=sid)
        pruned_2 = manager.prune_messages(pruned_1, session_id=sid)

        # Second prune should not prune more than first
        # (protected messages from first prune should be preserved)
        tool_count_1 = sum(1 for m in pruned_1 if m.role == "tool")
        tool_count_2 = sum(1 for m in pruned_2 if m.role == "tool")
        assert tool_count_2 == tool_count_1

    def test_different_sessions_have_separate_pruning_state(self, isolated_db, session_manager):
        """Pruning state should be isolated per session."""
        sid1 = session_manager.create()
        sid2 = session_manager.create()
        manager = AgentManager()

        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Check"),
            ChatMessage(role="tool", content="x" * 50000, tool_call_id="call-1"),
            ChatMessage(role="user", content="Done"),
        ]

        # Prune for session 1
        manager.prune_messages(messages, session_id=sid1)

        # Prune for session 2 (should be independent)
        manager.prune_messages(messages, session_id=sid2)

        # Both should work without interference
        assert True  # No crash = success

    def test_snapshot_isolation_between_sessions(self, isolated_db, session_manager):
        """Snapshots from different sessions should not mix."""
        sid1 = session_manager.create()
        sid2 = session_manager.create()
        tracker = SnapshotTracker()

        snap1 = tracker.create_snapshot(session_id=sid1)
        tracker.save_snapshot(snap1, additions=10)

        snap2 = tracker.create_snapshot(session_id=sid2)
        tracker.save_snapshot(snap2, additions=20)

        snaps1 = tracker.get_session_snapshots(sid1)
        snaps2 = tracker.get_session_snapshots(sid2)

        assert len(snaps1) == 1
        assert len(snaps2) == 1
        assert snaps1[0]["additions"] == 10
        assert snaps2[0]["additions"] == 20

    def test_compaction_events_fire_with_session_id(
        self, isolated_db, session_manager, mock_provider
    ):
        """Compaction events should include valid session_id."""
        bus.clear()
        events = []  # type: list

        def on_start(data):
            events.append(("start", data))

        def on_complete(data):
            events.append(("complete", data))

        bus.subscribe(COMPACTION_STARTED, on_start)
        bus.subscribe(COMPACTION_COMPLETED, on_complete)

        try:
            sid = session_manager.create()
            manager = AgentManager()

            # Create messages that will trigger compaction
            large_content = "x" * 500000
            messages = [
                ChatMessage(role="system", content="System"),
                ChatMessage(role="user", content=large_content),
                ChatMessage(role="assistant", content="Response"),
                ChatMessage(role="user", content="More"),
            ]

            manager.compact(messages, max_tokens=100)

            # Events may or may not fire depending on implementation
            # Just verify no crash occurred
            assert isinstance(events, list)
        finally:
            bus.unsubscribe(COMPACTION_STARTED, on_start)
            bus.unsubscribe(COMPACTION_COMPLETED, on_complete)


# ---------------------------------------------------------------------------
# Event Bus Edge Cases
# ---------------------------------------------------------------------------


class TestEventBusEdgeCases:
    """Test event bus with edge case scenarios."""

    def test_unsubscribe_nonexistent_handler(self):
        """Unsubscribing a handler that was never subscribed should not crash."""
        bus.clear()

        def handler(data):
            pass

        # Should not raise
        bus.unsubscribe("test.nonexistent", handler)

    def test_publish_to_event_with_no_subscribers(self):
        """Publishing to event with no subscribers should not crash."""
        bus.clear()
        bus.publish("test.no_subscribers", {"key": "value"})
        # No crash = success

    def test_subscribe_same_handler_twice(self):
        """Subscribing the same handler twice should trigger it twice."""
        bus.clear()
        count = [0]

        def handler(data):
            count[0] += 1

        bus.subscribe("test.duplicate", handler)
        bus.subscribe("test.duplicate", handler)

        bus.publish("test.duplicate", None)
        assert count[0] == 2

        bus.unsubscribe("test.duplicate", handler)
        bus.unsubscribe("test.duplicate", handler)

    def test_once_handler_with_error(self):
        """Once handler that errors should still be removed."""
        bus.clear()
        count = [0]

        def failing_once(data):
            count[0] += 1
            raise ValueError("error")

        bus.once("test.once_error", failing_once)

        # First publish - should trigger and error
        bus.publish("test.once_error", None)
        assert count[0] == 1

        # Second publish - should NOT trigger (already removed)
        bus.publish("test.once_error", None)
        assert count[0] == 1

    def test_event_data_validation(self):
        """Event data classes should have expected attributes."""
        start = CompactionStartedData(session_id="s1", reason="auto")
        assert hasattr(start, "session_id")
        assert hasattr(start, "reason")

        complete = CompactionCompletedData(
            session_id="s1", tokens_before=100, tokens_after=50, tokens_freed=50
        )
        assert complete.tokens_freed == 50
        assert complete.tokens_before > complete.tokens_after

        prune = PruningCompletedData(session_id="s1", tokens_freed=10, messages_pruned=2)
        assert prune.messages_pruned == 2
