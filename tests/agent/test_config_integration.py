"""
Integration tests for berserker agent config loading.

Covers end-to-end flows:
- Config file -> load -> register -> execute.
- Config agents do NOT override built-in agents with same name.
- discover_and_load() integration with AgentManager.

Python 3.8.10 compatible.
"""

import json
import os
import shutil
import tempfile

import pytest

from berserker.agent.manager import AgentManager
from berserker.agent.registry import registry as agent_registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear non-built-in agents before each test to avoid state leakage."""
    # Remove any non-built-in agents
    built_in_names = {"berserker", "plan", "general", "explore", "compaction", "title", "summary", "consultant", "critic", "executor"}
    for name in list(agent_registry._agents.keys()):
        if name not in built_in_names:
            try:
                agent_registry.unregister(name)
            except Exception:
                pass
    yield
    # Cleanup after test
    for name in list(agent_registry._agents.keys()):
        if name not in built_in_names:
            try:
                agent_registry.unregister(name)
            except Exception:
                pass


@pytest.fixture
def manager():
    """Create a fresh AgentManager instance."""
    return AgentManager()


@pytest.fixture
def temp_config_file():
    """Create a temporary JSON config file with agent definitions."""
    fd, path = tempfile.mkstemp(suffix=".json")
    config = {
        "agents": [
            {
                "name": "custom-agent",
                "description": "A custom agent from config",
                "mode": "subagent",
                "model": "gpt-4o",
                "prompt": "You are a custom agent loaded from config.",
            },
            {
                "name": "another-agent",
                "description": "Another custom agent",
                "mode": "primary",
                "model": "claude-sonnet-4-20250514",
                "prompt": "You are another agent.",
                "permission": "restricted",
            },
        ]
    }
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)
        yield path
    finally:
        os.unlink(path)


@pytest.fixture
def temp_agent_dir():
    """Create a temporary directory with agent files for discovery."""
    temp_dir = tempfile.mkdtemp()
    try:
        agent1 = {
            "name": "discovered-custom",
            "description": "Discovered custom agent",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a discovered custom agent.",
        }
        with open(os.path.join(temp_dir, "custom.json"), "w") as f:
            json.dump(agent1, f)

        yield temp_dir
    finally:
        shutil.rmtree(temp_dir)


# ---------------------------------------------------------------------------
# load_from_file Tests
# ---------------------------------------------------------------------------


class TestLoadFromFile:
    """Tests for AgentManager.load_from_file method."""

    def test_load_and_register_from_file(self, manager, temp_config_file):
        """Loading from file should register new agents."""
        initial_count = agent_registry.count()
        count = manager.load_from_file(temp_config_file)

        assert count == 2
        assert agent_registry.count() == initial_count + 2
        assert agent_registry.has("custom-agent")
        assert agent_registry.has("another-agent")

    def test_loaded_agents_are_custom(self, manager, temp_config_file):
        """Agents loaded from config should be non-native."""
        manager.load_from_file(temp_config_file)

        custom_agent = agent_registry.get("custom-agent")
        assert custom_agent.schema.native is False

    def test_builtin_agents_not_overridden(self, manager, temp_config_file):
        """Config agents should NOT override built-in agents."""
        # Create a config that tries to override 'build'
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            config = {
                "agents": [
                    {
                        "name": "berserker",  # Same as built-in
                        "description": "Attempted override",
                        "mode": "primary",
                        "model": "gpt-4o",
                        "prompt": "Override prompt.",
                    }
                ]
            }
            with os.fdopen(fd, "w") as f:
                json.dump(config, f)

            # Get original build agent
            original = agent_registry.get("berserker")
            original_prompt = original.system_prompt

            count = manager.load_from_file(path)
            # Should skip the duplicate
            assert count == 0

            # Original should be unchanged
            after = agent_registry.get("berserker")
            assert after.system_prompt == original_prompt
        finally:
            os.unlink(path)

    def test_load_invalid_file(self, manager):
        """Loading an invalid file should return 0 and not crash."""
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("{invalid}")
            count = manager.load_from_file(path)
            assert count == 0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# load_from_config_dict Tests
# ---------------------------------------------------------------------------


class TestLoadFromConfigDict:
    """Tests for AgentManager.load_from_config_dict method."""

    def test_load_and_register_from_dict(self, manager):
        """Loading from dict should register new agents."""
        initial_count = agent_registry.count()
        config = {
            "agents": [
                {
                    "name": "dict-agent",
                    "description": "Agent from dict",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "You are a dict agent.",
                }
            ]
        }

        count = manager.load_from_config_dict(config)
        assert count == 1
        assert agent_registry.count() == initial_count + 1
        assert agent_registry.has("dict-agent")

    def test_duplicate_names_skipped(self, manager):
        """Duplicate agent names should be skipped."""
        config = {
            "agents": [
                {
                    "name": "berserker",  # Built-in
                    "description": "Duplicate",
                    "mode": "primary",
                    "model": "gpt-4o",
                    "prompt": "Duplicate prompt.",
                }
            ]
        }

        count = manager.load_from_config_dict(config)
        assert count == 0


# ---------------------------------------------------------------------------
# discover_and_load Tests
# ---------------------------------------------------------------------------


class TestDiscoverAndLoad:
    """Tests for AgentManager.discover_and_load method."""

    def test_discover_and_load_from_custom_path(self, manager, temp_agent_dir, monkeypatch):
        """discover_and_load should find and register agents from paths."""
        import berserker.agent.discovery as discovery_module

        initial_count = agent_registry.count()

        original_paths = discovery_module.AGENT_DISCOVERY_PATHS
        try:
            discovery_module.AGENT_DISCOVERY_PATHS = [
                (lambda: temp_agent_dir, "")
            ]

            count = manager.discover_and_load()
            assert count == 1
            assert agent_registry.count() == initial_count + 1
            assert agent_registry.has("discovered-custom")
        finally:
            discovery_module.AGENT_DISCOVERY_PATHS = original_paths

    def test_discover_and_load_no_dirs(self, manager, monkeypatch):
        """discover_and_load with no agent dirs should return 0."""
        import berserker.agent.discovery as discovery_module

        original_paths = discovery_module.AGENT_DISCOVERY_PATHS
        try:
            discovery_module.AGENT_DISCOVERY_PATHS = [
                (lambda: "/nonexistent", "agents")
            ]

            count = manager.discover_and_load()
            assert count == 0
        finally:
            discovery_module.AGENT_DISCOVERY_PATHS = original_paths


# ---------------------------------------------------------------------------
# End-to-End Integration Tests
# ---------------------------------------------------------------------------


class TestConfigToRegistryFlow:
    """End-to-end tests: config file -> load -> register -> access."""

    def test_full_flow(self, manager, temp_config_file):
        """Complete flow: load from file, verify registration, access agent."""
        # Load from config
        count = manager.load_from_file(temp_config_file)
        assert count == 2

        # Verify agents are accessible
        agent = agent_registry.get("custom-agent")
        assert agent.name == "custom-agent"
        assert agent.description == "A custom agent from config"
        assert agent.mode == "subagent"
        assert agent.model == "gpt-4o"

        # Verify via manager.get_agent
        agent2 = manager.get_agent("custom-agent")
        assert agent2.name == "custom-agent"

    def test_multiple_loads_accumulate(self, manager):
        """Multiple config loads should accumulate agents."""
        config1 = {
            "agents": [
                {
                    "name": "agent-alpha",
                    "description": "Alpha",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "Alpha prompt.",
                }
            ]
        }
        config2 = {
            "agents": [
                {
                    "name": "agent-beta",
                    "description": "Beta",
                    "mode": "primary",
                    "model": "gpt-4o",
                    "prompt": "Beta prompt.",
                }
            ]
        }

        count1 = manager.load_from_config_dict(config1)
        count2 = manager.load_from_config_dict(config2)

        assert count1 == 1
        assert count2 == 1
        assert agent_registry.has("agent-alpha")
        assert agent_registry.has("agent-beta")

    def test_builtin_agents_preserved_after_config_load(self, manager, temp_config_file):
        """Built-in agents should still be accessible after config loading."""
        # Load config agents
        manager.load_from_file(temp_config_file)

        # Built-in agents should still work
        build_agent = agent_registry.get("berserker")
        assert build_agent.name == "berserker"
        assert build_agent.native is True

        plan_agent = agent_registry.get("plan")
        assert plan_agent.name == "plan"
        assert plan_agent.native is True

    def test_config_agent_accessible_via_manager(self, manager, temp_config_file):
        """Config-loaded agents should be accessible via manager methods."""
        manager.load_from_file(temp_config_file)

        # Via get_agent (BaseAgent)
        base = manager.get_agent("custom-agent")
        assert base.name == "custom-agent"

        # Via list (AgentInfo)
        all_agents = manager.list()
        names = [a.name for a in all_agents]
        assert "custom-agent" in names
