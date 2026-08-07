"""
Tests for berserker.agent.base module.

Covers:
- BaseAgent abstract class (cannot be instantiated directly).
- BuiltInAgent and CustomAgent concrete classes.
- Property accessors (name, mode, model, description, permission, schema).
- can_use_tool() permission checks.
- get_system_prompt() with and without injections.
- __repr__ and __eq__ methods.
"""

import pytest

from berserker.agent.base import BaseAgent, BuiltInAgent, CustomAgent
from berserker.agent.schema import AgentSchema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_schema():
    """Return a minimal valid AgentSchema."""
    return AgentSchema(
        name="test-agent",
        description="A test agent",
        mode="subagent",
        model="gpt-4o",
        prompt="You are a test agent.",
    )


@pytest.fixture
def full_permission_schema():
    """Return a schema with full permission."""
    return AgentSchema(
        name="full-agent",
        description="Full permission agent",
        mode="primary",
        model="gpt-4o",
        prompt="You are a full agent.",
        permission="full",
    )


@pytest.fixture
def restricted_schema():
    """Return a schema with restricted permission and tools."""
    return AgentSchema(
        name="restricted-agent",
        description="Restricted permission agent",
        mode="subagent",
        model="gpt-4o",
        prompt="You are a restricted agent.",
        permission="restricted",
        options={"tools": ["read", "write", "grep"]},
    )


# ---------------------------------------------------------------------------
# BaseAgent Abstract Class Tests
# ---------------------------------------------------------------------------


class TestBaseAgentAbstract:
    """Tests for BaseAgent abstract behavior."""

    def test_cannot_instantiate_baseagent(self, minimal_schema):
        """BaseAgent should not be instantiable directly."""
        with pytest.raises(TypeError):
            BaseAgent(minimal_schema)

    def test_subclass_must_implement_execute(self, minimal_schema):
        """A subclass without execute() should raise TypeError."""
        class IncompleteAgent(BaseAgent):
            pass

        with pytest.raises(TypeError):
            IncompleteAgent(minimal_schema)


# ---------------------------------------------------------------------------
# Concrete Agent Property Tests
# ---------------------------------------------------------------------------


