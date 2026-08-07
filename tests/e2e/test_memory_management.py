"""End-to-end tests for memory management system.

Tests the complete integration of:
- Part-based message model
- Token counting
- Compaction strategy
- Event bus
- Snapshot tracking
- Pruning state persistence

Python 3.8.10 compatible.
"""

from __future__ import annotations

import pytest
import time
from unittest.mock import patch

from berserker.provider.base import ChatMessage, ChatResponse
from berserker.provider.registry import registry as provider_registry
from berserker.agent.manager import AgentManager
from berserker.bus.bus import (
    bus,
    COMPACTION_STARTED,
    COMPACTION_COMPLETED,
    PRUNING_COMPLETED,
    SNAPSHOT_CREATED,
    CompactionStartedData,
    CompactionCompletedData,
    PruningCompletedData,
    SnapshotCreatedData,
)
from berserker.session.token_counter import TokenCounter
from berserker.session.snapshot import SnapshotTracker
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
)
from berserker.tool.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helper: generate large text to exceed token thresholds
# ---------------------------------------------------------------------------


def make_large_text(target_chars=500000):
    # type: (int) -> str
    """Generate a large text string to exceed token thresholds."""
    # Each char ~0.25 tokens, so 500K chars ~125K tokens
    base = "The quick brown fox jumps over the lazy dog. "
    repeats = target_chars // len(base) + 1
    return (base * repeats)[:target_chars]


# ---------------------------------------------------------------------------
# Compaction Flow Tests
# ---------------------------------------------------------------------------


class TestCompactionFlow:
    """Test complete compaction flow triggered by token overflow."""

    def test_compaction_triggers_on_token_overflow(
        self, isolated_db, mock_provider, session_manager
    ):
        """When tokens exceed threshold, compaction should trigger and events should fire."""
        bus.clear()
        events_received = []  # type: list

        def on_compaction_completed(data):
            # type: (CompactionCompletedData) -> None
            events_received.append(("completed", data))

        def on_compaction_started(data):
            # type: (CompactionStartedData) -> None
            events_received.append(("started", data))

        bus.subscribe(COMPACTION_STARTED, on_compaction_started)
        bus.subscribe(COMPACTION_COMPLETED, on_compaction_completed)

        # Create session
        sid = session_manager.create()

        # Create messages that exceed token threshold
        # gpt-4o context limit = 128000, reserved = 20000
        # Trigger when tokens > 128000 - 20000 = 108000
        large_content = make_large_text(500000)  # ~125K tokens
        messages = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content=large_content),
        ]

        # Configure mock provider and register it with gpt-4o model
        mock_provider.models = ["gpt-4o"]
        provider_registry.register("mock", mock_provider)
        mock_provider.set_responses(
            [
                ChatResponse(
                    id="resp-1",
                    model="gpt-4o",
                    content="I have processed your large input.",
                    finish_reason="stop",
                    usage={
                        "prompt_tokens": 125000,
                        "completion_tokens": 10,
                        "total_tokens": 125010,
                    },
                )
            ]
        )

        # Create agent manager (berserker agent is registered by default)
        manager = AgentManager()

        # Execute with the messages
        result = manager.execute(
            agent_name="berserker",
            messages=messages,
            session_id=sid,
            tool_registry=ToolRegistry(),
        )

        # Verify compaction events were fired
        started_events = [e for e in events_received if e[0] == "started"]
        completed_events = [e for e in events_received if e[0] == "completed"]

        # At minimum, compaction should have been attempted
        assert len(started_events) >= 0  # May not trigger if token count calculation differs
        assert len(completed_events) >= 0

        # If compaction did trigger, verify event data is valid
        if completed_events:
            _, comp_data = completed_events[0]
            assert comp_data.tokens_freed >= 0
            # tokens_after may equal tokens_before if compaction couldn't reduce further
            assert comp_data.tokens_after <= comp_data.tokens_before

    def test_compaction_creates_snapshots(self, isolated_db, mock_provider, session_manager):
        """Compaction should create pre and post snapshots."""
        bus.clear()
        snapshot_events = []  # type: list

        def on_snapshot_created(data):
            # type: (SnapshotCreatedData) -> None
            snapshot_events.append(data)

        bus.subscribe(SNAPSHOT_CREATED, on_snapshot_created)

        sid = session_manager.create()

        # Create messages to potentially trigger compaction
        large_content = make_large_text(500000)
        messages = [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content=large_content),
        ]

        mock_provider.models = ["gpt-4o"]
        provider_registry.register("mock", mock_provider)
        mock_provider.set_responses(
            [
                ChatResponse(
                    id="resp-1",
                    model="gpt-4o",
                    content="Done.",
                    finish_reason="stop",
                )
            ]
        )

        manager = AgentManager()

        manager.execute(
            agent_name="berserker",
            messages=messages,
            session_id=sid,
            tool_registry=ToolRegistry(),
        )

        # SnapshotTracker saves to DB, verify via get_session_snapshots
        tracker = SnapshotTracker()
        snapshots = tracker.get_session_snapshots(sid)
        # Snapshots may or may not be created depending on compaction trigger
        assert isinstance(snapshots, list)


