"""
Base agent class hierarchy for berserker.

Defines:
- BaseAgent: Abstract base class for all agents.
- BuiltInAgent: Concrete class for built-in agents.
- CustomAgent: Concrete class for user-defined agents.

Python 3.8.10 compatible: uses abc.ABC, type comments.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from berserker.agent.schema import AgentSchema

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all agents.

    This class defines the interface that all agents must implement.
    It cannot be instantiated directly — use BuiltInAgent or CustomAgent.

    Attributes:
        schema: The validated AgentSchema for this agent.
    """

    def __init__(self, schema):
        # type: (AgentSchema) -> None
        """Initialize the base agent with a validated schema.

        Args:
            schema: Validated AgentSchema instance.

        Raises:
            TypeError: If schema is not an AgentSchema instance.
        """
        if not isinstance(schema, AgentSchema):
            raise TypeError(
                "Expected AgentSchema, got {}".format(type(schema).__name__)
            )
        self._schema = schema

    @property
    def name(self):
        # type: () -> str
        """Return the agent's unique identifier."""
        return self._schema.name

    @property
    def mode(self):
        # type: () -> str
        """Return the agent's execution mode."""
        return self._schema.mode

    @property
    def model(self):
        # type: () -> str
        """Return the model identifier this agent uses."""
        return self._schema.model

    @property
    def description(self):
        # type: () -> str
        """Return the agent's human-readable description."""
        return self._schema.description

    @property
    def permission(self):
        # type: () -> str
        """Return the agent's permission level."""
        return self._schema.permission

    @property
    def schema(self):
        # type: () -> AgentSchema
        """Return the full AgentSchema for this agent."""
        return self._schema

    @property
    def tools(self):
        # type: () -> List[str]
        """Return the list of tool IDs this agent is allowed to use.

        Extracted from schema.options['tools']. Defaults to empty list.
        """
        return list(self._schema.options.get("tools", []))

    @property
    def native(self):
        # type: () -> bool
        """Return whether this agent is built-in (native) or user-defined."""
        return self._schema.native

    @property
    def system_prompt(self):
        # type: () -> str
        """Return the raw system prompt string (without template injections)."""
        return self._schema.prompt

    @property
    def max_tool_iterations(self):
        # type: () -> int
        """Return the maximum tool call loop iterations for this agent.

        Extracted from schema.options['max_tool_iterations']. Defaults to 100000.
        """
        return self._schema.options.get("max_tool_iterations", 100000)

    def can_use_tool(self, tool_name):
        # type: (str) -> bool
        """Check if this agent can use the specified tool.

        Default implementation checks the agent's permission level.
        Subclasses may override for more granular control.

        Args:
            tool_name: The tool identifier to check.

        Returns:
            True if the agent can use the tool, False otherwise.
        """
        if self._schema.permission == "full":
            return True
        # Restricted agents need explicit tool allowance
        allowed_tools = self._schema.options.get("tools", [])  # type: List[str]
        return tool_name in allowed_tools

    def get_system_prompt(self, injections=None):
        # type: (Optional[Dict[str, str]]) -> str
        """Return the system prompt with optional template injections.

        Args:
            injections: Optional dictionary of template variable replacements.

        Returns:
            The system prompt string with injections applied.
        """
        prompt = self._schema.prompt
        if injections:
            try:
                prompt = prompt.format(**injections)
            except (KeyError, ValueError) as e:
                logger.warning(
                    "Failed to apply injections for agent '%s': %s",
                    self.name,
                    e,
                )
        return prompt

    @abstractmethod
    def execute(self, messages, session_id, tool_registry, **kwargs):
        # type: (List[Any], str, Any, Any) -> Dict[str, Any]
        """Execute the agent against the given messages.

        This is the core method that each agent type must implement.

        Args:
            messages: List of ChatMessage objects forming the conversation.
            session_id: Session ID for message persistence.
            tool_registry: ToolRegistry for executing tool calls.
            **kwargs: Additional execution parameters.

        Returns:
            Dict with execution results (e.g., 'content', 'usage').

        Raises:
            NotImplementedError: Must be implemented by subclasses.
        """
        raise NotImplementedError(
            "Subclasses of BaseAgent must implement execute()"
        )

    def __repr__(self):
        # type: () -> str
        return "{}(name={!r}, mode={!r}, model={!r})".format(
            self.__class__.__name__,
            self.name,
            self.mode,
            self.model,
        )

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, BaseAgent):
            return False
        return self._schema == other._schema


class BuiltInAgent(BaseAgent):
    """Concrete agent class for built-in (native) agents.

    Built-in agents are part of the core berserker distribution.
    They have native=True in their schema.
    """

    def __init__(self, schema):
        # type: (AgentSchema) -> None
        """Initialize a built-in agent.

        Args:
            schema: Validated AgentSchema with native=True.
        """
        super(BuiltInAgent, self).__init__(schema)
        # Ensure native flag is set
        object.__setattr__(self._schema, "native", True)

    def execute(self, messages, session_id, tool_registry, **kwargs):
        # type: (List[Any], str, Any, Any) -> Dict[str, Any]
        """Execute the built-in agent.

        Delegates to the existing AgentManager.execute() logic.
        This method will be wired up during Phase 2 migration.

        Args:
            messages: List of ChatMessage objects.
            session_id: Session ID for persistence.
            tool_registry: ToolRegistry for tool execution.
            **kwargs: Additional parameters (on_tool_call, on_permission_ask, etc.).

        Returns:
            Dict with 'content' and 'usage' keys.

        Raises:
            NotImplementedError: Until Phase 2 migration is complete.
        """
        # TODO: Wire up to AgentManager.execute() in Phase 2
        # For now, this is a placeholder that indicates the agent
        # needs to be executed through the existing manager.
        logger.info(
            "BuiltInAgent '%s' execute called — "
            "delegation to AgentManager pending Phase 2",
            self.name,
        )
        return {
            "content": "",
            "usage": None,
            "_delegation_pending": True,
            "_agent_name": self.name,
        }


class CustomAgent(BaseAgent):
    """Concrete agent class for user-defined (custom) agents.

    Custom agents are loaded from configuration files or generated at runtime.
    They have native=False in their schema.
    """

    def __init__(self, schema):
        # type: (AgentSchema) -> None
        """Initialize a custom agent.

        Args:
            schema: Validated AgentSchema with native=False.
        """
        super(CustomAgent, self).__init__(schema)
        # Ensure native flag is set
        object.__setattr__(self._schema, "native", False)

    def execute(self, messages, session_id, tool_registry, **kwargs):
        # type: (List[Any], str, Any, Any) -> Dict[str, Any]
        """Execute the custom agent.

        Uses the same execution path as built-in agents but with
        custom configuration from the schema.

        Args:
            messages: List of ChatMessage objects.
            session_id: Session ID for persistence.
            tool_registry: ToolRegistry for tool execution.
            **kwargs: Additional parameters.

        Returns:
            Dict with 'content' and 'usage' keys.

        Raises:
            NotImplementedError: Until Phase 2 migration is complete.
        """
        # TODO: Wire up to AgentManager.execute() in Phase 2
        logger.info(
            "CustomAgent '%s' execute called — "
            "delegation to AgentManager pending Phase 2",
            self.name,
        )
        return {
            "content": "",
            "usage": None,
            "_delegation_pending": True,
            "_agent_name": self.name,
        }
