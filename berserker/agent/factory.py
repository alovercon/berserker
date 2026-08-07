"""
Agent factory for berserker.

Provides:
- create_agent_from_schema(schema) — creates BuiltInAgent or CustomAgent from schema.
- create_builtin_agent(name) — creates a built-in agent by name.
- _BUILTIN_AGENT_SCHEMAS — internal mapping of built-in agent configurations.

Python 3.8.10 compatible: uses from __future__ import annotations, typing imports,
type comments.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from berserker.agent.base import BuiltInAgent, CustomAgent
from berserker.agent.exceptions import AgentNotFoundError, AgentSchemaValidationError
from berserker.agent.prompts import (
    _ALL_TOOL_IDS,
    _SUBAGENT_TOOL_IDS,
    _DEFAULT_MODEL,
    _MINIMAL_TOOL_IDS,
    _READ_ONLY_TOOL_IDS,
    _SYSTEM_PROMPT_BUILD,
    _SYSTEM_PROMPT_COMPACTION,
    _SYSTEM_PROMPT_EXPLORE,
    _SYSTEM_PROMPT_GENERAL,
    _SYSTEM_PROMPT_PLAN,
    _SYSTEM_PROMPT_SUMMARY,
    _SYSTEM_PROMPT_TITLE,
    _SYSTEM_PROMPT_CONSULTANT,
    _SYSTEM_PROMPT_CRITIC,
    _SYSTEM_PROMPT_ORCHESTRATOR,
)
from berserker.agent.schema import AgentSchema


# ---------------------------------------------------------------------------
# Built-in Agent Schema Registry
# ---------------------------------------------------------------------------

_BUILTIN_AGENT_SCHEMAS = {
    "berserker": AgentSchema(
        name="berserker",
        description="Primary coding agent for implementation tasks",
        mode="primary",
        native=True,
        hidden=False,
        permission="full",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_BUILD,
        options={"tools": list(_ALL_TOOL_IDS)},
    ),
    "plan": AgentSchema(
        name="plan",
        description="Code analysis and planning agent",
        mode="primary",
        native=True,
        hidden=False,
        permission="restricted",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_PLAN,
        options={"tools": list(_READ_ONLY_TOOL_IDS)},
    ),
    "general": AgentSchema(
        name="general",
        description="General-purpose subagent for complex multi-step tasks",
        mode="subagent",
        native=True,
        hidden=False,
        permission="full",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_GENERAL,
        options={"tools": list(_SUBAGENT_TOOL_IDS)},
    ),
    "explore": AgentSchema(
        name="explore",
        description="Codebase exploration and discovery subagent",
        mode="subagent",
        native=True,
        hidden=False,
        permission="restricted",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_EXPLORE,
        options={"tools": list(_READ_ONLY_TOOL_IDS)},
    ),
    "compaction": AgentSchema(
        name="compaction",
        description="Hidden agent for compressing conversation history",
        mode="hidden",
        native=True,
        hidden=True,
        permission="restricted",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_COMPACTION,
        options={"tools": list(_READ_ONLY_TOOL_IDS)},
    ),
    "title": AgentSchema(
        name="title",
        description="Hidden agent for generating session titles",
        mode="hidden",
        native=True,
        hidden=True,
        permission="restricted",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_TITLE,
        options={"tools": list(_MINIMAL_TOOL_IDS)},
    ),
    "summary": AgentSchema(
        name="summary",
        description="Hidden agent for generating session summaries",
        mode="hidden",
        native=True,
        hidden=True,
        permission="restricted",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_SUMMARY,
        options={"tools": list(_READ_ONLY_TOOL_IDS)},
    ),
    "consultant": AgentSchema(
        name="consultant",
        description="Pre-planning consultant that analyzes requests to identify hidden intentions, ambiguities, and AI failure points.",
        mode="subagent",
        native=True,
        hidden=False,
        permission="restricted",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_CONSULTANT,
        options={"tools": ["read", "ls", "glob", "grep", "lsp"]},
    ),
    "critic": AgentSchema(
        name="critic",
        description="Expert reviewer for evaluating work plans against rigorous clarity, verifiability, and completeness standards.",
        mode="subagent",
        native=True,
        hidden=False,
        permission="restricted",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_CRITIC,
        options={"tools": ["read", "ls", "glob", "grep"]},
    ),
    "executor": AgentSchema(
        name="executor",
        description="Master orchestrator agent that coordinates specialized agents to complete todo lists.",
        mode="primary",
        native=True,
        hidden=False,
        permission="full",
        model=_DEFAULT_MODEL,
        prompt=_SYSTEM_PROMPT_ORCHESTRATOR,
        options={"tools": [tool for tool in _ALL_TOOL_IDS if tool != "task"]},
    ),
}  # type: Dict[str, AgentSchema]


# ---------------------------------------------------------------------------
# Factory Functions
# ---------------------------------------------------------------------------


def create_agent_from_schema(schema):
    # type: (AgentSchema) -> BuiltInAgent | CustomAgent
    """Create an agent instance from a validated AgentSchema.

    Returns a BuiltInAgent if schema.native is True, otherwise a CustomAgent.

    Args:
        schema: A validated AgentSchema instance.

    Returns:
        A BuiltInAgent or CustomAgent instance based on the schema's native flag.

    Raises:
        AgentSchemaValidationError: If the schema is invalid (missing required fields,
            invalid mode, etc.). The AgentSchema dataclass validates on construction,
            so this typically means the schema was not properly validated before calling.
    """
    # Validate schema has required fields (AgentSchema.__post_init__ does this,
    # but we double-check for safety when receiving external schemas)
    if not schema.name or not schema.name.strip():
        raise AgentSchemaValidationError(
            "name",
            "Agent name is required and cannot be empty",
        )
    if not schema.description or not schema.description.strip():
        raise AgentSchemaValidationError(
            "description",
            "Agent description is required and cannot be empty",
        )
    if not schema.mode or not schema.mode.strip():
        raise AgentSchemaValidationError(
            "mode",
            "Agent mode is required and cannot be empty",
        )
    if not schema.model or not schema.model.strip():
        raise AgentSchemaValidationError(
            "model",
            "Agent model is required and cannot be empty",
        )
    if not schema.prompt or not schema.prompt.strip():
        raise AgentSchemaValidationError(
            "prompt",
            "Agent prompt is required and cannot be empty",
        )

    if schema.native:
        return BuiltInAgent(schema)
    else:
        return CustomAgent(schema)


def create_builtin_agent(name):
    # type: (str) -> BuiltInAgent
    """Create a built-in agent by its registered name.

    Looks up the agent configuration from the internal built-in schema registry
    and returns a BuiltInAgent instance.

    Args:
        name: The built-in agent name (e.g., "build", "plan", "general",
            "explore", "compaction", "title", "summary").

    Returns:
        A BuiltInAgent instance configured with the built-in schema.

    Raises:
        AgentNotFoundError: If the name is not a recognized built-in agent.
    """
    if name not in _BUILTIN_AGENT_SCHEMAS:
        raise AgentNotFoundError(
            name,
            available=list(_BUILTIN_AGENT_SCHEMAS.keys()),
        )
    schema = _BUILTIN_AGENT_SCHEMAS[name]
    return BuiltInAgent(schema)