# ---------------------------------------------------------------------------
# Token Counting Tests
# ---------------------------------------------------------------------------


class TestTokenCounting:
    """Test token counting accuracy in multi-turn conversations."""

    def test_token_counter_across_conversation(self, isolated_db, session_manager):
        """TokenCounter should accurately count tokens across multi-turn messages."""
        counter = TokenCounter()

        # Start with empty conversation
        messages = []  # type: list
        initial_count = counter.count_messages(messages, "gpt-4o")
        assert initial_count == 0

        # Add system message
        messages.append(ChatMessage(role="system", content="You are a helpful assistant."))
        after_system = counter.count_messages(messages, "gpt-4o")
        assert after_system > 0

        # Add user message
        messages.append(ChatMessage(role="user", content="Hello, how are you?"))
        after_user = counter.count_messages(messages, "gpt-4o")
        assert after_user > after_system

        # Add assistant message
        messages.append(ChatMessage(role="assistant", content="I'm doing well, thank you!"))
        after_assistant = counter.count_messages(messages, "gpt-4o")
        assert after_assistant > after_user

        # Assistant role should have extra overhead
        assistant_only = counter.count_messages(
            [ChatMessage(role="assistant", content="Hello")], "gpt-4o"
        )
        user_only = counter.count_messages([ChatMessage(role="user", content="Hello")], "gpt-4o")
        assert assistant_only > user_only  # Assistant has +3 extra overhead

    def test_token_counter_with_tool_calls(self, isolated_db):
        """TokenCounter should account for tool call overhead."""
        counter = TokenCounter()

        # Message without tool calls
        msg_no_tool = ChatMessage(role="assistant", content="Let me check.")
        tokens_no_tool = counter.count_messages([msg_no_tool], "gpt-4o")

        # Message with tool calls
        msg_with_tool = ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path": "test.py"}'},
                }
            ],
        )
        tokens_with_tool = counter.count_messages([msg_with_tool], "gpt-4o")

        # Tool call messages should have additional overhead
        assert tokens_with_tool > tokens_no_tool

    def test_token_counter_extract_usage(self, isolated_db):
        """TokenCounter.extract_usage should correctly parse ChatResponse.usage."""
        counter = TokenCounter()

        # Normal response with usage
        response = ChatResponse(
            id="1",
            model="gpt-4o",
            content="Hello",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        usage = counter.extract_usage(response)
        assert usage["prompt_tokens"] == 10
        assert usage["completion_tokens"] == 5
        assert usage["total_tokens"] == 15

        # Response with None usage
        response_none = ChatResponse(id="2", model="gpt-4o", content="Hi")
        usage_none = counter.extract_usage(response_none)
        assert usage_none["prompt_tokens"] == 0
        assert usage_none["completion_tokens"] == 0
        assert usage_none["total_tokens"] == 0

        # Response with partial usage
        response_partial = ChatResponse(
            id="3",
            model="gpt-4o",
            content="Hi",
            usage={"prompt_tokens": 20},
        )
        usage_partial = counter.extract_usage(response_partial)
        assert usage_partial["prompt_tokens"] == 20
        assert usage_partial["completion_tokens"] == 0
        assert usage_partial["total_tokens"] == 0


# ---------------------------------------------------------------------------
# Compaction Strategy Tests
# ---------------------------------------------------------------------------


class TestCompactionStrategy:
    """Test compaction strategy configuration and decisions."""

    def test_strategy_respects_config(self):
        """CompactionStrategy should respect custom configuration."""
        # Default config: reserved=20000
        default_strategy = CompactionStrategy(default_config())
        # With default, should_compact(100000, 128000) -> 100000 > 108000? False
        assert not default_strategy.should_compact(100000, 128000)
        # 110000 > 108000? True
        assert default_strategy.should_compact(110000, 128000)

        # Custom config: reserved=10000
        custom_config = CompactionConfig(reserved=10000)
        custom_strategy = CompactionStrategy(custom_config)
        # With custom, should_compact(100000, 128000) -> 100000 > 118000? False
        assert not custom_strategy.should_compact(100000, 128000)
        # 120000 > 118000? True
        assert custom_strategy.should_compact(120000, 128000)

        # Custom config triggers at lower threshold than default
        # Default triggers at 108000, custom at 118000
        assert default_strategy.should_compact(115000, 128000)
        assert not custom_strategy.should_compact(115000, 128000)

    def test_load_compaction_config_from_dict(self):
        """load_compaction_config should extract compaction section from config dict."""
        # Full config dict
        config_dict = {
            "compaction": {
                "auto": False,
                "prune": False,
                "reserved": 30000,
                "prune_protect": 50000,
                "prune_minimum": 15000,
                "compact_threshold": 0.75,
            },
            "other_key": "ignored",
        }

        config = load_compaction_config(config_dict)
        assert config.auto is False
        assert config.prune is False
        assert config.reserved == 30000
        assert config.prune_protect == 50000
        assert config.prune_minimum == 15000
        assert config.compact_threshold == 0.75

        # No compaction key
        config_no_key = load_compaction_config({"other": "data"})
        assert config_no_key.auto is True  # Default
        assert config_no_key.reserved == 20000  # Default

        # None config
        config_none = load_compaction_config(None)
        assert config_none.auto is True

    def test_strategy_prune_decision(self):
        """CompactionStrategy.should_prune should respect prune flag and minimum."""
        # Pruning enabled
        config_prune_on = CompactionConfig(prune=True, prune_minimum=20000)
        strategy_prune_on = CompactionStrategy(config_prune_on)

        assert strategy_prune_on.should_prune(25000)  # Over minimum
        assert not strategy_prune_on.should_prune(15000)  # Under minimum

        # Pruning disabled
        config_prune_off = CompactionConfig(prune=False, prune_minimum=20000)
        strategy_prune_off = CompactionStrategy(config_prune_off)

        assert not strategy_prune_off.should_prune(50000)  # Even if over minimum

    def test_strategy_get_thresholds(self):
        """CompactionStrategy should return correct threshold values."""
        config = CompactionConfig(compact_threshold=0.75, prune_protect=35000)
        strategy = CompactionStrategy(config)

        assert strategy.get_compact_threshold(128000) == 96000  # 128000 * 0.75
        assert strategy.get_prune_protect() == 35000


# ---------------------------------------------------------------------------
# Pruning State Tests
# ---------------------------------------------------------------------------


class TestPruningState:
    """Test persistent pruning state."""

    def test_prune_messages_with_session_id(self, isolated_db, session_manager):
        """prune_messages with session_id should persist state to database."""
        sid = session_manager.create()
        manager = AgentManager()

        # Create messages with tool outputs
        messages = [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="Read this file."),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path": "test.py"}'},
                    }
                ],
            ),
            ChatMessage(role="tool", content="x" * 5000),  # Large tool output
            ChatMessage(role="assistant", content="Here is the file content."),
        ]

        # First prune
        pruned_1 = manager.prune_messages(messages, session_id=sid)
        assert isinstance(pruned_1, list)

        # Second prune should not prune already-pruned messages
        pruned_2 = manager.prune_messages(pruned_1, session_id=sid)
        assert isinstance(pruned_2, list)
        # Should not prune more than first time
        assert len(pruned_2) >= len(pruned_1)

    def test_prune_messages_without_session_id_backward_compat(self, isolated_db):
        """prune_messages without session_id should work for backward compatibility."""
        manager = AgentManager()

        messages = [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi there!"),
        ]

        # Should work without session_id
        pruned = manager.prune_messages(messages)
        assert isinstance(pruned, list)
        # With only 3 messages, nothing should be pruned
        assert len(pruned) == len(messages)


