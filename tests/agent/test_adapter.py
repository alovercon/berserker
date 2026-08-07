"""
Tests for berserker.agent.adapter module.

Covers:
- FrontendAdapter cannot be instantiated directly (abstract).
- subscribe/unsubscribe events.
- _publish_event dispatches to subscribers.
- list_agents/get_agent delegation to AgentManager.
- execute_agent abstract method.
- cancel_execution abstract method.
- get_agent_status abstract method.
"""

import threading
import pytest

from berserker.agent.adapter import FrontendAdapter
from berserker.agent.events import AgentEvent, AGENT_STARTED


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


class MockAgentManager(object):
    """Mock AgentManager for testing."""

    def __init__(self):
        self._agents = {
            "build": {"name": "build", "mode": "primary"},
            "plan": {"name": "plan", "mode": "primary"},
            "general": {"name": "general", "mode": "subagent"},
        }

    def list(self):
        return list(self._agents.values())

    def get(self, name):
        if name not in self._agents:
            raise KeyError("Agent '{}' not found".format(name))
        return self._agents[name]


class MockToolRegistry(object):
    """Mock ToolRegistry for testing."""

    def list_all(self):
        return []

    def get(self, name):
        return None


class ConcreteFrontendAdapter(FrontendAdapter):
    """Concrete implementation of FrontendAdapter for testing."""

    def execute_agent(
        self,
        agent_name,
        messages,
        session_id,
        on_tool_call=None,
        on_permission_ask=None,
        extra=None,
    ):
        return {"content": "test response", "usage": None, "finish_reason": "stop"}

    def cancel_execution(self):
        self._abort_event.set()
        self._is_executing = False

    def get_agent_status(self, session_id=None):
        return {
            "is_executing": self._is_executing,
            "current_agent": None,
            "session_id": session_id,
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_agent_manager():
    return MockAgentManager()


@pytest.fixture
def mock_tool_registry():
    return MockToolRegistry()


@pytest.fixture
def adapter(mock_agent_manager, mock_tool_registry):
    return ConcreteFrontendAdapter(mock_agent_manager, mock_tool_registry)


# ---------------------------------------------------------------------------
# Abstract Class Tests
# ---------------------------------------------------------------------------


class TestFrontendAdapterAbstract(object):
    """Test that FrontendAdapter is abstract and cannot be instantiated."""

    def test_cannot_instantiate_directly(self, mock_agent_manager, mock_tool_registry):
        with pytest.raises(TypeError):
            FrontendAdapter(mock_agent_manager, mock_tool_registry)

    def test_execute_agent_is_abstract(self, mock_agent_manager, mock_tool_registry):
        with pytest.raises(TypeError):
            FrontendAdapter(mock_agent_manager, mock_tool_registry)

    def test_cancel_execution_is_abstract(self, mock_agent_manager, mock_tool_registry):
        with pytest.raises(TypeError):
            FrontendAdapter(mock_agent_manager, mock_tool_registry)

    def test_get_agent_status_is_abstract(self, mock_agent_manager, mock_tool_registry):
        with pytest.raises(TypeError):
            FrontendAdapter(mock_agent_manager, mock_tool_registry)


# ---------------------------------------------------------------------------
# Concrete Implementation Tests
# ---------------------------------------------------------------------------


class TestConcreteFrontendAdapter(object):
    """Test concrete FrontendAdapter implementation."""

    def test_execute_agent_returns_result(self, adapter):
        result = adapter.execute_agent(
            agent_name="build",
            messages=[],
            session_id="sess_123",
        )
        assert result["content"] == "test response"
        assert result["usage"] is None
        assert result["finish_reason"] == "stop"

    def test_cancel_execution_sets_abort_event(self, adapter):
        assert not adapter._abort_event.is_set()
        adapter.cancel_execution()
        assert adapter._abort_event.is_set()

    def test_get_agent_status_returns_dict(self, adapter):
        status = adapter.get_agent_status(session_id="sess_123")
        assert isinstance(status, dict)
        assert "is_executing" in status
        assert "current_agent" in status
        assert "session_id" in status
        assert status["session_id"] == "sess_123"


# ---------------------------------------------------------------------------
# Event Subscription Tests
# ---------------------------------------------------------------------------


class TestEventSubscription(object):
    """Test event subscription and publishing."""

    def test_subscribe_events(self, adapter):
        callback = lambda event: None  # noqa: E731
        adapter.subscribe_events(callback)
        assert callback in adapter._event_subscribers

    def test_subscribe_events_no_duplicates(self, adapter):
        callback = lambda event: None  # noqa: E731
        adapter.subscribe_events(callback)
        adapter.subscribe_events(callback)
        assert adapter._event_subscribers.count(callback) == 1

    def test_unsubscribe_events(self, adapter):
        callback = lambda event: None  # noqa: E731
        adapter.subscribe_events(callback)
        adapter.unsubscribe_events(callback)
        assert callback not in adapter._event_subscribers

    def test_unsubscribe_nonexistent(self, adapter):
        callback = lambda event: None  # noqa: E731
        # Should not raise
        adapter.unsubscribe_events(callback)

    def test_publish_event_dispatches_to_subscribers(self, adapter):
        received = []

        def collector(event):
            received.append(event)

        adapter.subscribe_events(collector)
        event = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
        )
        adapter._publish_event(event)
        assert len(received) == 1
        assert received[0] == event

    def test_publish_event_multiple_subscribers(self, adapter):
        received = []

        def collector1(event):
            received.append(("c1", event))

        def collector2(event):
            received.append(("c2", event))

        adapter.subscribe_events(collector1)
        adapter.subscribe_events(collector2)
        event = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
        )
        adapter._publish_event(event)
        assert len(received) == 2
        assert received[0][0] == "c1"
        assert received[1][0] == "c2"

    def test_publish_event_subscriber_error_does_not_break(self, adapter):
        received = []

        def bad_callback(event):
            raise RuntimeError("Subscriber error")

        def good_callback(event):
            received.append(event)

        adapter.subscribe_events(bad_callback)
        adapter.subscribe_events(good_callback)
        event = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
        )
        # Should not raise
        adapter._publish_event(event)
        assert len(received) == 1
        assert received[0] == event


