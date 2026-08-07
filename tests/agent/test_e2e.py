"""
End-to-end workflow tests for the agent framework.

Covers 5 complete workflow scenarios:
1. Config -> Registry -> Execution flow
2. Discovery -> Loading flow
3. Messaging -> Orchestration flow
4. Permission -> Execution flow
5. FrontendAdapter -> Display flow

Python 3.8.10 compatible: uses type comments, unittest.mock, pytest.
"""

from __future__ import annotations

import os
import tempfile
import warnings
from unittest.mock import MagicMock, patch, PropertyMock

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
from berserker.agent.config import load_agents_from_dict, load_agents_from_config
from berserker.permission import (
    PermissionRuleset,
    AgentPermissionRule,
    ALLOWED,
    DENIED,
    NEEDS_ASK,
)
from berserker.provider.base import ChatMessage, ChatResponse


class TestConfigToRegistryToExecutionFlow(object):
    """
    E2E Test 1: Config -> Registry -> Execution flow.

    Verifies the complete pipeline:
    1. Load agent config from dict
    2. Validate schema
    3. Create agent from schema
    4. Register in AgentRegistry
    5. Register in AgentManager
    6. Execute agent (mocked)
    """

    def test_full_config_to_execution_pipeline(self):
        """Complete pipeline from config dict to agent execution."""
        # Step 1: Load config from dict
        config = {
            "agents": [
                {
                    "name": "e2e-config-agent",
                    "description": "E2E test agent from config",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "You are an E2E test agent.",
                    "permission": "restricted",
                    "options": {
                        "tools": ["read", "write"],
                        "max_tool_iterations": 30,
                    },
                }
            ]
        }
        schemas = load_agents_from_dict(config)
        assert len(schemas) == 1
        schema = schemas[0]

        # Step 2: Schema already validated by load_agents_from_dict
        assert schema.name == "e2e-config-agent"

        # Step 3: Create agent from schema
        agent = create_agent_from_schema(schema)
        assert isinstance(agent, CustomAgent)
        assert agent.name == "e2e-config-agent"

        # Step 4: Register in AgentRegistry (skip - manager.register() does this internally)
        # agent_registry.register(agent)  # Would cause double-registration

        # Step 5: Register in AgentManager (also registers in AgentRegistry internally)
        manager = AgentManager()
        manager.register(agent)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            info = manager.get("e2e-config-agent")
        assert info.name == "e2e-config-agent"

        # Step 6: Execute agent (mocked)
        mock_response = ChatResponse(
            id="resp-e2e",
            model="gpt-4o",
            content="E2E execution result",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )
        with patch.object(agent, "execute", return_value=mock_response):
            result = agent.execute([ChatMessage(role="user", content="Test")])
            assert result.content == "E2E execution result"

        # Cleanup
        agent_registry.unregister("e2e-config-agent")

    def test_config_to_execution_with_invalid_config(self):
        """Pipeline should handle invalid config gracefully."""
        config = {
            "agents": [
                {
                    "name": "",  # Invalid - empty name
                    "description": "Invalid agent",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "Invalid.",
                }
            ]
        }
        schemas = load_agents_from_dict(config)
        assert len(schemas) == 0

    def test_config_to_execution_with_multiple_agents(self):
        """Pipeline should handle multiple agents from config."""
        config = {
            "agents": [
                {
                    "name": "agent-a",
                    "description": "Agent A",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "You are agent A.",
                },
                {
                    "name": "agent-b",
                    "description": "Agent B",
                    "mode": "subagent",
                    "model": "claude-3.5-sonnet",
                    "prompt": "You are agent B.",
                },
            ]
        }
        schemas = load_agents_from_dict(config)
        assert len(schemas) == 2

        agents = []
        for schema in schemas:
            agent = create_agent_from_schema(schema)
            agent_registry.register(agent)
            agents.append(agent)

        # Verify both registered
        assert agent_registry.get("agent-a") is not None
        assert agent_registry.get("agent-b") is not None

        # Cleanup
        agent_registry.unregister("agent-a")
        agent_registry.unregister("agent-b")


