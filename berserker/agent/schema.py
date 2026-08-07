"""
Validated agent schema for berserker.

Defines:
- AgentSchema dataclass with 13 fields matching the reference implementation.
- validate_agent_schema() function for dict-to-schema conversion.
- AgentSchemaValidationError (re-exported from exceptions).

Python 3.8.10 compatible: uses @dataclass, typing.Optional, type comments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from berserker.agent.exceptions import AgentSchemaValidationError
from berserker.agent.modes import AgentMode


# ---------------------------------------------------------------------------
# AgentSchema Dataclass
# ---------------------------------------------------------------------------


@dataclass
class AgentSchema(object):
    """Validated schema for a single agent configuration.

    This dataclass replaces the imperative AgentInfo class with a validated,
    schema-driven approach. It has 13 fields matching the reference implementation.

    Attributes:
        name: Unique identifier for the agent (e.g. 'build', 'plan').
        description: Human-readable description of the agent's purpose.
        mode: Agent mode — 'primary', 'subagent', or 'hidden'.
        native: True for built-in agents, False for user-defined.
        hidden: Visibility flag — True hides agent from UI lists.
        top_p: Nucleus sampling parameter (0.0-1.0).
        temperature: Temperature sampling parameter (0.0-2.0).
        color: UI display color (hex string or CSS color name).
        permission: Permission level — 'full' or 'restricted'.
        model: Model identifier this agent uses (e.g. 'gpt-4o').
        variant: Optional model variant identifier.
        prompt: System prompt that defines the agent's behavior.
        options: Additional agent-specific options dictionary.
        steps: Maximum number of execution steps/stages.
    """

    # Required fields
    name: str = ""
    description: str = ""
    mode: str = ""
    model: str = ""
    prompt: str = ""

    # Optional fields with defaults
    native: bool = False
    hidden: bool = False
    permission: str = "full"
    options: Dict[str, Any] = field(default_factory=dict)

    # Optional sampling/behavior fields
    top_p: Optional[float] = None
    temperature: Optional[float] = None
    color: Optional[str] = None
    variant: Optional[str] = None
    steps: Optional[int] = None

    def __post_init__(self):
        # type: () -> None
        """Validate schema after initialization."""
        self._validate_required()
        self._validate_mode()
        self._validate_permission()
        self._validate_sampling_params()
        self._validate_name_format()

    def _validate_required(self):
        # type: () -> None
        """Validate that all required fields are non-empty."""
        required_fields = ["name", "description", "mode", "model", "prompt"]
        for field_name in required_fields:
            value = getattr(self, field_name)
            if not value or (isinstance(value, str) and not value.strip()):
                raise AgentSchemaValidationError(
                    field_name,
                    "Field '{}' is required and cannot be empty".format(field_name),
                )

    def _validate_mode(self):
        # type: () -> None
        """Validate that mode is a valid AgentMode value."""
        try:
            AgentMode.from_string(self.mode)
        except ValueError:
            raise AgentSchemaValidationError(
                "mode",
                "Invalid mode '{}'. Must be one of: {}".format(
                    self.mode, ", ".join(AgentMode.valid_values())
                ),
            )

    def _validate_permission(self):
        # type: () -> None
        """Validate that permission is a known value."""
        valid_permissions = ["full", "restricted"]
        if self.permission not in valid_permissions:
            raise AgentSchemaValidationError(
                "permission",
                "Invalid permission '{}'. Must be one of: {}".format(
                    self.permission, ", ".join(valid_permissions)
                ),
            )

    def _validate_sampling_params(self):
        # type: () -> None
        """Validate top_p and temperature ranges."""
        if self.top_p is not None:
            if not isinstance(self.top_p, (int, float)):
                raise AgentSchemaValidationError(
                    "top_p",
                    "top_p must be a number, got {}".format(type(self.top_p).__name__),
                )
            if not (0.0 <= self.top_p <= 1.0):
                raise AgentSchemaValidationError(
                    "top_p",
                    "top_p must be between 0.0 and 1.0, got {}".format(self.top_p),
                )

        if self.temperature is not None:
            if not isinstance(self.temperature, (int, float)):
                raise AgentSchemaValidationError(
                    "temperature",
                    "temperature must be a number, got {}".format(
                        type(self.temperature).__name__
                    ),
                )
            if not (0.0 <= self.temperature <= 2.0):
                raise AgentSchemaValidationError(
                    "temperature",
                    "temperature must be between 0.0 and 2.0, got {}".format(
                        self.temperature
                    ),
                )

    def _validate_name_format(self):
        # type: () -> None
        """Validate that name follows naming conventions."""
        # Names must be lowercase alphanumeric with hyphens allowed
        if not re.match(r"^[a-z][a-z0-9-]*$", self.name):
            raise AgentSchemaValidationError(
                "name",
                "Name '{}' must be lowercase alphanumeric with hyphens, "
                "starting with a letter".format(self.name),
            )

    def to_dict(self):
        # type: () -> Dict[str, Any]
        """Convert schema to a dictionary representation.

        Returns:
            Dictionary with all schema fields.
        """
        return {
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "native": self.native,
            "hidden": self.hidden,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "color": self.color,
            "permission": self.permission,
            "model": self.model,
            "variant": self.variant,
            "prompt": self.prompt,
            "options": dict(self.options),
            "steps": self.steps,
        }

    @classmethod
    def from_dict(cls, data):
        # type: (Dict[str, Any]) -> AgentSchema
        """Create an AgentSchema from a dictionary.

        Args:
            data: Dictionary with schema fields.

        Returns:
            Validated AgentSchema instance.

        Raises:
            AgentSchemaValidationError: If validation fails.
        """
        # Extract known fields, ignore unknown ones
        known_fields = {
            "name", "description", "mode", "native", "hidden",
            "top_p", "temperature", "color", "permission",
            "model", "variant", "prompt", "options", "steps",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Validation Function
# ---------------------------------------------------------------------------


def validate_agent_schema(data):
    # type: (Dict[str, Any]) -> AgentSchema
    """Validate a dictionary and return an AgentSchema.

    This is the primary entry point for converting raw configuration
    dictionaries into validated AgentSchema instances.

    Args:
        data: Dictionary with agent configuration fields.

    Returns:
        Validated AgentSchema instance.

    Raises:
        AgentSchemaValidationError: If any field fails validation.

    Example:
        >>> schema = validate_agent_schema({
        ...     "name": "my-agent",
        ...     "description": "My custom agent",
        ...     "mode": "subagent",
        ...     "model": "gpt-4o",
        ...     "prompt": "You are a helpful assistant...",
        ... })
    """
    if not isinstance(data, dict):
        raise AgentSchemaValidationError(
            "data",
            "Expected a dictionary, got {}".format(type(data).__name__),
        )

    return AgentSchema.from_dict(data)
