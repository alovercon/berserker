"""Integration tests for the compaction system.

Covers CompactionConfig, CompactionStrategy, event bus integration,
and pruning state persistence.

Python 3.8.10 compatible.
"""

import pytest

from berserker.agent.compaction import (
    CompactionConfig,
    CompactionStrategy,
    default_config,
    load_compaction_config,
)
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
from berserker.agent.manager import AgentManager
from berserker.provider.base import ChatMessage


# ---------------------------------------------------------------------------
# CompactionConfig Tests
# ---------------------------------------------------------------------------


class TestCompactionConfig:
    """Test CompactionConfig dataclass and its methods."""

    def test_default_values(self):
        """Default config should have expected values."""
        config = CompactionConfig()
        assert config.auto is True
        assert config.prune is True
        assert config.reserved == 20000
        assert config.prune_protect == 40000
        assert config.prune_minimum == 20000
        assert config.compact_threshold == 0.8

    def test_from_dict_partial_data(self):
        """from_dict() should use defaults for missing keys."""
        config = CompactionConfig.from_dict({"auto": False, "reserved": 30000})
        assert config.auto is False
        assert config.reserved == 30000
        assert config.prune is True  # default
        assert config.prune_protect == 40000  # default
        assert config.prune_minimum == 20000  # default
        assert config.compact_threshold == 0.8  # default

    def test_from_dict_unknown_keys_ignored(self):
        """from_dict() should silently ignore unknown keys."""
        config = CompactionConfig.from_dict(
            {"auto": False, "unknown_key": 1, "another_bad": "value"}
        )
        assert config.auto is False
        assert not hasattr(config, "unknown_key")

    def test_from_dict_non_dict_returns_default(self):
        """from_dict() with non-dict should return default config."""
        config_none = CompactionConfig.from_dict(None)
        assert config_none.auto is True
        assert config_none.reserved == 20000

        config_list = CompactionConfig.from_dict([1, 2, 3])
        assert config_list.auto is True

        config_str = CompactionConfig.from_dict("not a dict")
        assert config_str.auto is True

    def test_to_dict_returns_all_fields(self):
        """to_dict() should return dict matching all fields."""
        config = CompactionConfig(
            auto=False,
            prune=False,
            reserved=10000,
            prune_protect=50000,
            prune_minimum=15000,
            compact_threshold=0.9,
        )
        result = config.to_dict()
        assert result == {
            "auto": False,
            "prune": False,
            "reserved": 10000,
            "prune_protect": 50000,
            "prune_minimum": 15000,
            "compact_threshold": 0.9,
            "keep_last_turns": 2,
        }


# ---------------------------------------------------------------------------
# CompactionStrategy Tests
# ---------------------------------------------------------------------------


class TestCompactionStrategy:
    """Test CompactionStrategy decision methods."""

    def test_should_compact_true_when_over_threshold(self):
        """should_compact() returns True when tokens > model_limit - reserved."""
        strategy = CompactionStrategy()  # reserved=20000
        model_limit = 100000
        # tokens > 100000 - 20000 = 80000
        assert strategy.should_compact(80001, model_limit) is True

    def test_should_compact_false_when_under_threshold(self):
        """should_compact() returns False when tokens <= model_limit - reserved."""
        strategy = CompactionStrategy()
        model_limit = 100000
        # tokens <= 100000 - 20000 = 80000
        assert strategy.should_compact(80000, model_limit) is False
        assert strategy.should_compact(50000, model_limit) is False

    def test_should_prune_true_when_enabled_and_over_minimum(self):
        """should_prune() returns True when prune enabled AND tokens > prune_minimum."""
        strategy = CompactionStrategy()  # prune=True, prune_minimum=20000
        assert strategy.should_prune(20001) is True
        assert strategy.should_prune(50000) is True

    def test_should_prune_false_when_disabled(self):
        """should_prune() returns False when prune is disabled."""
        config = CompactionConfig(prune=False)
        strategy = CompactionStrategy(config)
        assert strategy.should_prune(100000) is False

    def test_should_prune_false_when_under_minimum(self):
        """should_prune() returns False when tokens <= prune_minimum."""
        strategy = CompactionStrategy()  # prune_minimum=20000
        assert strategy.should_prune(20000) is False
        assert strategy.should_prune(10000) is False

    def test_get_compact_threshold(self):
        """get_compact_threshold() returns int(model_limit * compact_threshold)."""
        strategy = CompactionStrategy()  # compact_threshold=0.8
        assert strategy.get_compact_threshold(100000) == 80000
        assert strategy.get_compact_threshold(128000) == 102400

    def test_get_prune_protect(self):
        """get_prune_protect() returns config.prune_protect."""
        strategy = CompactionStrategy()
        assert strategy.get_prune_protect() == 40000

        config = CompactionConfig(prune_protect=50000)
        strategy2 = CompactionStrategy(config)
        assert strategy2.get_prune_protect() == 50000