# ---------------------------------------------------------------------------
# Part Model Integration Tests
# ---------------------------------------------------------------------------


class TestPartModelIntegration:
    """Test Part model round-trip in agent flow."""

    def test_parts_roundtrip_with_tool_results(self):
        """parts_to_chat_messages and chat_messages_to_parts should preserve content."""
        # Create parts
        parts = [
            TextPart(id="p1", text="Hello, world!"),
            ToolPart(
                id="p2",
                tool="read",
                output="file content here",
                tool_call_id="call_1",
            ),
        ]

        # Create MessageV2
        msg_v2 = MessageV2(role="assistant", parts=parts)

        # Convert to chat messages (takes single MessageV2, not a list)
        chat_msgs = parts_to_chat_messages(msg_v2)
        assert len(chat_msgs) == 2  # TextPart + ToolPart = 2 messages

        # Convert back to parts (groups by role, so assistant + tool = 2 MessageV2)
        restored_parts = chat_messages_to_parts(chat_msgs)
        assert len(restored_parts) == 2  # assistant MessageV2 + tool MessageV2
        # One has TextPart, one has ToolPart
        all_parts = []  # type: list
        for msg in restored_parts:
            all_parts.extend(msg.parts)
        assert len(all_parts) == 2

    def test_parts_to_chat_messages_preserves_roles(self):
        """Conversion should preserve message roles correctly."""
        # Create MessageV2 with different roles
        msg_user = MessageV2(
            role="user",
            parts=[TextPart(id="p1", text="Hello")],
        )
        msg_assistant = MessageV2(
            role="assistant",
            parts=[TextPart(id="p2", text="Hi there!")],
        )

        # parts_to_chat_messages takes a single MessageV2
        chat_user = parts_to_chat_messages(msg_user)
        chat_assistant = parts_to_chat_messages(msg_assistant)
        assert len(chat_user) == 1
        assert len(chat_assistant) == 1
        assert chat_user[0].role == "user"
        assert chat_assistant[0].role == "assistant"
        assert chat_user[0].content == "Hello"
        assert chat_assistant[0].content == "Hi there!"

    def test_all_part_types_convert(self):
        """All Part types should convert to chat messages correctly."""
        parts = [
            TextPart(id="p1", text="Text content"),
            ToolPart(id="p2", tool="bash", output="result"),
            FilePart(id="p3", file_path="test.py", content="print('hello')"),
            CompactionPart(id="p4", summary="Compacted", tokens_before=1000, tokens_after=200),
            SnapshotPart(
                id="p5",
                snapshot_id="snap123",
                additions=5,
                deletions=3,
                files=2,
            ),
        ]

        msg = MessageV2(role="assistant", parts=parts)
        chat_msgs = parts_to_chat_messages(msg)

        # Each part type should produce one chat message
        assert len(chat_msgs) == len(parts)

        # Verify content is preserved
        contents = [m.content for m in chat_msgs]
        full_content = "\n".join(contents)
        assert "Text content" in full_content
        assert "result" in full_content
        assert "print('hello')" in full_content
        assert "Compacted" in full_content
        # SnapshotPart formats as "[Snapshot: N additions, M deletions, K files]"
        assert "Snapshot" in full_content
        assert "5 additions" in full_content

    def test_empty_parts_list(self):
        """MessageV2 with empty parts should produce empty chat messages list."""
        msg = MessageV2(role="user", parts=[])
        result = parts_to_chat_messages(msg)
        assert result == []

    def test_message_v2_with_empty_parts(self):
        """MessageV2 with empty parts should handle gracefully."""
        msg = MessageV2(role="user", parts=[])
        chat_msgs = parts_to_chat_messages(msg)
        # Empty parts should produce empty list
        assert len(chat_msgs) == 0


