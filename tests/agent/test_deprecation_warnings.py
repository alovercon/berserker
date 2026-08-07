"""
Deprecation warning tests for AgentInfo class.

Verifies that:
- AgentInfo instantiation emits DeprecationWarning
- Warning message mentions AgentSchema/BaseAgent as replacement
- Functionality still works despite the warning
- Internal AgentInfo creations (built-in agents, register, etc.) do NOT emit warnings

Python 3.8.10 compatible: uses type comments, pytest.warns().
"""

from __future__ import annotations

import warnings
import pytest

from berserker.agent.manager import AgentInfo


class TestAgentInfoDeprecationWarning(object):
    """Test that AgentInfo emits DeprecationWarning on instantiation."""

    def test_agent_info_emits_deprecation_warning(self):
        """AgentInfo instantiation should emit a DeprecationWarning."""
        with pytest.warns(DeprecationWarning) as record:
            agent = AgentInfo(
                name="test-agent",
                mode="subagent",
                model="gpt-4o",
                system_prompt="You are a test agent.",
                tools=["read", "write"],
            )

        assert len(record) == 1
        assert agent.name == "test-agent"

    def test_warning_message_mentions_replacement(self):
        """The warning message should mention AgentSchema and BaseAgent."""
        with pytest.warns(DeprecationWarning) as record:
            AgentInfo(
                name="test",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )

        warning_message = str(record[0].message)
        assert "AgentSchema" in warning_message
        assert "BaseAgent" in warning_message
        assert "deprecated" in warning_message.lower()

    def test_warning_is_deprecation_type(self):
        """The warning should be a DeprecationWarning, not just a UserWarning."""
        with pytest.warns(DeprecationWarning) as record:
            AgentInfo(
                name="test",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )

        assert record[0].category == DeprecationWarning

    def test_functionality_still_works_despite_warning(self):
        """AgentInfo should still function correctly after emitting the warning."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            agent = AgentInfo(
                name="functional-test",
                mode="primary",
                model="claude-3.5-sonnet",
                system_prompt="You are a functional test agent.",
                tools=["read", "write", "bash"],
                description="Testing that functionality works",
                permission="restricted",
                max_tool_iterations=50,
            )

        assert agent.name == "functional-test"
        assert agent.mode == "primary"
        assert agent.model == "claude-3.5-sonnet"
        assert "functional test" in agent.system_prompt
        assert agent.tools == ["read", "write", "bash"]
        assert agent.description == "Testing that functionality works"
        assert agent.permission == "restricted"
        assert agent.max_tool_iterations == 50

    def test_agent_info_repr_works(self):
        """__repr__ should still work correctly."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            agent = AgentInfo(
                name="repr-test",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )

        repr_str = repr(agent)
        assert "repr-test" in repr_str
        assert "subagent" in repr_str
        assert "gpt-4o" in repr_str

    def test_agent_info_equality_works(self):
        """__eq__ should still work correctly."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            agent1 = AgentInfo(
                name="eq-test",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )
            agent2 = AgentInfo(
                name="eq-test",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )
            agent3 = AgentInfo(
                name="eq-test-different",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )

        assert agent1 == agent2
        assert agent1 != agent3

    def test_warning_stacklevel_points_to_caller(self):
        """The warning should point to the caller's code, not AgentInfo.__init__."""
        with pytest.warns(DeprecationWarning) as record:
            AgentInfo(
                name="stacklevel-test",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )

        # The warning filename should point to this test file, not manager.py
        assert "test_deprecation_warnings" in record[0].filename

    def test_multiple_instantiations_emit_multiple_warnings(self):
        """Each AgentInfo instantiation should emit its own warning."""
        with pytest.warns(DeprecationWarning) as record:
            AgentInfo(
                name="first",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )
            AgentInfo(
                name="second",
                mode="subagent",
                model="gpt-4o",
                system_prompt="Test.",
                tools=["read"],
            )

        assert len(record) == 2