# ---------------------------------------------------------------------------
# default_config() and load_compaction_config() Tests
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """Test default_config() and load_compaction_config() functions."""

    def test_default_config_returns_fresh_instance(self):
        """default_config() should return a CompactionConfig with defaults."""
        config = default_config()
        assert isinstance(config, CompactionConfig)
        assert config.auto is True
        assert config.reserved == 20000

    def test_load_compaction_config_none(self):
        """load_compaction_config(None) should return default config."""
        config = load_compaction_config(None)
        assert config.auto is True
        assert config.reserved == 20000

    def test_load_compaction_config_empty_dict(self):
        """load_compaction_config({}) should return default config."""
        config = load_compaction_config({})
        assert config.auto is True
        assert config.reserved == 20000

    def test_load_compaction_config_no_compaction_key(self):
        """load_compaction_config without 'compaction' key returns defaults."""
        config = load_compaction_config({"other_key": "value"})
        assert config.auto is True

    def test_load_compaction_config_partial(self):
        """load_compaction_config with partial data uses defaults for missing."""
        config = load_compaction_config({"compaction": {"auto": False}})
        assert config.auto is False
        assert config.prune is True  # default
        assert config.reserved == 20000  # default

    def test_load_compaction_config_real_dict(self):
        """load_compaction_config with real config dict applies values, ignores unknown."""
        config = load_compaction_config(
            {
                "compaction": {
                    "auto": False,
                    "reserved": 30000,
                    "unknown_key": 1,
                }
            }
        )
        assert config.auto is False
        assert config.reserved == 30000
        assert config.prune is True  # default
        assert not hasattr(config, "unknown_key")


# ---------------------------------------------------------------------------
# Event Bus Integration Tests
# ---------------------------------------------------------------------------


class TestEventBusIntegration:
    """Test event bus with compaction-related events."""

    def test_event_constants_exist(self):
        """Event type constants should be defined in bus module."""
        assert COMPACTION_STARTED == "compaction.started"
        assert COMPACTION_COMPLETED == "compaction.completed"
        assert PRUNING_COMPLETED == "pruning.completed"
        assert SNAPSHOT_CREATED == "snapshot.created"

    def test_event_data_classes(self):
        """Event data classes should be instantiable with expected fields."""
        comp_start = CompactionStartedData(session_id="test-1", reason="token_overflow")
        assert comp_start.session_id == "test-1"
        assert comp_start.reason == "token_overflow"

        comp_done = CompactionCompletedData(
            session_id="test-1", tokens_before=90000, tokens_after=50000, tokens_freed=40000
        )
        assert comp_done.tokens_freed == 40000

        prune_done = PruningCompletedData(
            session_id="test-1", tokens_freed=10000, messages_pruned=3
        )
        assert prune_done.messages_pruned == 3

        snapshot = SnapshotCreatedData(
            session_id="test-1", snapshot_id="snap-1", additions=5, deletions=2, files=3
        )
        assert snapshot.files == 3

    def test_publish_and_subscribe(self):
        """Publishing an event should trigger subscribed callbacks."""
        received = []

        def callback(data):
            received.append(data)

        bus.subscribe(COMPACTION_STARTED, callback)
        try:
            test_data = CompactionStartedData(session_id="test-1", reason="manual")
            bus.publish(COMPACTION_STARTED, test_data)
            assert len(received) == 1
            assert received[0].session_id == "test-1"
            assert received[0].reason == "manual"
        finally:
            bus.unsubscribe(COMPACTION_STARTED, callback)

    def test_error_isolation(self):
        """Failing callback should not stop other callbacks from executing."""
        results = []

        def failing_callback(data):
            results.append("failing")
            raise ValueError("intentional error")

        def good_callback(data):
            results.append("good")

        bus.subscribe(COMPACTION_COMPLETED, failing_callback)
        bus.subscribe(COMPACTION_COMPLETED, good_callback)
        try:
            bus.publish(COMPACTION_COMPLETED, None)
            # Both callbacks should have been called
            assert "failing" in results
            assert "good" in results
        finally:
            bus.unsubscribe(COMPACTION_COMPLETED, failing_callback)
            bus.unsubscribe(COMPACTION_COMPLETED, good_callback)


# ---------------------------------------------------------------------------
# AgentManager.prune_messages() with session_id Tests
# ---------------------------------------------------------------------------


class TestPruneMessagesWithSessionId:
    """Test AgentManager.prune_messages() with DB persistence."""

    def test_prune_messages_no_session_id_backward_compatible(self, isolated_db):
        """prune_messages with session_id=None should be backward compatible, no DB ops."""
        manager = AgentManager()
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
        ]
        result = manager.prune_messages(messages, session_id=None)
        assert result is not messages
        assert len(result) == len(messages)

    def test_prune_messages_with_session_id_persists_state(self, session_manager):
        """prune_messages with session_id should persist pruning state to DB."""
        from berserker.storage import get_db

        manager = AgentManager()
        # Create a valid session (required by FK constraint on pruning_state)
        session_id = session_manager.create()

        # Create enough tool output to trigger pruning
        large_content = "x" * 50000  # ~12500 tokens
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

        result = manager.prune_messages(messages, session_id=session_id)

        # Verify some messages were pruned
        tool_msgs = [m for m in result if m.role == "tool"]
        pruned_msgs = [m for m in tool_msgs if "[Tool output pruned" in m.content]
        assert len(pruned_msgs) >= 1

        # Verify pruning state was persisted to DB
        db = get_db()
        rows = db.fetchall(
            "SELECT message_id FROM pruning_state WHERE session_id = ?",
            (session_id,),
        )
        assert len(rows) >= 1