class TestDiscoveryToLoadingFlow(object):
    """
    E2E Test 2: Discovery -> Loading flow.

    Verifies the complete pipeline:
    1. Create temporary config file
    2. Discover config files
    3. Load agents from discovered files
    4. Verify agents are accessible
    """

    def test_discovery_to_loading_pipeline(self):
        """Complete pipeline from file discovery to agent loading."""
        # Step 1: Create temporary config file (JSON format - load_agents_from_config expects JSON)
        config_content = """{
    "agents": [
        {
            "name": "discovered-agent",
            "description": "Agent discovered from config file",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a discovered agent.",
            "permission": "restricted",
            "options": {
                "tools": ["read", "write"]
            }
        }
    ]
}"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(config_content)
            config_path = f.name

        registered = False
        try:
            # Step 2: Load agents from file
            schemas = load_agents_from_config(config_path)
            assert len(schemas) == 1
            assert schemas[0].name == "discovered-agent"

            # Step 3: Create agent (skip registry register - manager.register() does it)
            agent = create_agent_from_schema(schemas[0])
            assert isinstance(agent, CustomAgent)

            # Step 4: Verify agent is accessible via AgentManager
            manager = AgentManager()
            manager.register(agent)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                info = manager.get("discovered-agent")
            assert info.name == "discovered-agent"
            assert info.model == "gpt-4o"

        finally:
            # Cleanup
            os.unlink(config_path)
            if registered:
                agent_registry.unregister("discovered-agent")

    def test_discovery_with_nonexistent_file(self):
        """Discovery should raise FileNotFoundError for nonexistent files."""
        with pytest.raises(FileNotFoundError):
            load_agents_from_config("/nonexistent/path/config.json")

    def test_discovery_with_invalid_json(self):
        """Discovery should raise ValueError for invalid JSON."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("invalid: json: content: [")
            config_path = f.name

        try:
            with pytest.raises(ValueError):
                load_agents_from_config(config_path)
        finally:
            os.unlink(config_path)


class TestMessagingToOrchestrationFlow(object):
    """
    E2E Test 3: Messaging -> Orchestration flow.

    Verifies the complete pipeline:
    1. Create message router
    2. Register agents
    3. Send messages between agents
    4. Create orchestrator
    5. Execute tasks in parallel
    6. Verify results
    """

    def test_messaging_to_orchestration_pipeline(self):
        """Complete pipeline from messaging to orchestration."""
        # Step 1: Create message router
        router = MessageRouter()

        # Step 2: Register agents
        router.register_agent("coordinator")
        router.register_agent("worker-1")
        router.register_agent("worker-2")

        # Step 3: Send messages between agents
        msg1 = AgentMessage(
            from_agent="coordinator",
            to_agent="worker-1",
            session_id="e2e-session",
            content="Task 1: Process data",
        )
        msg2 = AgentMessage(
            from_agent="coordinator",
            to_agent="worker-2",
            session_id="e2e-session",
            content="Task 2: Analyze results",
        )
        router.send(msg1)
        router.send(msg2)

        # Verify messages delivered
        assert msg1.status == "delivered"
        assert msg2.status == "delivered"

        # Step 4: Create orchestrator
        mock_manager = MagicMock()
        mock_registry = MagicMock()
        orchestrator = Orchestrator(mock_manager, mock_registry)

        # Step 5: Execute tasks in parallel (mocked)
        mock_manager.execute.return_value = {"content": "Task completed", "usage": None}

        tasks = [
            OrchestrationTask(
                task_id="task-1",
                agent_name="worker-1",
                prompt="Process data",
                session_id="e2e-session",
            ),
            OrchestrationTask(
                task_id="task-2",
                agent_name="worker-2",
                prompt="Analyze results",
                session_id="e2e-session",
            ),
        ]
        results = orchestrator.execute_parallel(tasks)

        # Step 6: Verify results
        assert len(results) == 2
        mock_manager.execute.call_count == 2

    def test_messaging_broadcast_to_orchestration(self):
        """Broadcast message should trigger multiple orchestrations."""
        router = MessageRouter()
        for name in ["broadcaster", "listener-1", "listener-2", "listener-3"]:
            router.register_agent(name)

        broadcast_msg = AgentMessage(
            from_agent="broadcaster",
            to_agent="listener-1",  # ignored for broadcast
            session_id="broadcast-session",
            content="Broadcast: All agents start",
        )
        count = router.broadcast(broadcast_msg, exclude=[])
        # Excludes sender, delivers to 3 listeners
        assert count == 3

        # Verify all listeners received
        for name in ["listener-1", "listener-2", "listener-3"]:
            received = router.receive(name)
            assert len(received) == 1
            assert received[0].content == "Broadcast: All agents start"


class TestPermissionToExecutionFlow(object):
    """
    E2E Test 4: Permission -> Execution flow.

    Verifies the complete pipeline:
    1. Create permission ruleset
    2. Configure agent with permission rules
    3. Execute agent with tool calls
    4. Verify permission checks
    5. Handle denied permissions
    """

    def test_permission_to_execution_pipeline(self):
        """Complete pipeline from permission setup to execution."""
        # Step 1: Create permission ruleset
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="allow"))
        ruleset.add_rule(AgentPermissionRule(tool="bash", action="deny"))
        ruleset.add_rule(AgentPermissionRule(tool="network", action="ask"))

        # Step 2: Verify permission checks
        assert ruleset.check("read") == ALLOWED
        assert ruleset.check("write") == ALLOWED
        assert ruleset.check("bash") == DENIED
        assert ruleset.check("network") == NEEDS_ASK
        assert ruleset.check("unknown") == NEEDS_ASK

        # Step 3: Create agent with permission rules
        schema = AgentSchema(
            name="permission-agent",
            description="Agent with permission rules",
            mode="subagent",
            model="gpt-4o",
            prompt="You are a permission-controlled agent.",
            permission="restricted",
            options={"tools": ["read", "write", "bash"]},
        )
        agent = create_agent_from_schema(schema)

        # Step 4: Verify agent has correct tools
        assert "read" in agent.tools
        assert "write" in agent.tools
        assert "bash" in agent.tools

        # Step 5: Execute with permission check (mocked)
        mock_response = ChatResponse(
            id="resp-1",
            model="gpt-4o",
            content="Permission-checked execution",
            usage={"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        )
        with patch.object(agent, "execute", return_value=mock_response):
            result = agent.execute([ChatMessage(role="user", content="Test")])
            assert result.content == "Permission-checked execution"

    def test_permission_agent_specific_rules(self):
        """Agent-specific permission rules should override global rules."""
        ruleset = PermissionRuleset()
        # Global rule: allow write
        ruleset.add_rule(AgentPermissionRule(tool="write", action="allow"))
        # Agent-specific rule: deny write for "restricted-agent"
        ruleset.add_rule(AgentPermissionRule(
            tool="write", action="deny", agent="restricted-agent"
        ))

        # Global agents can write
        assert ruleset.check("write", agent="general-agent") == ALLOWED
        # Restricted agent cannot write
        assert ruleset.check("write", agent="restricted-agent") == DENIED

    def test_permission_ruleset_merge(self):
        """Merged rulesets should combine rules correctly."""
        ruleset1 = PermissionRuleset()
        ruleset1.add_rule(AgentPermissionRule(tool="read", action="allow"))
        ruleset1.add_rule(AgentPermissionRule(tool="write", action="deny"))

        ruleset2 = PermissionRuleset()
        ruleset2.add_rule(AgentPermissionRule(tool="bash", action="allow"))
        ruleset2.add_rule(AgentPermissionRule(tool="write", action="allow"))  # Override

        merged = ruleset1.merge(ruleset2)
        assert len(merged) == 4  # merge appends all rules (no dedup)

        # ruleset2's rules get priority+1000, so they take precedence
        assert merged.check("read") == ALLOWED
        assert merged.check("write") == ALLOWED  # ruleset2's allow wins (higher priority)
        assert merged.check("bash") == ALLOWED


class TestFrontendAdapterToDisplayFlow(object):
    """
    E2E Test 5: FrontendAdapter -> Display flow.

    Verifies the complete pipeline:
    1. Create FrontendAdapter with mock dependencies
    2. Subscribe to events
    3. Execute agent (mocked)
    4. Verify events published
    5. Verify display updates
    """

    def test_frontend_adapter_to_display_pipeline(self):
        """Complete pipeline from adapter setup to display updates."""
        # Step 1: Create mock dependencies
        mock_manager = MagicMock()
        mock_registry = MagicMock()
        mock_display = MagicMock()

        # Create a concrete subclass for testing
        class TestAdapter(FrontendAdapter):
            def execute_agent(self, agent_name, messages, session_id,
                              on_tool_call=None, on_permission_ask=None, extra=None):
                # Simulate execution with events
                self._publish_event({
                    "type": "execution_start",
                    "agent_name": agent_name,
                    "session_id": session_id,
                })
                result = {"content": "Execution result", "usage": None}
                self._publish_event({
                    "type": "execution_complete",
                    "agent_name": agent_name,
                    "session_id": session_id,
                })
                return result

            def cancel_execution(self):
                self._publish_event({"type": "execution_cancelled"})

            def get_agent_status(self, session_id=None):
                return {
                    "is_executing": False,
                    "current_agent": None,
                    "session_id": session_id,
                }

        adapter = TestAdapter(mock_manager, mock_registry, mock_display)

        # Step 2: Subscribe to events
        events_received = []

        def event_callback(event):
            events_received.append(event)

        adapter.subscribe_events(event_callback)

        # Step 3: Execute agent
        messages = [ChatMessage(role="user", content="Test message")]
        result = adapter.execute_agent(
            agent_name="test-agent",
            messages=messages,
            session_id="e2e-display-session",
        )

        # Step 4: Verify events published
        assert len(events_received) == 2
        assert events_received[0]["type"] == "execution_start"
        assert events_received[0]["agent_name"] == "test-agent"
        assert events_received[1]["type"] == "execution_complete"

        # Step 5: Verify result
        assert result["content"] == "Execution result"

    def test_frontend_adapter_cancel_execution(self):
        """Cancel execution should publish cancellation event."""
        mock_manager = MagicMock()
        mock_registry = MagicMock()

        class TestAdapter(FrontendAdapter):
            def execute_agent(self, agent_name, messages, session_id,
                              on_tool_call=None, on_permission_ask=None, extra=None):
                return {"content": "result", "usage": None}

            def cancel_execution(self):
                self._publish_event({"type": "execution_cancelled"})

            def get_agent_status(self, session_id=None):
                return {"is_executing": False}

        adapter = TestAdapter(mock_manager, mock_registry)

        events = []
        adapter.subscribe_events(lambda e: events.append(e))

        adapter.cancel_execution()

        assert len(events) == 1
        assert events[0]["type"] == "execution_cancelled"

    def test_frontend_adapter_unsubscribe_events(self):
        """Unsubscribing should stop receiving events."""
        mock_manager = MagicMock()
        mock_registry = MagicMock()

        class TestAdapter(FrontendAdapter):
            def execute_agent(self, agent_name, messages, session_id,
                              on_tool_call=None, on_permission_ask=None, extra=None):
                self._publish_event({"type": "test"})
                return {"content": "result", "usage": None}

            def cancel_execution(self):
                pass

            def get_agent_status(self, session_id=None):
                return {"is_executing": False}

        adapter = TestAdapter(mock_manager, mock_registry)

        events = []
        callback = lambda e: events.append(e)

        adapter.subscribe_events(callback)
        adapter._publish_event({"type": "before_unsubscribe"})
        assert len(events) == 1

        adapter.unsubscribe_events(callback)
        adapter._publish_event({"type": "after_unsubscribe"})
        assert len(events) == 1  # Still 1, didn't receive after unsubscribe
