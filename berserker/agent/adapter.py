"""FrontendAdapter — unified interface for CLI/GUI/Web frontends."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional
import threading

from berserker.agent.events import AgentEvent

logger = logging.getLogger(__name__)


class FrontendAdapter(ABC):
    """Abstract interface for frontend-agent interaction.

    Provides a unified API that CLI, GUI, and Web frontends use to:
    - Execute agents with callbacks for tool calls, permissions, status
    - Cancel running executions
    - Query agent status
    - Subscribe to agent events

    Python 3.8.10 compatible: uses type comments.
    """

    def __init__(self, agent_manager, tool_registry, display=None):
        # type: (Any, Any, Optional[Any]) -> None
        """Initialize with required dependencies.

        Args:
            agent_manager: AgentManager instance.
            tool_registry: ToolRegistry instance.
            display: Optional DisplayAdapter for output operations.
        """
        self._agent_manager = agent_manager
        self._tool_registry = tool_registry
        self._display = display
        self._event_subscribers = []  # type: List[Callable[[Any], None]]
        self._abort_event = threading.Event()
        self._is_executing = False
        self._lock = threading.Lock()

    @abstractmethod
    def execute_agent(
        self,
        agent_name,  # type: str
        messages,  # type: List[Any]
        session_id,  # type: str
        on_tool_call=None,  # type: Optional[Callable]
        on_permission_ask=None,  # type: Optional[Callable]
        extra=None,  # type: Optional[Dict[str, Any]]
    ):
        # type: (...) -> Dict[str, Any]
        """Execute an agent turn.

        Args:
            agent_name: Name of agent to execute.
            messages: List of ChatMessage objects.
            session_id: Current session ID.
            on_tool_call: Optional callback(tool_name, args, result).
            on_permission_ask: Optional callback(tool_name, args) -> bool.
            extra: Optional dict of extra context for tools.

        Returns:
            Dict with keys: 'content', 'usage', 'finish_reason'.
        """
        pass

    @abstractmethod
    def cancel_execution(self):
        # type: () -> None
        """Signal the running agent to abort."""
        pass

    @abstractmethod
    def get_agent_status(self, session_id=None):
        # type: (Optional[str]) -> Dict[str, Any]
        """Get current agent status.

        Args:
            session_id: Optional session ID to filter status.

        Returns:
            Dict with keys: 'is_executing', 'current_agent', 'session_id'.
        """
        pass

    def subscribe_events(self, callback):
        # type: (Callable[[Any], None]) -> None
        """Subscribe to agent events.

        Args:
            callback: Callable that receives AgentEvent objects.
        """
        if callback not in self._event_subscribers:
            self._event_subscribers.append(callback)

    def unsubscribe_events(self, callback):
        # type: (Callable[[Any], None]) -> None
        """Unsubscribe from agent events.

        Args:
            callback: The callable to remove.
        """
        if callback in self._event_subscribers:
            self._event_subscribers.remove(callback)

    def _publish_event(self, event):
        # type: (Any) -> None
        """Publish an event to all subscribers.

        Args:
            event: AgentEvent instance to publish.
        """
        for callback in self._event_subscribers:
            try:
                callback(event)
            except Exception as e:
                logger.warning("Subscriber callback error during event publishing: %s", e)
                pass  # Subscriber errors should not break execution

    def list_agents(self):
        # type: () -> List[Any]
        """List all registered agents.

        Returns:
            List of agent configurations from AgentManager.
        """
        return self._agent_manager.list()

    def get_agent(self, name):
        # type: (str) -> Any
        """Get agent by name.

        Args:
            name: Agent identifier.

        Returns:
            Agent configuration from AgentManager.
        """
        return self._agent_manager.get(name)
