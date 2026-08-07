"""
Tests for berserker.agent.messaging module.

Covers:
- AgentMessage dataclass (creation, serialization, deserialization, equality).
- MessageRouter (send, receive, peek, broadcast, pending count, clear).
- Agent registration and error handling.
- Module-level singleton.
"""

import pytest
import time

from berserker.agent.messaging import AgentMessage, MessageRouter, message_router


# ---------------------------------------------------------------------------
# AgentMessage Tests
# ---------------------------------------------------------------------------


class TestAgentMessage:
    """Tests for AgentMessage dataclass."""

    def test_create_message_defaults(self):
        """Test message creation with default values."""
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
        )
        assert msg.from_agent == "build"
        assert msg.to_agent == "plan"
        assert msg.session_id == "sess-1"
        assert msg.content == "Hello"
        assert msg.metadata == {}
        assert msg.status == "pending"
        assert msg.message_id is not None
        assert len(msg.message_id) == 32  # uuid4 hex
        assert isinstance(msg.timestamp, float)

    def test_create_message_custom(self):
        """Test message creation with custom values."""
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
            metadata={"key": "value"},
            message_id="custom-id",
            timestamp=1234567890.0,
            status="delivered",
        )
        assert msg.message_id == "custom-id"
        assert msg.metadata == {"key": "value"}
        assert msg.timestamp == 1234567890.0
        assert msg.status == "delivered"

    def test_message_repr(self):
        """Test message string representation."""
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
            message_id="abcdef1234567890",
        )
        repr_str = repr(msg)
        assert "abcdef12" in repr_str
        assert "build" in repr_str
        assert "plan" in repr_str

    def test_message_equality(self):
        """Test message equality based on message_id."""
        msg1 = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
            message_id="same-id",
        )
        msg2 = AgentMessage(
            from_agent="plan",
            to_agent="build",
            session_id="sess-2",
            content="World",
            message_id="same-id",
        )
        assert msg1 == msg2

    def test_message_inequality(self):
        """Test message inequality with different IDs."""
        msg1 = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
        )
        msg2 = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
        )
        assert msg1 != msg2  # Different auto-generated IDs

    def test_message_not_equal_non_message(self):
        """Test message inequality with non-AgentMessage."""
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
        )
        assert msg != "not a message"
        assert msg != 42
        assert msg != None

    def test_to_dict(self):
        """Test message serialization to dictionary."""
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
            metadata={"key": "value"},
            message_id="test-id",
            timestamp=1234567890.0,
            status="delivered",
        )
        d = msg.to_dict()
        assert d["message_id"] == "test-id"
        assert d["from_agent"] == "build"
        assert d["to_agent"] == "plan"
        assert d["session_id"] == "sess-1"
        assert d["content"] == "Hello"
        assert d["metadata"] == {"key": "value"}
        assert d["timestamp"] == 1234567890.0
        assert d["status"] == "delivered"

    def test_from_dict(self):
        """Test message deserialization from dictionary."""
        data = {
            "message_id": "test-id",
            "from_agent": "build",
            "to_agent": "plan",
            "session_id": "sess-1",
            "content": "Hello",
            "metadata": {"key": "value"},
            "timestamp": 1234567890.0,
            "status": "delivered",
        }
        msg = AgentMessage.from_dict(data)
        assert msg.message_id == "test-id"
        assert msg.from_agent == "build"
        assert msg.to_agent == "plan"
        assert msg.session_id == "sess-1"
        assert msg.content == "Hello"
        assert msg.metadata == {"key": "value"}
        assert msg.timestamp == 1234567890.0
        assert msg.status == "delivered"

    def test_from_dict_defaults(self):
        """Test deserialization with missing optional fields."""
        data = {
            "message_id": "test-id",
            "from_agent": "build",
            "to_agent": "plan",
            "session_id": "sess-1",
            "content": "Hello",
        }
        msg = AgentMessage.from_dict(data)
        assert msg.metadata == {}
        assert msg.status == "pending"


# ---------------------------------------------------------------------------
# MessageRouter Tests
# ---------------------------------------------------------------------------


