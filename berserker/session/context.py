"""
berserker.session.context — SessionContext with state machine, abort coordination,
message cache, and lifecycle methods.

Python 3.8.10 compatible: uses type comments, Optional/List/Dict/Any from typing,
no | union syntax, no match/case, no walrus operator in complex expressions.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from berserker.session.manager import SessionManager


class SessionState:
    """Session state constants (class constants, not Enum, for Python 3.8.10 compatibility)."""

    IDLE = "idle"
    ACTIVE = "active"
    AGENT_RUNNING = "agent_running"
    ABORTING = "aborting"
    COMPACTING = "compacting"


# Valid state transitions: from_state -> set of allowed to_states
_VALID_TRANSITIONS = {
    SessionState.IDLE: {SessionState.ACTIVE, SessionState.IDLE},
    SessionState.ACTIVE: {SessionState.AGENT_RUNNING, SessionState.IDLE},
    SessionState.AGENT_RUNNING: {SessionState.ABORTING, SessionState.IDLE},
    SessionState.ABORTING: {SessionState.IDLE},
    SessionState.COMPACTING: {SessionState.IDLE, SessionState.ACTIVE},
}


class SessionContext:
    """Manages the runtime state of a single session.

    Provides:
    - State machine with validated transitions
    - Abort coordination via threading.Event
    - Message cache with lazy loading and dirty tracking
    - Lifecycle methods for agent switching and compaction

    Args:
        session_id: The unique identifier for this session.
        session_manager: Optional SessionManager for persistence operations.
    """

    def __init__(self, session_id, session_manager=None):
        # type: (str, Optional[SessionManager]) -> None
        self.session_id = session_id
        self._manager = session_manager
        self._state = SessionState.IDLE  # type: str
        self._lock = threading.Lock()
        self._abort_event = threading.Event()

        # Message cache
        self._messages_cache = []  # type: List[Dict[str, Any]]
        self._messages_dirty = True  # type: bool

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def transition(self, new_state):
        # type: (str) -> bool
        """Transition to a new state if valid.

        Args:
            new_state: The target state constant from SessionState.

        Returns:
            True if the transition was valid and applied.

        Raises:
            ValueError: If the transition is not allowed from the current state.
        """
        allowed = _VALID_TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            raise ValueError("Invalid state transition: {} -> {}".format(self._state, new_state))
        self._state = new_state
        return True

    @property
    def state(self):
        # type: () -> str
        """Return the current session state."""
        return self._state

    # ------------------------------------------------------------------
    # Abort coordination
    # ------------------------------------------------------------------

    def request_abort(self):
        # type: () -> None
        """Signal an abort request.

        Sets the abort event and transitions to ABORTING if currently
        in AGENT_RUNNING state.
        """
        self._abort_event.set()
        with self._lock:
            if self._state == SessionState.AGENT_RUNNING:
                self.transition(SessionState.ABORTING)

    def is_aborted(self):
        # type: () -> bool
        """Check if an abort has been requested."""
        return self._abort_event.is_set()

    def reset_abort(self):
        # type: () -> None
        """Clear the abort signal."""
        self._abort_event.clear()

    # ------------------------------------------------------------------
    # Message cache operations
    # ------------------------------------------------------------------

    def get_messages(self):
        # type: () -> List[Dict[str, Any]]
        """Get cached messages, loading from manager if dirty.

        Returns:
            List of message dicts.
        """
        if self._messages_dirty and self._manager is not None:
            self._messages_cache = self._manager.get_messages(self.session_id)
            self._messages_dirty = False
        return self._messages_cache

    def append_message(self, role, content, **kwargs):
        # type: (str, str, **Any) -> str
        """Append a message via the manager and invalidate cache.

        Args:
            role: Message role ('user', 'assistant', 'system', 'tool').
            content: Message text content.
            **kwargs: Additional arguments passed to manager.append_message
                      (e.g., tool_calls, tool_result_for, tool_name).

        Returns:
            The generated message ID string.
        """
        if self._manager is None:
            raise RuntimeError("No session manager available for append_message")
        message_id = self._manager.append_message(self.session_id, role, content, **kwargs)
        self._messages_dirty = True
        return message_id

    def refresh(self):
        # type: () -> None
        """Force the message cache to reload on next get_messages() call."""
        self._messages_dirty = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def switch_out(self):
        # type: () -> None
        """Switch this session out of active use.

        If the agent is running, sets the abort event first.
        Then forces the state to IDLE.
        """
        with self._lock:
            if self._state in (SessionState.AGENT_RUNNING, SessionState.ABORTING):
                self._abort_event.set()
                self._state = SessionState.IDLE

    def switch_in(self):
        # type: () -> None
        """Switch this session in for active use.

        Clears any pending abort and transitions to ACTIVE.
        Idempotent: safe to call when already ACTIVE.
        """
        with self._lock:
            self._abort_event.clear()
            if self._state != SessionState.ACTIVE:
                self.transition(SessionState.ACTIVE)

    def compact(self, system_prompt, summary):
        # type: (str, str) -> int
        """Compact the conversation history.

        Replaces all messages with a compacted version containing
        the system prompt and summary.

        Args:
            system_prompt: The system prompt to include.
            summary: The conversation summary.

        Returns:
            Number of messages after compaction.
        """
        if self._manager is None:
            raise RuntimeError("No session manager available for compact")

        compacted_messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "assistant",
                "content": summary,
            },
        ]

        count = self._manager.replace_messages(self.session_id, compacted_messages)
        self._messages_dirty = True
        return count

    def close(self):
        # type: () -> None
        """Final cleanup. Transition to IDLE state."""
        with self._lock:
            self._state = SessionState.IDLE
            self._abort_event.set()

    # ------------------------------------------------------------------
    # LLM message building
    # ------------------------------------------------------------------

    def build_messages_for_llm(self):
        # type: () -> List[Dict[str, Any]]
        """Build the message list to send to the LLM.

        Filters out messages that should not be sent to the LLM:
        - Messages with no role or no content
        - Messages with role="tool" that have no content
        - Messages marked as internal (if any)

        Returns:
            List of message dicts ready for LLM consumption.
        """
        messages = self.get_messages()
        filtered = []  # type: List[Dict[str, Any]]
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            # Skip messages with no role
            if not role:
                continue
            # Skip messages with no content — unless they have tool_calls (assistant)
            # or tool_result_for (tool). DeepSeek requires these to be present.
            has_tool_calls = msg.get("tool_calls") is not None
            has_tool_result = msg.get("tool_result_for") is not None
            if not content and not has_tool_calls and not has_tool_result:
                continue
            # Skip tool messages with no content and no tool_result_for
            if role == "tool" and not content:
                continue
            filtered.append(msg)
        return filtered
