"""
Tests for berserker.agent.events module.

Covers:
- AgentEvent creation with all fields.
- Default timestamp behavior.
- Event type constants existence.
- AgentEvent with custom timestamp.
- AgentEvent repr and equality.
"""

import time
import pytest

from berserker.agent.events import (
    AgentEvent,
    AGENT_STARTED,
    AGENT_COMPLETED,
    AGENT_FAILED,
    AGENT_TOOL_CALL,
    AGENT_MESSAGE,
    AGENT_STATUS_CHANGED,
    AGENT_CANCELLED,
)


# ---------------------------------------------------------------------------
# Event Type Constants
# ---------------------------------------------------------------------------


class TestEventTypeConstants(object):
    """Test that all event type constants exist and have correct values."""

    def test_agent_started_constant(self):
        assert AGENT_STARTED == "agent.started"

    def test_agent_completed_constant(self):
        assert AGENT_COMPLETED == "agent.completed"

    def test_agent_failed_constant(self):
        assert AGENT_FAILED == "agent.failed"

    def test_agent_tool_call_constant(self):
        assert AGENT_TOOL_CALL == "agent.tool_call"

    def test_agent_message_constant(self):
        assert AGENT_MESSAGE == "agent.message"

    def test_agent_status_changed_constant(self):
        assert AGENT_STATUS_CHANGED == "agent.status_changed"

    def test_agent_cancelled_constant(self):
        assert AGENT_CANCELLED == "agent.cancelled"

    def test_all_constants_are_strings(self):
        constants = [
            AGENT_STARTED,
            AGENT_COMPLETED,
            AGENT_FAILED,
            AGENT_TOOL_CALL,
            AGENT_MESSAGE,
            AGENT_STATUS_CHANGED,
            AGENT_CANCELLED,
        ]
        for const in constants:
            assert isinstance(const, str)


# ---------------------------------------------------------------------------
# AgentEvent Creation
# ---------------------------------------------------------------------------


class TestAgentEventCreation(object):
    """Test AgentEvent creation and field assignment."""

    def test_create_with_all_fields(self):
        ts = time.time()
        event = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
            data={"key": "value"},
            timestamp=ts,
        )
        assert event.event_type == AGENT_STARTED
        assert event.session_id == "sess_123"
        assert event.agent_name == "build"
        assert event.data == {"key": "value"}
        assert event.timestamp == ts

    def test_create_with_minimal_fields(self):
        event = AgentEvent(
            event_type=AGENT_COMPLETED,
            session_id="sess_456",
            agent_name="plan",
        )
        assert event.event_type == AGENT_COMPLETED
        assert event.session_id == "sess_456"
        assert event.agent_name == "plan"
        assert event.data == {}
        assert isinstance(event.timestamp, float)

    def test_default_data_is_empty_dict(self):
        event = AgentEvent(
            event_type=AGENT_MESSAGE,
            session_id="sess_789",
            agent_name="general",
        )
        assert event.data == {}
        assert event.data is not None

    def test_default_timestamp_is_current_time(self):
        before = time.time()
        event = AgentEvent(
            event_type=AGENT_STATUS_CHANGED,
            session_id="sess_ts",
            agent_name="build",
        )
        after = time.time()
        assert before <= event.timestamp <= after

    def test_custom_timestamp(self):
        custom_ts = 1700000000.0
        event = AgentEvent(
            event_type=AGENT_FAILED,
            session_id="sess_err",
            agent_name="build",
            timestamp=custom_ts,
        )
        assert event.timestamp == custom_ts

    def test_data_can_be_none_explicitly(self):
        event = AgentEvent(
            event_type=AGENT_TOOL_CALL,
            session_id="sess_tool",
            agent_name="build",
            data=None,
        )
        # None data should default to empty dict
        assert event.data == {}


# ---------------------------------------------------------------------------
# AgentEvent Repr and Equality
# ---------------------------------------------------------------------------


class TestAgentEventReprAndEquality(object):
    """Test AgentEvent __repr__ and __eq__ methods."""

    def test_repr(self):
        event = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
        )
        repr_str = repr(event)
        assert "AgentEvent" in repr_str
        assert "agent.started" in repr_str
        assert "sess_123" in repr_str
        assert "build" in repr_str

    def test_equality_same_fields(self):
        event1 = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
            data={"key": "value"},
        )
        event2 = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
            data={"key": "value"},
        )
        assert event1 == event2

    def test_equality_different_event_type(self):
        event1 = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
        )
        event2 = AgentEvent(
            event_type=AGENT_COMPLETED,
            session_id="sess_123",
            agent_name="build",
        )
        assert event1 != event2

    def test_equality_different_session_id(self):
        event1 = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
        )
        event2 = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_456",
            agent_name="build",
        )
        assert event1 != event2

    def test_equality_different_data(self):
        event1 = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
            data={"a": 1},
        )
        event2 = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
            data={"b": 2},
        )
        assert event1 != event2

    def test_equality_with_non_agent_event(self):
        event = AgentEvent(
            event_type=AGENT_STARTED,
            session_id="sess_123",
            agent_name="build",
        )
        assert event != "not an event"
        assert event != 42
        assert event != None  # noqa: E711
        assert event != {"event_type": "agent.started"}
