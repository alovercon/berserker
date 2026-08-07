"""Agent-specific event types for the frontend adapter layer."""
from __future__ import annotations

from typing import Any, Dict
import time

# Event type constants
AGENT_STARTED = "agent.started"
AGENT_COMPLETED = "agent.completed"
AGENT_FAILED = "agent.failed"
AGENT_TOOL_CALL = "agent.tool_call"
AGENT_MESSAGE = "agent.message"
AGENT_STATUS_CHANGED = "agent.status_changed"
AGENT_CANCELLED = "agent.cancelled"


class AgentEvent(object):
    """Represents an agent lifecycle or status event.

    Python 3.8.10 compatible: uses type comments.

    Attributes:
        event_type: Type of the event (e.g., AGENT_STARTED).
        session_id: Session identifier associated with the event.
        agent_name: Name of the agent that generated the event.
        data: Optional dict of event-specific data.
        timestamp: Unix timestamp of when the event was created.
    """

    event_type = None  # type: str
    session_id = None  # type: str
    agent_name = None  # type: str
    data = None  # type: Dict[str, Any]
    timestamp = None  # type: float

    def __init__(
        self,
        event_type,  # type: str
        session_id,  # type: str
        agent_name,  # type: str
        data=None,  # type: Optional[Dict[str, Any]]
        timestamp=None,  # type: Optional[float]
    ):
        # type: (...) -> None
        self.event_type = event_type
        self.session_id = session_id
        self.agent_name = agent_name
        self.data = data if data is not None else {}
        self.timestamp = timestamp if timestamp is not None else time.time()

    def __repr__(self):
        # type: () -> str
        return "AgentEvent(event_type={!r}, session_id={!r}, agent_name={!r})".format(
            self.event_type, self.session_id, self.agent_name
        )

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, AgentEvent):
            return False
        return (
            self.event_type == other.event_type
            and self.session_id == other.session_id
            and self.agent_name == other.agent_name
            and self.data == other.data
        )
