"""
Inter-agent messaging system with thread-safe message routing.

Provides:
- AgentMessage dataclass for structured messages between agents
- MessageRouter class for async agent-to-agent communication
- Module-level singleton message_router for global access

Python 3.8.10 compatible: uses type comments, no 3.9+ features.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional


class AgentMessage(object):
    """Represents a message sent between agents.

    Attributes:
        message_id: Unique identifier (uuid4).
        from_agent: Name of the sending agent.
        to_agent: Name of the receiving agent.
        session_id: Associated session identifier.
        content: Message content string.
        metadata: Additional key-value data (default empty dict).
        timestamp: Creation time as Unix timestamp (time.time()).
        status: Message status — 'pending', 'delivered', 'read', 'failed'.
    """

    def __init__(
        self,
        from_agent,  # type: str
        to_agent,  # type: str
        session_id,  # type: str
        content,  # type: str
        metadata=None,  # type: Optional[Dict[str, Any]]
        message_id=None,  # type: Optional[str]
        timestamp=None,  # type: Optional[float]
        status="pending",  # type: str
    ):
        # type: (...) -> None
        self.message_id = message_id if message_id is not None else uuid.uuid4().hex
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.session_id = session_id
        self.content = content
        self.metadata = metadata if metadata is not None else {}
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.status = status

    def __repr__(self):
        # type: () -> str
        return "AgentMessage(id={!r}, from={!r}, to={!r}, status={!r})".format(
            self.message_id[:8], self.from_agent, self.to_agent, self.status
        )

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, AgentMessage):
            return False
        return self.message_id == other.message_id

    def to_dict(self):
        # type: () -> Dict[str, Any]
        """Serialize message to dictionary."""
        return {
            "message_id": self.message_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "session_id": self.session_id,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data):
        # type: (Dict[str, Any]) -> AgentMessage
        """Deserialize message from dictionary."""
        return cls(
            message_id=data["message_id"],
            from_agent=data["from_agent"],
            to_agent=data["to_agent"],
            session_id=data["session_id"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp"),
            status=data.get("status", "pending"),
        )


class MessageRouter(object):
    """Thread-safe message router for agent-to-agent communication.

    Maintains per-agent message queues with individual locks for
    fine-grained concurrency control.

    Usage:
        router = MessageRouter()
        router.register_agent("build")
        router.register_agent("plan")
        router.send(AgentMessage(...))
        messages = router.receive("build")
    """

    def __init__(self):
        # type: () -> None
        self._queues = {}  # type: Dict[str, List[AgentMessage]]
        self._locks = {}  # type: Dict[str, threading.Lock]
        self._global_lock = threading.Lock()

    def register_agent(self, agent_name):
        # type: (str) -> None
        """Register an agent for message routing and broadcast.

        Args:
            agent_name: Unique agent identifier.
        """
        with self._global_lock:
            if agent_name not in self._queues:
                self._queues[agent_name] = []
                self._locks[agent_name] = threading.Lock()

    def send(self, message):
        # type: (AgentMessage) -> None
        """Send a message to an agent's queue.

        Args:
            message: AgentMessage to deliver.

        Raises:
            ValueError: If target agent is not registered.
        """
        target = message.to_agent
        if target not in self._queues:
            raise ValueError(
                "Agent '{}' is not registered. Call register_agent() first.".format(target)
            )

        lock = self._locks[target]
        with lock:
            message.status = "delivered"
            self._queues[target].append(message)

    def receive(self, agent_name):
        # type: (str) -> List[AgentMessage]
        """Receive and clear all pending messages for an agent.

        Args:
            agent_name: Name of the receiving agent.

        Returns:
            List of AgentMessage objects (empty if none pending).

        Raises:
            ValueError: If agent is not registered.
        """
        if agent_name not in self._queues:
            raise ValueError(
                "Agent '{}' is not registered.".format(agent_name)
            )

        lock = self._locks[agent_name]
        with lock:
            messages = list(self._queues[agent_name])
            self._queues[agent_name] = []

        # Mark as read
        for msg in messages:
            msg.status = "read"

        return messages

    def peek(self, agent_name):
        # type: (str) -> List[AgentMessage]
        """Peek at pending messages without clearing the queue.

        Args:
            agent_name: Name of the agent.

        Returns:
            List of pending AgentMessage objects.

        Raises:
            ValueError: If agent is not registered.
        """
        if agent_name not in self._queues:
            raise ValueError(
                "Agent '{}' is not registered.".format(agent_name)
            )

        lock = self._locks[agent_name]
        with lock:
            return list(self._queues[agent_name])

    def broadcast(self, message, exclude=None):
        # type: (AgentMessage, Optional[List[str]]) -> int
        """Broadcast a message to all registered agents except excluded ones.

        Creates a copy of the message for each recipient with updated
        to_agent field.

        Args:
            message: AgentMessage to broadcast (from_agent and content used).
            exclude: List of agent names to skip (default None).

        Returns:
            Number of agents that received the broadcast.
        """
        if exclude is None:
            exclude = []

        exclude_set = set(exclude)
        count = 0

        with self._global_lock:
            targets = [
                name
                for name in self._queues
                if name not in exclude_set and name != message.from_agent
            ]

        for target in targets:
            broadcast_msg = AgentMessage(
                from_agent=message.from_agent,
                to_agent=target,
                session_id=message.session_id,
                content=message.content,
                metadata=dict(message.metadata),
            )
            self.send(broadcast_msg)
            count += 1

        return count

    def get_pending_count(self, agent_name):
        # type: (str) -> int
        """Count pending messages for an agent.

        Args:
            agent_name: Name of the agent.

        Returns:
            Number of pending messages.

        Raises:
            ValueError: If agent is not registered.
        """
        if agent_name not in self._queues:
            raise ValueError(
                "Agent '{}' is not registered.".format(agent_name)
            )

        lock = self._locks[agent_name]
        with lock:
            return len(self._queues[agent_name])

    def clear(self, agent_name):
        # type: (str) -> None
        """Clear all pending messages for an agent.

        Args:
            agent_name: Name of the agent.

        Raises:
            ValueError: If agent is not registered.
        """
        if agent_name not in self._queues:
            raise ValueError(
                "Agent '{}' is not registered.".format(agent_name)
            )

        lock = self._locks[agent_name]
        with lock:
            self._queues[agent_name] = []

    def get_registered_agents(self):
        # type: () -> List[str]
        """List all registered agent names.

        Returns:
            Sorted list of registered agent names.
        """
        with self._global_lock:
            return sorted(self._queues.keys())


# Module-level singleton
message_router = MessageRouter()
