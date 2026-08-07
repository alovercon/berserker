"""
Integration tests for the agent framework.

Full integration test covering:
- AgentManager lifecycle (create, get, list, register, unregister)
- Built-in agents registration
- Custom agent registration and access
- Mock agent execution
- Message router send/receive
- Orchestrator instantiation
- FrontendAdapter instantiation with mock dependencies
- Permission ruleset creation and checking
- Prompt loader discovery
- Config loading from dict

Python 3.8.10 compatible: uses type comments, unittest.mock, pytest.
"""

from __future__ import annotations

import os
import tempfile
import warnings
from unittest.mock import MagicMock, patch

import pytest

from berserker.agent.manager import AgentManager, AgentInfo
from berserker.agent.registry import AgentRegistry, registry as agent_registry
from berserker.agent.base import BaseAgent, BuiltInAgent, CustomAgent
from berserker.agent.schema import AgentSchema, validate_agent_schema
from berserker.agent.factory import create_agent_from_schema, create_builtin_agent
from berserker.agent.messaging import MessageRouter, AgentMessage, message_router
from berserker.agent.orchestrator import Orchestrator, OrchestrationTask, get_orchestrator
from berserker.agent.adapter import FrontendAdapter
from berserker.agent.prompt_loader import PromptLoader
from berserker.agent.config import load_agents_from_dict
from berserker.permission import (
    PermissionRuleset,
    AgentPermissionRule,
    ALLOWED,
    DENIED,
    NEEDS_ASK,
)
from berserker.provider.base import ChatMessage, ChatResponse


class TestAgentManagerIntegration(object):
    """Integration tests for AgentManager lifecycle."""

    def test_create_manager_instance(self):
        """Should be able to create an AgentManager instance."""
        manager = AgentManager()
        assert manager is not None

    def test_builtin_agents_registered(self):
        """Built-in agents should be registered on initialization."""
        manager = AgentManager()
        agents = manager.list()
        names = [a.name for a in agents]

        expected = ["berserker", "plan", "general", "explore", "compaction", "title", "summary", "consultant", "critic", "executor"]
        for name in expected:
            assert name in names

    def test_get_builtin_agent(self):
        """Should be able to get built-in agents by name."""
        manager = AgentManager()
        build = manager.get("berserker")
        assert build.name == "berserker"
        assert build.mode == "primary"
        assert "read" in build.tools

    def test_list_primary_agents(self):
        """list_primary should return only primary agents."""
        manager = AgentManager()
        primary = manager.list_primary()
        assert len(primary) >= 2
        for a in primary:
            assert a.mode == "primary"

    def test_list_subagents(self):
        """list_subagents should return only subagent agents."""
        manager = AgentManager()
        subagents = manager.list_subagents()
        assert len(subagents) >= 2
        for a in subagents:
            assert a.mode == "subagent"

    def test_register_custom_agent_via_baseagent(self):
        """Should be able to register a custom agent via BaseAgent."""
        manager = AgentManager()
        schema = AgentSchema(
            name="integration-test-agent",
            description="Integration test agent",
            mode="subagent",
            model="gpt-4o",
            prompt="You are an integration test agent.",
            permission="full",
            options={"tools": ["read", "write"]},
        )
        agent = create_agent_from_schema(schema)
        manager.register(agent)

        # Verify via old API (AgentInfo)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            info = manager.get("integration-test-agent")
        assert info.name == "integration-test-agent"

        # Verify via new API (BaseAgent)
        base = manager.get_agent("integration-test-agent")
        assert isinstance(base, BaseAgent)
        assert base.name == "integration-test-agent"

    def test_unregister_removes_agent(self):
        """Unregister should remove agent from both storages."""
        manager = AgentManager()
        schema = AgentSchema(
            name="temp-unregister",
            description="Temp",
            mode="subagent",
            model="gpt-4o",
            prompt="Temp.",
            options={"tools": ["read"]},
        )
        agent = create_agent_from_schema(schema)
        manager.register(agent)

        manager.unregister("temp-unregister")

        with pytest.raises(KeyError):
            manager.get("temp-unregister")

    def test_get_agent_returns_baseagent(self):
        """get_agent should return BaseAgent, not AgentInfo."""
        manager = AgentManager()
        agent = manager.get_agent("berserker")
        assert isinstance(agent, BaseAgent)
        assert isinstance(agent, BuiltInAgent)


