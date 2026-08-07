"""Core event bus implementation with thread-safe publish/subscribe."""

from __future__ import annotations

import threading
import logging
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Predefined event type constants
SESSION_CREATED = "session.created"
SESSION_DELETED = "session.deleted"
SESSION_COMPACTED = "session.compacted"
MESSAGE_ADDED = "message.added"
TOOL_EXECUTED = "tool.executed"
CONFIG_CHANGED = "config.changed"
PERMISSION_REQUESTED = "permission.requested"

# Memory management event type constants
COMPACTION_STARTED = "compaction.started"
COMPACTION_COMPLETED = "compaction.completed"
PRUNING_COMPLETED = "pruning.completed"
SNAPSHOT_CREATED = "snapshot.created"

# Inter-agent messaging event type constants
AGENT_MESSAGE_SENT = "agent.message_sent"
AGENT_MESSAGE_RECEIVED = "agent.message_received"
AGENT_MESSAGE_FAILED = "agent.message_failed"


@dataclass
class CompactionStartedData:
    """Data for compaction.started event."""

    session_id: str
    reason: str  # e.g., "token_overflow", "manual"


@dataclass
class CompactionCompletedData:
    """Data for compaction.completed event."""

    session_id: str
    tokens_before: int
    tokens_after: int
    tokens_freed: int


@dataclass
class PruningCompletedData:
    """Data for pruning.completed event."""

    session_id: str
    tokens_freed: int
    messages_pruned: int


@dataclass
class SnapshotCreatedData:
    """Data for snapshot.created event."""

    session_id: str
    snapshot_id: str
    additions: int
    deletions: int
    files: int


class EventBus:
    """Thread-safe publish/subscribe event bus.

    Callbacks execute in registration order. Errors in callbacks are caught
    and logged without crashing the bus. One-time callbacks are removed after
    their first execution (not before, to handle re-entrant publishes).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: Dict[str, List[Callable[[Any], None]]] = {}
        self._once_subscribers: Dict[str, List[Callable[[Any], None]]] = {}

    def subscribe(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """Register a callback for an event type.

        Args:
            event_type: String identifier for the event.
            callback: Callable that receives a single data argument.
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """Remove a previously registered callback.

        Args:
            event_type: String identifier for the event.
            callback: The exact callable object that was registered.
        """
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(callback)
                except ValueError:
                    pass  # Callback not found, silently ignore

    def once(self, event_type: str, callback: Callable[[Any], None]) -> None:
        """Register a one-time callback that auto-removes after first trigger.

        Args:
            event_type: String identifier for the event.
            callback: Callable that receives a single data argument.
        """
        with self._lock:
            if event_type not in self._once_subscribers:
                self._once_subscribers[event_type] = []
            self._once_subscribers[event_type].append(callback)

    def publish(self, event_type: str, data: Any = None) -> None:
        """Trigger all callbacks registered for an event type.

        Callbacks execute in registration order. Exceptions are caught and
        logged. One-time callbacks are removed AFTER execution to handle
        re-entrant publishes safely.

        Args:
            event_type: String identifier for the event.
            data: Arbitrary data passed to each callback.
        """
        with self._lock:
            regular = list(self._subscribers.get(event_type, []))
            once_list = list(self._once_subscribers.get(event_type, []))

        all_callbacks = regular + once_list

        for callback in all_callbacks:
            try:
                callback(data)
            except Exception:
                logger.exception("Error in event bus callback for event '%s'", event_type)

        # Remove executed one-time callbacks AFTER execution
        if once_list:
            with self._lock:
                if event_type in self._once_subscribers:
                    for cb in once_list:
                        try:
                            self._once_subscribers[event_type].remove(cb)
                        except ValueError:
                            pass
                    # Clean up empty lists
                    if not self._once_subscribers[event_type]:
                        del self._once_subscribers[event_type]

    def clear(self, event_type: Optional[str] = None) -> None:
        """Remove all callbacks.

        Args:
            event_type: If provided, clear only that event type.
                        If None, clear all events.
        """
        with self._lock:
            if event_type is None:
                self._subscribers.clear()
                self._once_subscribers.clear()
            else:
                self._subscribers.pop(event_type, None)
                self._once_subscribers.pop(event_type, None)

    def listeners(self, event_type: str) -> int:
        """Return the total count of listeners for an event type.

        Includes both regular and one-time subscribers.

        Args:
            event_type: String identifier for the event.

        Returns:
            Number of registered callbacks for the event.
        """
        with self._lock:
            regular_count = len(self._subscribers.get(event_type, []))
            once_count = len(self._once_subscribers.get(event_type, []))
            return regular_count + once_count


# Module-level singleton
bus = EventBus()
