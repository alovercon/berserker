"""
Tests for berserker.agent.factory module.

Covers:
- create_agent_from_schema(): schema-driven agent creation (native and custom).
- create_builtin_agent(): name-based built-in agent creation.
- Agent properties after creation (native, mode, permission, schema fields).
- Error handling for invalid schemas and unknown agent names.
"""

import pytest

from berserker.agent.factory import create_agent_from_schema, create_builtin_agent
from berserker.agent.base import BuiltInAgent, CustomAgent
from berserker.agent.exceptions import AgentNotFoundError, AgentSchemaValidationError
from berserker.agent.schema import AgentSchema, validate_agent_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def builtin_schema_dict():
    """Return a schema dictionary with native=True."""
    return {
        "name": "test-builtin",
        "description": "A test built-in agent",
        "mode": "subagent",
        "native": True,
        "model": "gpt-4o",
        "prompt": "You are a test built-in agent.",
        "permission": "full",
    }


@pytest.fixture
def custom_schema_dict():
    """Return a schema dictionary with native=False."""
    return {
        "name": "test-custom",
        "description": "A test custom agent",
        "mode": "primary",
        "native": False,
        "model": "gpt-4o-mini",
        "prompt": "You are a test custom agent.",
        "permission": "restricted",
    }


@pytest.fixture
def minimal_schema_dict():
    """Return a minimal schema dictionary (no native field)."""
    return {
        "name": "minimal-agent",
        "description": "A minimal agent",
        "mode": "subagent",
        "model": "gpt-4o",
        "prompt": "You are a minimal agent.",
    }


# ---------------------------------------------------------------------------
# create_agent_from_schema() Tests
# ---------------------------------------------------------------------------


class TestCreateAgentFromSchema:
    """Tests for the create_agent_from_schema() factory function."""

    def test_create_builtin_agent_from_schema(self, builtin_schema_dict):
        """Schema with native=True returns BuiltInAgent."""
        schema = validate_agent_schema(builtin_schema_dict)
        agent = create_agent_from_schema(schema)
        assert isinstance(agent, BuiltInAgent)

    def test_create_custom_agent_from_schema(self, custom_schema_dict):
        """Schema with native=False returns CustomAgent."""
        schema = validate_agent_schema(custom_schema_dict)
        agent = create_agent_from_schema(schema)
        assert isinstance(agent, CustomAgent)

    def test_create_agent_preserves_schema(self, builtin_schema_dict):
        """Returned agent has correct schema fields."""
        schema = validate_agent_schema(builtin_schema_dict)
        agent = create_agent_from_schema(schema)
        assert agent.name == "test-builtin"
        assert agent.description == "A test built-in agent"
        assert agent.mode == "subagent"
        assert agent.model == "gpt-4o"
        assert agent.permission == "full"

    def test_create_agent_invalid_schema(self):
        """Invalid dict raises AgentSchemaValidationError."""
        invalid_data = {"name": "bad"}  # Missing required fields
        with pytest.raises(AgentSchemaValidationError):
            validate_agent_schema(invalid_data)

    def test_create_agent_from_non_dict_raises(self):
        """Non-dict input raises AgentSchemaValidationError."""
        with pytest.raises(AgentSchemaValidationError):
            validate_agent_schema("not a dict")

    def test_create_agent_from_list_raises(self):
        """List input raises AgentSchemaValidationError."""
        with pytest.raises(AgentSchemaValidationError):
            validate_agent_schema([{"name": "test"}])

    def test_create_agent_from_schema_object(self, builtin_schema_dict):
        """Passing an AgentSchema object directly works."""
        schema = AgentSchema.from_dict(builtin_schema_dict)
        agent = create_agent_from_schema(schema)
        assert isinstance(agent, BuiltInAgent)
        assert agent.name == "test-builtin"

    def test_create_custom_agent_from_schema_object(self, custom_schema_dict):
        """Passing a custom AgentSchema object returns CustomAgent."""
        schema = AgentSchema.from_dict(custom_schema_dict)
        agent = create_agent_from_schema(schema)
        assert isinstance(agent, CustomAgent)

    def test_create_agent_defaults_native_false(self, minimal_schema_dict):
        """Schema without native field defaults to CustomAgent."""
        schema = validate_agent_schema(minimal_schema_dict)
        agent = create_agent_from_schema(schema)
        assert isinstance(agent, CustomAgent)
        assert agent.schema.native is False


# ---------------------------------------------------------------------------
# create_builtin_agent() Tests
# ---------------------------------------------------------------------------


