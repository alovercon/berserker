"""
Agent-specific exceptions for berserker.

Provides a hierarchy of exception classes for agent-related errors:
- AgentNotFoundError: Raised when a requested agent does not exist.
- AgentSchemaValidationError: Raised when agent schema validation fails.
- AgentExecutionError: Raised when agent execution encounters an error.
- AgentPermissionError: Raised when an agent lacks permission for an action.
- AgentRegistrationError: Raised when agent registration fails.

Python 3.8.10 compatible: uses type comments, no inline annotations.
"""

from __future__ import annotations


class AgentNotFoundError(Exception):
    """Raised when a requested agent is not found in the registry."""

    def __init__(self, name, available=None):
        # type: (str, Optional[List[str]]) -> None
        if available:
            message = "Agent '{}' not found. Available agents: {}".format(
                name, ", ".join(sorted(available))
            )
        else:
            message = "Agent '{}' not found".format(name)
        super(AgentNotFoundError, self).__init__(message)
        self.name = name
        self.available = available


class AgentSchemaValidationError(Exception):
    """Raised when an agent schema fails validation."""

    def __init__(self, field, message):
        # type: (str, str) -> None
        super(AgentSchemaValidationError, self).__init__(
            "Schema validation failed for field '{}': {}".format(field, message)
        )
        self.field = field
        self.validation_message = message


class AgentExecutionError(Exception):
    """Raised when an agent encounters an error during execution."""

    def __init__(self, agent_name, message, cause=None):
        # type: (str, str, Optional[Exception]) -> None
        super(AgentExecutionError, self).__init__(
            "Agent '{}' execution failed: {}".format(agent_name, message)
        )
        self.agent_name = agent_name
        self.cause = cause


class AgentPermissionError(Exception):
    """Raised when an agent lacks permission for a requested action."""

    def __init__(self, agent_name, action, reason=None):
        # type: (str, str, Optional[str]) -> None
        message = "Agent '{}' denied permission for action '{}'".format(agent_name, action)
        if reason:
            message += ": {}".format(reason)
        super(AgentPermissionError, self).__init__(message)
        self.agent_name = agent_name
        self.action = action
        self.reason = reason


class AgentRegistrationError(Exception):
    """Raised when agent registration or unregistration fails."""

    def __init__(self, name, message):
        # type: (str, str) -> None
        super(AgentRegistrationError, self).__init__(
            "Failed to register agent '{}': {}".format(name, message)
        )
        self.name = name


# Import Optional and List at module level for type comments
from typing import List, Optional  # noqa: E402, F401