class TestMessageRouter:
    """Tests for MessageRouter class."""

    def setup_method(self):
        """Create a fresh router for each test."""
        self.router = MessageRouter()

    def test_register_agent(self):
        """Test agent registration."""
        self.router.register_agent("build")
        self.router.register_agent("plan")
        assert "build" in self.router.get_registered_agents()
        assert "plan" in self.router.get_registered_agents()

    def test_register_agent_idempotent(self):
        """Test registering same agent twice is safe."""
        self.router.register_agent("build")
        self.router.register_agent("build")
        assert self.router.get_pending_count("build") == 0

    def test_send_and_receive(self):
        """Test basic send and receive flow."""
        self.router.register_agent("build")
        self.router.register_agent("plan")

        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello plan",
        )
        self.router.send(msg)

        assert self.router.get_pending_count("plan") == 1

        received = self.router.receive("plan")
        assert len(received) == 1
        assert received[0].message_id == msg.message_id
        assert received[0].status == "read"
        assert self.router.get_pending_count("plan") == 0

    def test_receive_clears_queue(self):
        """Test that receive clears the queue."""
        self.router.register_agent("plan")

        for i in range(3):
            self.router.send(
                AgentMessage(
                    from_agent="build",
                    to_agent="plan",
                    session_id="sess-1",
                    content="msg-{}".format(i),
                )
            )

        assert self.router.get_pending_count("plan") == 3
        received = self.router.receive("plan")
        assert len(received) == 3
        assert self.router.get_pending_count("plan") == 0

    def test_receive_empty_queue(self):
        """Test receiving from empty queue returns empty list."""
        self.router.register_agent("plan")
        received = self.router.receive("plan")
        assert received == []

    def test_peek_does_not_clear(self):
        """Test peek returns messages without clearing queue."""
        self.router.register_agent("plan")
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
        )
        self.router.send(msg)

        peeked = self.router.peek("plan")
        assert len(peeked) == 1
        assert self.router.get_pending_count("plan") == 1

    def test_peek_empty_queue(self):
        """Test peek on empty queue returns empty list."""
        self.router.register_agent("plan")
        assert self.router.peek("plan") == []

    def test_broadcast(self):
        """Test broadcast to all registered agents."""
        self.router.register_agent("build")
        self.router.register_agent("plan")
        self.router.register_agent("explore")

        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",  # Will be overridden by broadcast
            session_id="sess-1",
            content="Broadcast message",
        )
        count = self.router.broadcast(msg)

        # Should reach plan and explore (not build, the sender)
        assert count == 2
        assert self.router.get_pending_count("plan") == 1
        assert self.router.get_pending_count("explore") == 1
        assert self.router.get_pending_count("build") == 0

    def test_broadcast_with_exclude(self):
        """Test broadcast with excluded agents."""
        self.router.register_agent("build")
        self.router.register_agent("plan")
        self.router.register_agent("explore")

        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Broadcast",
        )
        count = self.router.broadcast(msg, exclude=["plan"])

        # Should only reach explore (plan excluded, build is sender)
        assert count == 1
        assert self.router.get_pending_count("explore") == 1
        assert self.router.get_pending_count("plan") == 0

    def test_broadcast_no_recipients(self):
        """Test broadcast when no other agents exist."""
        self.router.register_agent("build")

        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Broadcast",
        )
        count = self.router.broadcast(msg)
        assert count == 0

    def test_pending_count(self):
        """Test pending count accuracy."""
        self.router.register_agent("plan")
        assert self.router.get_pending_count("plan") == 0

        self.router.send(
            AgentMessage(
                from_agent="build",
                to_agent="plan",
                session_id="sess-1",
                content="msg1",
            )
        )
        assert self.router.get_pending_count("plan") == 1

        self.router.send(
            AgentMessage(
                from_agent="build",
                to_agent="plan",
                session_id="sess-1",
                content="msg2",
            )
        )
        assert self.router.get_pending_count("plan") == 2

    def test_clear(self):
        """Test clearing message queue."""
        self.router.register_agent("plan")
        for i in range(5):
            self.router.send(
                AgentMessage(
                    from_agent="build",
                    to_agent="plan",
                    session_id="sess-1",
                    content="msg-{}".format(i),
                )
            )
        assert self.router.get_pending_count("plan") == 5

        self.router.clear("plan")
        assert self.router.get_pending_count("plan") == 0

    def test_send_to_unregistered_agent_raises(self):
        """Test sending to unregistered agent raises ValueError."""
        msg = AgentMessage(
            from_agent="build",
            to_agent="unknown",
            session_id="sess-1",
            content="Hello",
        )
        with pytest.raises(ValueError, match="not registered"):
            self.router.send(msg)

    def test_receive_from_unregistered_agent_raises(self):
        """Test receiving from unregistered agent raises ValueError."""
        with pytest.raises(ValueError, match="not registered"):
            self.router.receive("unknown")

    def test_peek_unregistered_agent_raises(self):
        """Test peeking unregistered agent raises ValueError."""
        with pytest.raises(ValueError, match="not registered"):
            self.router.peek("unknown")

    def test_clear_unregistered_agent_raises(self):
        """Test clearing unregistered agent raises ValueError."""
        with pytest.raises(ValueError, match="not registered"):
            self.router.clear("unknown")

    def test_pending_count_unregistered_agent_raises(self):
        """Test pending count for unregistered agent raises ValueError."""
        with pytest.raises(ValueError, match="not registered"):
            self.router.get_pending_count("unknown")

    def test_send_marks_delivered(self):
        """Test that send marks message as delivered."""
        self.router.register_agent("plan")
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
        )
        assert msg.status == "pending"
        self.router.send(msg)
        assert msg.status == "delivered"

    def test_receive_marks_read(self):
        """Test that receive marks messages as read."""
        self.router.register_agent("plan")
        msg = AgentMessage(
            from_agent="build",
            to_agent="plan",
            session_id="sess-1",
            content="Hello",
        )
        self.router.send(msg)
        received = self.router.receive("plan")
        assert received[0].status == "read"

    def test_get_registered_agents_sorted(self):
        """Test that registered agents are returned sorted."""
        self.router.register_agent("explore")
        self.router.register_agent("build")
        self.router.register_agent("plan")
        agents = self.router.get_registered_agents()
        assert agents == ["build", "explore", "plan"]


# ---------------------------------------------------------------------------
# Singleton Tests
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for module-level singleton."""

    def test_singleton_is_message_router(self):
        """Test that message_router is a MessageRouter instance."""
        assert isinstance(message_router, MessageRouter)

    def test_singleton_is_unique(self):
        """Test that message_router is the same object on repeated imports."""
        from berserker.agent.messaging import message_router as mr2
        assert message_router is mr2