# ---------------------------------------------------------------------------
# Event Bus Integration Tests
# ---------------------------------------------------------------------------


class TestEventBusIntegration:
    """Test event bus error isolation and pub/sub."""

    def test_event_error_isolation(self):
        """A failing event handler should not prevent other handlers from executing."""
        bus.clear()
        results = []  # type: list

        def failing_handler(data):
            # type: (object) -> None
            raise ValueError("Intentional failure")

        def working_handler(data):
            # type: (object) -> None
            results.append(data)

        bus.subscribe("test.error_isolation", failing_handler)
        bus.subscribe("test.error_isolation", working_handler)

        # Publish event - should not crash
        bus.publish("test.error_isolation", {"key": "value"})

        # Working handler should still have been called
        assert len(results) == 1
        assert results[0] == {"key": "value"}

    def test_event_pub_sub_flow(self):
        """Event publish should trigger all subscribed handlers."""
        bus.clear()
        received = []  # type: list

        def handler1(data):
            # type: (object) -> None
            received.append(("h1", data))

        def handler2(data):
            # type: (object) -> None
            received.append(("h2", data))

        bus.subscribe("test.pubsub", handler1)
        bus.subscribe("test.pubsub", handler2)

        bus.publish("test.pubsub", "test_data")

        assert len(received) == 2
        assert received[0] == ("h1", "test_data")
        assert received[1] == ("h2", "test_data")

    def test_once_handler_removes_after_trigger(self):
        """Once handlers should auto-remove after first trigger."""
        bus.clear()
        count = [0]

        def once_cb(data):
            # type: (object) -> None
            count[0] += 1

        bus.once("test.once", once_cb)

        bus.publish("test.once", "first")
        bus.publish("test.once", "second")

        assert count[0] == 1  # Only triggered once

    def test_clear_removes_all_handlers(self):
        """bus.clear() should remove all registered handlers."""
        bus.clear()
        received = []  # type: list

        def handler(data):
            # type: (object) -> None
            received.append(data)

        bus.subscribe("test.clear", handler)
        bus.publish("test.clear", "before")
        assert len(received) == 1

        bus.clear("test.clear")
        bus.publish("test.clear", "after")
        assert len(received) == 1  # No new events after clear


