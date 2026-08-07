"""
Backward compatibility tests for AgentManager after Phase 2 refactoring.

Verifies that the existing AgentManager API continues to work correctly
after delegating to AgentRegistry internally.

Python 3.8.10 compatible: uses type comments, typing module imports.
"""

from __future__ import annotations

import pytest

from berserker.agent import (
    AgentInfo,
    AgentManager,
    agent_manager,
    BUILT_IN_AGENTS,
    AgentRegistry,
    registry,
    create_agent_from_schema,
    create_builtin_agent,
    BaseAgent,
    BuiltInAgent,
    CustomAgent,
    AgentSchema,
    AgentNotFoundError,
    AgentRegistrationError,
)


class TestAgentManagerGet(object):
    """Test AgentManager.get() returns AgentInfo as before."""

    def test_get_build_returns_agent_info(self):
        manager = AgentManager()
        agent = manager.get("berserker")
        assert isinstance(agent, AgentInfo)
        assert agent.name == "berserker"
        assert agent.mode == "primary"

    def test_get_plan_returns_agent_info(self):
        manager = AgentManager()
        agent = manager.get("plan")
        assert isinstance(agent, AgentInfo)
        assert agent.name == "plan"
        assert agent.mode == "primary"

    def test_get_general_returns_agent_info(self):
        manager = AgentManager()
        agent = manager.get("general")
        assert isinstance(agent, AgentInfo)
        assert agent.mode == "subagent"

    def test_get_not_found_raises_keyerror(self):
        manager = AgentManager()
        with pytest.raises(KeyError):
            manager.get("nonexistent")

    def test_get_has_correct_tools(self):
        manager = AgentManager()
        build = manager.get("berserker")
        assert "read" in build.tools
        assert "write" in build.tools
        assert "bash" in build.tools

    def test_get_plan_has_readonly_tools(self):
        manager = AgentManager()
        plan = manager.get("plan")
        assert "read" in plan.tools
        assert "write" not in plan.tools
        assert "bash" not in plan.tools


class TestAgentManagerList(object):
    """Test AgentManager.list() methods return AgentInfo lists."""

    def test_list_returns_all_builtins(self):
        manager = AgentManager()
        agents = manager.list()
        names = [a.name for a in agents]
        for name in BUILT_IN_AGENTS:
            assert name in names

    def test_list_primary(self):
        manager = AgentManager()
        primary = manager.list_primary()
        assert len(primary) >= 2  # build, plan
        for a in primary:
            assert a.mode == "primary"

    def test_list_subagents(self):
        manager = AgentManager()
        subagents = manager.list_subagents()
        names = [a.name for a in subagents]
        assert "general" in names
        assert "explore" in names
        for a in subagents:
            assert a.mode == "subagent"

    def test_list_returns_copy(self):
        manager = AgentManager()
        list1 = manager.list()
        list2 = manager.list()
        assert list1 is not list2


class TestAgentManagerRegisterUnregister(object):
    """Test new register/unregister methods on AgentManager."""

    def test_register_custom_agent(self):
        manager = AgentManager()
        schema = AgentSchema(
            name="test-agent",
            description="Test agent",
            mode="subagent",
            model="gpt-4o",
            prompt="You are a test agent.",
            permission="full",
            options={"tools": ["read", "write"]},
        )
        agent = create_agent_from_schema(schema)
        manager.register(agent)

        # Verify via old API
        info = manager.get("test-agent")
        assert isinstance(info, AgentInfo)
        assert info.name == "test-agent"

        # Verify via new API
        base = manager.get_agent("test-agent")
        assert isinstance(base, BaseAgent)
        assert base.name == "test-agent"

    def test_register_non_baseagent_raises(self):
        manager = AgentManager()
        with pytest.raises(TypeError):
            manager.register("not an agent")

    def test_unregister_removes_from_both(self):
        manager = AgentManager()
        schema = AgentSchema(
            name="temp-agent",
            description="Temporary agent",
            mode="subagent",
            model="gpt-4o",
            prompt="Temp.",
            options={"tools": ["read"]},
        )
        agent = create_agent_from_schema(schema)
        manager.register(agent)

        # Unregister
        manager.unregister("temp-agent")

        # Verify removed from old API
        with pytest.raises(KeyError):
            manager.get("temp-agent")

        # Verify removed from new API
        with pytest.raises(AgentNotFoundError):
            manager.get_agent("temp-agent")

    def test_unregister_not_found_raises(self):
        manager = AgentManager()
        with pytest.raises(AgentNotFoundError):
            manager.unregister("nonexistent")