class TestMessageRouterIntegration(object):
    """Integration tests for message routing."""

    def test_send_and_receive_message(self):
        """Should be able to send and receive messages between agents."""
        router = MessageRouter()
        router.register_agent("sender")
        router.register_agent("receiver")

        msg = AgentMessage(
            from_agent="sender",
            to_agent="receiver",
            session_id="test-session",
            content="Hello from sender",
        )
        router.send(msg)

        received = router.receive("receiver")
        assert len(received) == 1
        assert received[0].content == "Hello from sender"
        assert received[0].from_agent == "sender"

    def test_message_status_updated(self):
        """Message status should be updated through the lifecycle."""
        router = MessageRouter()
        router.register_agent("agent-a")

        msg = AgentMessage(
            from_agent="system",
            to_agent="agent-a",
            session_id="test",
            content="Test message",
        )
        assert msg.status == "pending"

        router.send(msg)
        assert msg.status == "delivered"

        router.receive("agent-a")
        assert msg.status == "read"

    def test_broadcast_to_multiple_agents(self):
        """Broadcast should deliver to all registered agents."""
        router = MessageRouter()
        for name in ["a", "b", "c"]:
            router.register_agent(name)

        msg = AgentMessage(
            from_agent="a",
            to_agent="b",  # to_agent is ignored for broadcast
            session_id="test",
            content="Broadcast message",
        )
        count = router.broadcast(msg, exclude=["c"])
        assert count == 1  # Only "b" receives (exclude "c", skip sender "a")


class TestOrchestratorIntegration(object):
    """Integration tests for orchestrator."""

    def test_orchestrator_instantiation(self):
        """Should be able to create an Orchestrator instance."""
        mock_manager = MagicMock()
        mock_registry = MagicMock()
        orchestrator = Orchestrator(mock_manager, mock_registry)
        assert orchestrator is not None

    def test_orchestrator_empty_parallel(self):
        """Parallel execution with no tasks should return empty list."""
        mock_manager = MagicMock()
        mock_registry = MagicMock()
        orchestrator = Orchestrator(mock_manager, mock_registry)
        results = orchestrator.execute_parallel([])
        assert results == []

    def test_orchestrator_empty_sequential(self):
        """Sequential execution with no tasks should return empty list."""
        mock_manager = MagicMock()
        mock_registry = MagicMock()
        orchestrator = Orchestrator(mock_manager, mock_registry)
        results = orchestrator.execute_sequential([])
        assert results == []

    def test_orchestration_task_creation(self):
        """Should be able to create OrchestrationTask instances."""
        task = OrchestrationTask(
            task_id="task-1",
            agent_name="berserker",
            prompt="Do something",
            session_id="session-1",
        )
        assert task.task_id == "task-1"
        assert task.agent_name == "berserker"
        assert task.status == "pending"

    def test_orchestration_task_cancel(self):
        """Should be able to cancel a task."""
        task = OrchestrationTask(
            task_id="task-2",
            agent_name="berserker",
            prompt="Do something",
            session_id="session-1",
        )
        task.cancel()
        assert task.is_cancelled()
        assert task.status == "cancelled"


class TestFrontendAdapterIntegration(object):
    """Integration tests for FrontendAdapter."""

    def test_frontend_adapter_instantiation(self):
        """Should be able to create a FrontendAdapter with mock dependencies."""
        mock_manager = MagicMock()
        mock_registry = MagicMock()
        mock_display = MagicMock()

        # Create a concrete subclass for testing
        class TestAdapter(FrontendAdapter):
            def execute_agent(self, agent_name, messages, session_id,
                              on_tool_call=None, on_permission_ask=None, extra=None):
                return {"content": "test", "usage": None}

            def cancel_execution(self):
                pass

            def get_agent_status(self, session_id=None):
                return {"is_executing": False, "current_agent": None, "session_id": session_id}

        adapter = TestAdapter(mock_manager, mock_registry, mock_display)
        assert adapter is not None
        assert adapter._display == mock_display

    def test_frontend_adapter_event_subscription(self):
        """Event subscription should work correctly."""
        mock_manager = MagicMock()
        mock_registry = MagicMock()

        class TestAdapter(FrontendAdapter):
            def execute_agent(self, agent_name, messages, session_id,
                              on_tool_call=None, on_permission_ask=None, extra=None):
                return {"content": "test", "usage": None}

            def cancel_execution(self):
                pass

            def get_agent_status(self, session_id=None):
                return {"is_executing": False}

        adapter = TestAdapter(mock_manager, mock_registry)

        events_received = []

        def callback(event):
            events_received.append(event)

        adapter.subscribe_events(callback)
        adapter._publish_event("test-event")

        assert len(events_received) == 1
        assert events_received[0] == "test-event"

    def test_frontend_adapter_list_agents(self):
        """list_agents should delegate to agent_manager."""
        mock_manager = MagicMock()
        mock_manager.list.return_value = ["agent1", "agent2"]
        mock_registry = MagicMock()

        class TestAdapter(FrontendAdapter):
            def execute_agent(self, agent_name, messages, session_id,
                              on_tool_call=None, on_permission_ask=None, extra=None):
                return {"content": "test", "usage": None}

            def cancel_execution(self):
                pass

            def get_agent_status(self, session_id=None):
                return {"is_executing": False}

        adapter = TestAdapter(mock_manager, mock_registry)
        agents = adapter.list_agents()
        assert agents == ["agent1", "agent2"]