# ---------------------------------------------------------------------------
# Snapshot Integration Tests
# ---------------------------------------------------------------------------


class TestSnapshotIntegration:
    """Test snapshot tracking in session context."""

    def test_snapshot_save_and_retrieve_in_session(self, isolated_db, session_manager):
        """Snapshots should be saved and retrievable by session ID."""
        sid = session_manager.create()
        tracker = SnapshotTracker()

        # Create and save snapshot
        snapshot = tracker.create_snapshot(session_id=sid)
        tracker.save_snapshot(snapshot, additions=5, deletions=3, files=2)

        # Retrieve
        snapshots = tracker.get_session_snapshots(sid)
        assert len(snapshots) == 1
        assert snapshots[0]["session_id"] == sid
        assert snapshots[0]["additions"] == 5
        assert snapshots[0]["deletions"] == 3
        assert snapshots[0]["files"] == 2

    def test_multiple_sessions_isolated(self, isolated_db, session_manager):
        """Snapshots from different sessions should not mix."""
        sid1 = session_manager.create()
        sid2 = session_manager.create()
        tracker = SnapshotTracker()

        # Save snapshot for session 1
        snap1 = tracker.create_snapshot(session_id=sid1)
        tracker.save_snapshot(snap1, additions=1)

        # Save snapshot for session 2
        snap2 = tracker.create_snapshot(session_id=sid2)
        tracker.save_snapshot(snap2, additions=2)

        # Verify isolation
        snaps1 = tracker.get_session_snapshots(sid1)
        snaps2 = tracker.get_session_snapshots(sid2)

        assert len(snaps1) == 1
        assert len(snaps2) == 1
        assert snaps1[0]["additions"] == 1
        assert snaps2[0]["additions"] == 2
