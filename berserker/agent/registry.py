"""
Thread-safe agent registry for berserker.

Provides a centralized storage for BaseAgent instances with CRUD operations.
All methods are protected by a threading.RLock for concurrent access safety.

Python 3.8.10 compatible: uses type comments, typing module imports.
"""

from __future__ import annotations

import threading
from typing import Dict, List

from berserker.agent.base import BaseAgent
from berserker.agent.exceptions import AgentNotFoundError, AgentRegistrationError


class AgentRegistry(object):
    """Thread-safe registry for storing and retrieving BaseAgent instances.

    This class provides CRUD operations for agent management. All methods
    are protected by a reentrant lock (RLock) to ensure thread safety.

    Usage:
        registry = AgentRegistry()
        registry.register(my_agent)
        agent = registry.get("agent_name")
        agents = registry.list_all()
    """

    def __init__(self):
        # type: () -> None
        """Initialize an empty agent registry with a reentrant lock."""
        self._agents = {}  # type: Dict[str, BaseAgent]
        self._lock = threading.RLock()

    def register(self, agent):
        # type: (BaseAgent) -> None
        """Register a BaseAgent instance in the registry.

        Args:
            agent: A BaseAgent instance to register.

        Raises:
            AgentRegistrationError: If an agent with the same name already exists.
            TypeError: If agent is not a BaseAgent instance.
        """
        if not isinstance(agent, BaseAgent):
            raise TypeError(
                "Expected BaseAgent, got {}".format(type(agent).__name__)
            )

        with self._lock:
            name = agent.name
            if name in self._agents:
                raise AgentRegistrationError(
                    name,
                    "An agent with name '{}' is already registered".format(name),
                )
            self._agents[name] = agent

    def unregister(self, name):
        # type: (str) -> None
        """Remove an agent from the registry by name.

        Args:
            name: The name of the agent to remove.

        Raises:
            AgentNotFoundError: If no agent with the given name exists.
        """
        with self._lock:
            if name not in self._agents:
                raise AgentNotFoundError(name)
            del self._agents[name]

    def get(self, name):
        # type: (str) -> BaseAgent
        """Retrieve an agent by name.

        Args:
            name: The name of the agent to retrieve.

        Returns:
            The BaseAgent instance with the given name.

        Raises:
            AgentNotFoundError: If no agent with the given name exists.
        """
        with self._lock:
            if name not in self._agents:
                raise AgentNotFoundError(name)
            return self._agents[name]

    def list_all(self):
        # type: () -> List[BaseAgent]
        """Return a list of all registered agents.

        Returns:
            A list of all BaseAgent instances in the registry.
        """
        with self._lock:
            return list(self._agents.values())

    def list_by_mode(self, mode):
        # type: (str) -> List[BaseAgent]
        """Return a list of agents filtered by mode.

        Args:
            mode: The mode string to filter by (e.g., "primary", "subagent").

        Returns:
            A list of BaseAgent instances matching the given mode.
        """
        with self._lock:
            return [agent for agent in self._agents.values() if agent.mode == mode]

    def list_primary(self):
        # type: () -> List[BaseAgent]
        """Return a list of agents with mode='primary'.

        Returns:
            A list of BaseAgent instances with mode set to "primary".
        """
        return self.list_by_mode("primary")

    def list_subagents(self):
        # type: () -> List[BaseAgent]
        """Return a list of agents with mode='subagent'.

        Returns:
            A list of BaseAgent instances with mode set to "subagent".
        """
        return self.list_by_mode("subagent")

    def has(self, name):
        # type: (str) -> bool
        """Check if an agent with the given name exists.

        Args:
            name: The name to check.

        Returns:
            True if an agent with the given name is registered, False otherwise.
        """
        with self._lock:
            return name in self._agents

    def clear(self):
        # type: () -> None
        """Remove all agents from the registry.

        This method is primarily intended for testing purposes.
        """
        with self._lock:
            self._agents.clear()

    def count(self):
        # type: () -> int
        """Return the number of registered agents.

        Returns:
            The count of agents currently in the registry.
        """
        with self._lock:
            return len(self._agents)


# Module-level singleton instance
registry = AgentRegistry()