class TestBuiltInAgentProperties:
    """Tests for BuiltInAgent property accessors."""

    def test_name(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        assert agent.name == "test-agent"

    def test_mode(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        assert agent.mode == "subagent"

    def test_model(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        assert agent.model == "gpt-4o"

    def test_description(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        assert agent.description == "A test agent"

    def test_permission(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        assert agent.permission == "full"

    def test_schema_reference(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        assert agent.schema is minimal_schema

    def test_native_flag_set(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        assert agent.schema.native is True


class TestCustomAgentProperties:
    """Tests for CustomAgent property accessors."""

    def test_native_flag_set(self, minimal_schema):
        agent = CustomAgent(minimal_schema)
        assert agent.schema.native is False


# ---------------------------------------------------------------------------
# Permission Tests
# ---------------------------------------------------------------------------


class TestCanUseTool:
    """Tests for can_use_tool() permission checks."""

    def test_full_permission_allows_all(self, full_permission_schema):
        agent = BuiltInAgent(full_permission_schema)
        assert agent.can_use_tool("read") is True
        assert agent.can_use_tool("write") is True
        assert agent.can_use_tool("bash") is True
        assert agent.can_use_tool("anything") is True

    def test_restricted_permission_allows_listed(self, restricted_schema):
        agent = CustomAgent(restricted_schema)
        assert agent.can_use_tool("read") is True
        assert agent.can_use_tool("write") is True
        assert agent.can_use_tool("grep") is True

    def test_restricted_permission_denies_unlisted(self, restricted_schema):
        agent = CustomAgent(restricted_schema)
        assert agent.can_use_tool("bash") is False
        assert agent.can_use_tool("edit") is False

    def test_restricted_no_tools_denies_all(self, minimal_schema):
        """Restricted agent with no tools in options denies everything."""
        schema = AgentSchema(
            name="no-tools",
            description="No tools",
            mode="subagent",
            model="gpt-4o",
            prompt="Test",
            permission="restricted",
        )
        agent = CustomAgent(schema)
        assert agent.can_use_tool("read") is False


# ---------------------------------------------------------------------------
# System Prompt Tests
# ---------------------------------------------------------------------------


class TestGetSystemPrompt:
    """Tests for get_system_prompt() with injections."""

    def test_no_injections(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        prompt = agent.get_system_prompt()
        assert prompt == "You are a test agent."

    def test_with_injections(self):
        schema = AgentSchema(
            name="template-agent",
            description="Template agent",
            mode="subagent",
            model="gpt-4o",
            prompt="Hello {name}, you are a {role} agent.",
        )
        agent = BuiltInAgent(schema)
        prompt = agent.get_system_prompt({"name": "Alice", "role": "testing"})
        assert prompt == "Hello Alice, you are a testing agent."

    def test_partial_injections(self):
        """Missing injection keys should log warning but not crash."""
        schema = AgentSchema(
            name="partial-agent",
            description="Partial agent",
            mode="subagent",
            model="gpt-4o",
            prompt="Hello {name}, you are a {role} agent.",
        )
        agent = BuiltInAgent(schema)
        # Missing 'role' key — should fall back gracefully
        prompt = agent.get_system_prompt({"name": "Bob"})
        # Original prompt returned (injection failed)
        assert prompt == "Hello {name}, you are a {role} agent."

    def test_no_injections_dict(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        prompt = agent.get_system_prompt(None)
        assert prompt == "You are a test agent."


# ---------------------------------------------------------------------------
# Execute Method Tests
# ---------------------------------------------------------------------------


class TestExecute:
    """Tests for execute() placeholder behavior."""

    def test_builtin_execute_returns_delegation_pending(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        result = agent.execute([], "test-session", None)
        assert result["_delegation_pending"] is True
        assert result["_agent_name"] == "test-agent"

    def test_custom_execute_returns_delegation_pending(self, minimal_schema):
        agent = CustomAgent(minimal_schema)
        result = agent.execute([], "test-session", None)
        assert result["_delegation_pending"] is True
        assert result["_agent_name"] == "test-agent"


# ---------------------------------------------------------------------------
# Repr and Eq Tests
# ---------------------------------------------------------------------------


class TestReprAndEq:
    """Tests for __repr__ and __eq__ methods."""

    def test_repr_builtin(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        repr_str = repr(agent)
        assert "BuiltInAgent" in repr_str
        assert "test-agent" in repr_str
        assert "subagent" in repr_str
        assert "gpt-4o" in repr_str

    def test_repr_custom(self, minimal_schema):
        agent = CustomAgent(minimal_schema)
        repr_str = repr(agent)
        assert "CustomAgent" in repr_str

    def test_eq_same_schema(self, minimal_schema):
        agent1 = BuiltInAgent(minimal_schema)
        agent2 = BuiltInAgent(minimal_schema)
        assert agent1 == agent2

    def test_eq_different_schema(self, minimal_schema):
        schema2 = AgentSchema(
            name="other-agent",
            description="Other",
            mode="primary",
            model="gpt-4o",
            prompt="Other prompt",
        )
        agent1 = BuiltInAgent(minimal_schema)
        agent2 = BuiltInAgent(schema2)
        assert agent1 != agent2

    def test_eq_non_agent(self, minimal_schema):
        agent = BuiltInAgent(minimal_schema)
        assert agent != "not an agent"
        assert agent != 42
        assert agent != None  # noqa: E711

    def test_eq_builtin_vs_custom_same_schema(self, minimal_schema):
        """BuiltInAgent and CustomAgent with same schema should be equal."""
        builtin = BuiltInAgent(minimal_schema)
        custom = CustomAgent(minimal_schema)
        assert builtin == custom


# ---------------------------------------------------------------------------
# Constructor Validation Tests
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    """Tests for constructor type validation."""

    def test_non_schema_raises(self):
        with pytest.raises(TypeError) as exc_info:
            BuiltInAgent({"name": "test"})
        assert "Expected AgentSchema" in str(exc_info.value)

    def test_none_raises(self):
        with pytest.raises(TypeError):
            CustomAgent(None)