# ---------------------------------------------------------------------------
# Agent Manager Delegation Tests
# ---------------------------------------------------------------------------


class TestAgentManagerDelegation(object):
    """Test that list_agents and get_agent delegate to AgentManager."""

    def test_list_agents_delegates(self, adapter, mock_agent_manager):
        agents = adapter.list_agents()
        assert agents == mock_agent_manager.list()
        assert len(agents) == 3

    def test_get_agent_delegates(self, adapter, mock_agent_manager):
        agent = adapter.get_agent("build")
        assert agent == mock_agent_manager.get("build")
        assert agent["name"] == "build"

    def test_get_agent_raises_on_unknown(self, adapter):
        with pytest.raises(KeyError):
            adapter.get_agent("nonexistent")


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------


class TestFrontendAdapterInit(object):
    """Test FrontendAdapter initialization."""

    def test_init_with_all_params(self, mock_agent_manager, mock_tool_registry):
        mock_display = object()
        adapter = ConcreteFrontendAdapter(
            mock_agent_manager, mock_tool_registry, display=mock_display
        )
        assert adapter._agent_manager is mock_agent_manager
        assert adapter._tool_registry is mock_tool_registry
        assert adapter._display is mock_display
        assert adapter._event_subscribers == []
        assert isinstance(adapter._abort_event, threading.Event)
        assert adapter._is_executing is False
        assert hasattr(adapter._lock, "acquire")
        assert hasattr(adapter._lock, "release")

    def test_init_without_display(self, mock_agent_manager, mock_tool_registry):
        adapter = ConcreteFrontendAdapter(mock_agent_manager, mock_tool_registry)
        assert adapter._display is None

    def test_init_event_subscribers_empty(self, mock_agent_manager, mock_tool_registry):
        adapter = ConcreteFrontendAdapter(mock_agent_manager, mock_tool_registry)
        assert adapter._event_subscribers == []

    def test_init_abort_event_not_set(self, mock_agent_manager, mock_tool_registry):
        adapter = ConcreteFrontendAdapter(mock_agent_manager, mock_tool_registry)
        assert not adapter._abort_event.is_set()

    def test_init_not_executing(self, mock_agent_manager, mock_tool_registry):
        adapter = ConcreteFrontendAdapter(mock_agent_manager, mock_tool_registry)
        assert adapter._is_executing is False