class TestAgentManagerGetAgent(object):
    """Test new get_agent() method returns BaseAgent."""

    def test_get_agent_returns_baseagent(self):
        manager = AgentManager()
        agent = manager.get_agent("berserker")
        assert isinstance(agent, BaseAgent)
        assert isinstance(agent, BuiltInAgent)

    def test_get_agent_not_found_raises(self):
        manager = AgentManager()
        with pytest.raises(AgentNotFoundError):
            manager.get_agent("nonexistent")

    def test_get_agent_has_tools_property(self):
        manager = AgentManager()
        agent = manager.get_agent("berserker")
        assert "read" in agent.tools
        assert "write" in agent.tools

    def test_get_agent_has_system_prompt(self):
        manager = AgentManager()
        agent = manager.get_agent("berserker")
        assert "software engineer" in agent.system_prompt

    def test_get_agent_has_max_tool_iterations(self):
        manager = AgentManager()
        agent = manager.get_agent("berserker")
        assert agent.max_tool_iterations == 100000


class TestGlobalRegistryIntegration(object):
    """Test that AgentManager and global registry are in sync."""

    def test_builtin_agents_in_global_registry(self):
        # Create a fresh manager to ensure registry is populated
        manager = AgentManager()
        for name in BUILT_IN_AGENTS:
            assert registry.has(name)

    def test_manager_register_updates_registry(self):
        manager = AgentManager()
        schema = AgentSchema(
            name="sync-test",
            description="Sync test",
            mode="subagent",
            model="gpt-4o",
            prompt="Sync.",
            options={"tools": ["read"]},
        )
        agent = create_agent_from_schema(schema)
        manager.register(agent)

        assert registry.has("sync-test")
        manager.unregister("sync-test")
        assert not registry.has("sync-test")


class TestSingletonAgentManager(object):
    """Test the global agent_manager singleton still works."""

    def test_singleton_has_builtins(self):
        for name in BUILT_IN_AGENTS:
            agent = agent_manager.get(name)
            assert isinstance(agent, AgentInfo)

    def test_singleton_list_works(self):
        agents = agent_manager.list()
        assert len(agents) >= 7

    def test_singleton_list_primary(self):
        primary = agent_manager.list_primary()
        assert len(primary) >= 2


class TestBaseAgentNewProperties(object):
    """Test new properties added to BaseAgent for execute() compatibility."""

    def test_tools_property(self):
        agent = create_builtin_agent("berserker")
        tools = agent.tools
        assert isinstance(tools, list)
        assert len(tools) > 0
        assert "read" in tools

    def test_system_prompt_property(self):
        agent = create_builtin_agent("plan")
        prompt = agent.system_prompt
        assert isinstance(prompt, str)
        assert "READ-ONLY" in prompt

    def test_max_tool_iterations_property(self):
        agent = create_builtin_agent("general")
        assert agent.max_tool_iterations == 100000

    def test_custom_agent_tools_from_schema(self):
        schema = AgentSchema(
            name="custom-tools-test",
            description="Test",
            mode="subagent",
            model="gpt-4o",
            prompt="Test.",
            options={"tools": ["read", "ls"], "max_tool_iterations": 50},
        )
        agent = create_agent_from_schema(schema)
        assert agent.tools == ["read", "ls"]
        assert agent.max_tool_iterations == 50