class TestCreateBuiltinAgent:
    """Tests for the create_builtin_agent() factory function."""

    BUILTIN_NAMES = [
        "berserker",
        "plan",
        "general",
        "explore",
        "compaction",
        "title",
        "summary",
    ]

    def test_create_all_builtin_agents(self):
        """Loop through all 7 built-in names, verify each creates BuiltInAgent."""
        for name in self.BUILTIN_NAMES:
            agent = create_builtin_agent(name)
            assert isinstance(agent, BuiltInAgent), (
                "Expected BuiltInAgent for '{}', got {}".format(
                    name, type(agent).__name__
                )
            )
            assert agent.name == name

    def test_create_build_agent(self):
        """Specifically test 'build' agent."""
        agent = create_builtin_agent("berserker")
        assert isinstance(agent, BuiltInAgent)
        assert agent.name == "berserker"
        assert agent.schema.native is True

    def test_create_plan_agent(self):
        """Specifically test 'plan' agent."""
        agent = create_builtin_agent("plan")
        assert isinstance(agent, BuiltInAgent)
        assert agent.name == "plan"
        assert agent.schema.native is True

    def test_create_general_agent(self):
        """Specifically test 'general' agent."""
        agent = create_builtin_agent("general")
        assert isinstance(agent, BuiltInAgent)
        assert agent.name == "general"
        assert agent.schema.native is True

    def test_create_explore_agent(self):
        """Specifically test 'explore' agent."""
        agent = create_builtin_agent("explore")
        assert isinstance(agent, BuiltInAgent)
        assert agent.name == "explore"
        assert agent.schema.native is True

    def test_create_compaction_agent(self):
        """Specifically test 'compaction' agent."""
        agent = create_builtin_agent("compaction")
        assert isinstance(agent, BuiltInAgent)
        assert agent.name == "compaction"
        assert agent.schema.native is True

    def test_create_title_agent(self):
        """Specifically test 'title' agent."""
        agent = create_builtin_agent("title")
        assert isinstance(agent, BuiltInAgent)
        assert agent.name == "title"
        assert agent.schema.native is True

    def test_create_summary_agent(self):
        """Specifically test 'summary' agent."""
        agent = create_builtin_agent("summary")
        assert isinstance(agent, BuiltInAgent)
        assert agent.name == "summary"
        assert agent.schema.native is True

    def test_create_unknown_builtin_raises(self):
        """Unknown name raises AgentNotFoundError."""
        with pytest.raises(AgentNotFoundError) as exc_info:
            create_builtin_agent("nonexistent-agent")
        assert "nonexistent-agent" in str(exc_info.value)

    def test_create_builtin_agent_has_description(self):
        """Built-in agents have non-empty descriptions."""
        for name in self.BUILTIN_NAMES:
            agent = create_builtin_agent(name)
            assert agent.description, (
                "Agent '{}' should have a description".format(name)
            )

    def test_create_builtin_agent_has_prompt(self):
        """Built-in agents have non-empty prompts."""
        for name in self.BUILTIN_NAMES:
            agent = create_builtin_agent(name)
            assert agent.schema.prompt, (
                "Agent '{}' should have a prompt".format(name)
            )


# ---------------------------------------------------------------------------
# Agent Property Tests After Creation
# ---------------------------------------------------------------------------


class TestAgentProperties:
    """Tests for agent properties after creation via factory functions."""

    def test_builtin_agent_has_native_true(self, builtin_schema_dict):
        """Verify native=True for built-in agents."""
        schema = validate_agent_schema(builtin_schema_dict)
        agent = create_agent_from_schema(schema)
        assert agent.schema.native is True

    def test_custom_agent_has_native_false(self, custom_schema_dict):
        """Verify native=False for custom agents."""
        schema = validate_agent_schema(custom_schema_dict)
        agent = create_agent_from_schema(schema)
        assert agent.schema.native is False

    def test_agent_has_correct_mode(self, custom_schema_dict):
        """Verify mode matches schema."""
        schema = validate_agent_schema(custom_schema_dict)
        agent = create_agent_from_schema(schema)
        assert agent.mode == "primary"

    def test_agent_has_correct_permission(self, custom_schema_dict):
        """Verify permission matches schema."""
        schema = validate_agent_schema(custom_schema_dict)
        agent = create_agent_from_schema(schema)
        assert agent.permission == "restricted"

    def test_builtin_agent_permission(self):
        """Built-in agents have expected permission levels."""
        # Build agent should have full permission
        build_agent = create_builtin_agent("berserker")
        assert build_agent.permission in ("full", "restricted")

    def test_agent_schema_roundtrip(self, builtin_schema_dict):
        """Agent schema can be converted to dict and back."""
        schema = validate_agent_schema(builtin_schema_dict)
        agent = create_agent_from_schema(schema)
        schema_dict = agent.schema.to_dict()
        assert schema_dict["name"] == "test-builtin"
        assert schema_dict["mode"] == "subagent"

    def test_agent_repr(self, builtin_schema_dict):
        """Agent has a useful __repr__."""
        schema = validate_agent_schema(builtin_schema_dict)
        agent = create_agent_from_schema(schema)
        repr_str = repr(agent)
        assert "test-builtin" in repr_str
        assert "BuiltInAgent" in repr_str

    def test_agent_equality_same_schema(self, builtin_schema_dict):
        """Two agents with same schema are equal."""
        schema = validate_agent_schema(builtin_schema_dict)
        agent1 = create_agent_from_schema(schema)
        agent2 = create_agent_from_schema(schema)
        assert agent1 == agent2

    def test_agent_inequality_different_schema(
        self, builtin_schema_dict, custom_schema_dict
    ):
        """Agents with different schemas are not equal."""
        builtin_schema = validate_agent_schema(builtin_schema_dict)
        custom_schema = validate_agent_schema(custom_schema_dict)
        builtin = create_agent_from_schema(builtin_schema)
        custom = create_agent_from_schema(custom_schema)
        assert builtin != custom

    def test_builtin_agent_can_use_tool(self):
        """Built-in agents with full permission can use any tool."""
        agent = create_builtin_agent("berserker")
        assert agent.can_use_tool("bash") is True
        assert agent.can_use_tool("read") is True

    def test_custom_agent_restricted_permission(self, custom_schema_dict):
        """Custom agent with restricted permission checks tool list."""
        # Add a tools list to the restricted schema
        restricted_dict = dict(custom_schema_dict)
        restricted_dict["options"] = {"tools": ["read", "grep"]}
        schema = validate_agent_schema(restricted_dict)
        agent = create_agent_from_schema(schema)
        assert agent.can_use_tool("read") is True
        assert agent.can_use_tool("bash") is False