class TestPermissionRulesetIntegration(object):
    """Integration tests for permission ruleset."""

    def test_create_ruleset_with_rules(self):
        """Should be able to create a PermissionRuleset with custom rules."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny"))

        assert len(ruleset) == 2

    def test_ruleset_check_allowed(self):
        """Ruleset should return ALLOWED for matching allow rule."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))

        result = ruleset.check("read")
        assert result == ALLOWED

    def test_ruleset_check_denied(self):
        """Ruleset should return DENIED for matching deny rule."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny"))

        result = ruleset.check("write")
        assert result == DENIED

    def test_ruleset_check_needs_ask(self):
        """Ruleset should return NEEDS_ASK when no rule matches."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))

        result = ruleset.check("unknown-tool")
        assert result == NEEDS_ASK

    def test_ruleset_merge(self):
        """Ruleset merge should combine rules from both rulesets."""
        ruleset1 = PermissionRuleset()
        ruleset1.add_rule(AgentPermissionRule(tool="read", action="allow"))

        ruleset2 = PermissionRuleset()
        ruleset2.add_rule(AgentPermissionRule(tool="write", action="deny"))

        merged = ruleset1.merge(ruleset2)
        assert len(merged) == 2

    def test_ruleset_agent_specific_rule(self):
        """Agent-specific rules should only match that agent."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(
            tool="write", action="deny", agent="berserker"
        ))
        ruleset.add_rule(AgentPermissionRule(
            tool="write", action="allow"
        ))

        # build agent should be denied
        assert ruleset.check("write", agent="berserker") == DENIED
        # other agents should be allowed (global rule)
        assert ruleset.check("write", agent="plan") == ALLOWED


class TestPromptLoaderIntegration(object):
    """Integration tests for prompt loader."""

    def test_prompt_loader_instantiation(self):
        """Should be able to create a PromptLoader instance."""
        loader = PromptLoader()
        assert loader is not None
        assert loader.prompt_dir is not None

    def test_discover_prompts(self):
        """discover_prompts should return a list of prompt names."""
        loader = PromptLoader()
        prompts = loader.discover_prompts()
        assert isinstance(prompts, list)
        # Should find at least some prompts if the directory exists
        if os.path.isdir(loader.prompt_dir):
            assert len(prompts) > 0


class TestConfigLoadingIntegration(object):
    """Integration tests for config loading."""

    def test_load_agents_from_dict(self):
        """Should be able to load agents from a dictionary."""
        config = {
            "agents": [
                {
                    "name": "config-agent",
                    "description": "Agent from config",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "You are a config agent.",
                }
            ]
        }
        schemas = load_agents_from_dict(config)
        assert len(schemas) == 1
        assert schemas[0].name == "config-agent"

    def test_load_agents_from_dict_empty_list(self):
        """Should handle empty agents list."""
        config = {"agents": []}
        schemas = load_agents_from_dict(config)
        assert len(schemas) == 0

    def test_load_agents_from_dict_invalid_config(self):
        """Should raise ValueError for invalid config."""
        with pytest.raises(ValueError):
            load_agents_from_dict({"no_agents_key": []})

    def test_load_agents_from_dict_skips_invalid(self):
        """Should skip invalid agent entries with warning."""
        config = {
            "agents": [
                {"name": "valid", "description": "Valid", "mode": "subagent",
                 "model": "gpt-4o", "prompt": "Valid prompt."},
                {"name": ""},  # Invalid - empty name
            ]
        }
        schemas = load_agents_from_dict(config)
        assert len(schemas) == 1
        assert schemas[0].name == "valid"
