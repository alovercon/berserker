"""
berserker.agent — Agent system for berserker.

Exports:
    AgentInfo: Dataclass for agent configuration (legacy, backward compatible).
    AgentManager: Manager class for agent lifecycle and execution.
    agent_manager: Singleton AgentManager instance.
    BUILT_IN_AGENTS: List of 7 built-in agent names.
    find_nearest_agents_md: Function to find AGENTS.md files hierarchically.
    load_hierarchical_instructions: Function to load hierarchical instructions.
    build_progressive_disclosure_context: Function to build task-specific context.

Phase 1 additions (schema-driven framework):
    AgentSchema: Validated dataclass for agent configuration (13 fields).
    AgentMode: Enum for agent execution modes (primary, subagent, hidden).
    AgentVisibility: Enum for agent visibility levels.
    BaseAgent: Abstract base class for all agents.
    BuiltInAgent: Concrete class for built-in agents.
    CustomAgent: Concrete class for user-defined agents.
    validate_agent_schema: Function to validate dict → AgentSchema.
    Exceptions: AgentNotFoundError, AgentSchemaValidationError,
                AgentExecutionError, AgentPermissionError, AgentRegistrationError.
"""

from __future__ import annotations

# Legacy exports (backward compatible)
from berserker.agent.manager import (
    AgentInfo,
    AgentManager,
    agent_manager,
    BUILT_IN_AGENTS,
)

from berserker.agent.agents_md import (
    find_nearest_agents_md,
    load_hierarchical_instructions,
    build_progressive_disclosure_context,
)

# Phase 1: Schema-driven framework exports
from berserker.agent.modes import (
    AgentMode,
    AgentVisibility,
    is_primary,
    is_subagent,
    is_hidden,
)

from berserker.agent.schema import (
    AgentSchema,
    validate_agent_schema,
)

from berserker.agent.base import (
    BaseAgent,
    BuiltInAgent,
    CustomAgent,
)

from berserker.agent.exceptions import (
    AgentNotFoundError,
    AgentSchemaValidationError,
    AgentExecutionError,
    AgentPermissionError,
    AgentRegistrationError,
)

# Phase 2: Dynamic registry and factory exports
from berserker.agent.registry import (
    AgentRegistry,
    registry,
)

from berserker.agent.factory import (
    create_agent_from_schema,
    create_builtin_agent,
)

__all__ = [
    # Legacy (backward compatible)
    "AgentInfo",
    "AgentManager",
    "agent_manager",
    "BUILT_IN_AGENTS",
    "find_nearest_agents_md",
    "load_hierarchical_instructions",
    "build_progressive_disclosure_context",
    # Phase 1: Schema-driven framework
    "AgentSchema",
    "AgentMode",
    "AgentVisibility",
    "is_primary",
    "is_subagent",
    "is_hidden",
    "validate_agent_schema",
    "BaseAgent",
    "BuiltInAgent",
    "CustomAgent",
    "AgentNotFoundError",
    "AgentSchemaValidationError",
    "AgentExecutionError",
    "AgentPermissionError",
    "AgentRegistrationError",
    # Phase 2: Dynamic registry and factory
    "AgentRegistry",
    "registry",
    "create_agent_from_schema",
    "create_builtin_agent",
]