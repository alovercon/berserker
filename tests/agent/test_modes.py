"""
Tests for berserker.agent.modes module.

Covers:
- AgentMode enum values and from_string() conversion.
- AgentVisibility enum values.
- Helper functions: is_primary(), is_subagent(), is_hidden().
"""

import pytest

from berserker.agent.modes import (
    AgentMode,
    AgentVisibility,
    is_primary,
    is_subagent,
    is_hidden,
)


class TestAgentMode:
    """Tests for AgentMode enum."""

    def test_primary_value(self):
        assert AgentMode.PRIMARY.value == "primary"

    def test_subagent_value(self):
        assert AgentMode.SUBAGENT.value == "subagent"

    def test_hidden_value(self):
        assert AgentMode.HIDDEN.value == "hidden"

    def test_from_string_lowercase(self):
        assert AgentMode.from_string("primary") == AgentMode.PRIMARY
        assert AgentMode.from_string("subagent") == AgentMode.SUBAGENT
        assert AgentMode.from_string("hidden") == AgentMode.HIDDEN

    def test_from_string_uppercase(self):
        assert AgentMode.from_string("PRIMARY") == AgentMode.PRIMARY
        assert AgentMode.from_string("SUBAGENT") == AgentMode.SUBAGENT
        assert AgentMode.from_string("HIDDEN") == AgentMode.HIDDEN

    def test_from_string_mixed_case(self):
        assert AgentMode.from_string("Primary") == AgentMode.PRIMARY
        assert AgentMode.from_string("SubAgent") == AgentMode.SUBAGENT

    def test_from_string_invalid(self):
        with pytest.raises(ValueError) as exc_info:
            AgentMode.from_string("invalid")
        assert "Invalid agent mode" in str(exc_info.value)
        assert "primary" in str(exc_info.value)
        assert "subagent" in str(exc_info.value)
        assert "hidden" in str(exc_info.value)

    def test_valid_values(self):
        values = AgentMode.valid_values()
        assert isinstance(values, list)
        assert "primary" in values
        assert "subagent" in values
        assert "hidden" in values
        assert len(values) == 3


class TestAgentVisibility:
    """Tests for AgentVisibility enum."""

    def test_visible_value(self):
        assert AgentVisibility.VISIBLE.value == "visible"

    def test_hidden_value(self):
        assert AgentVisibility.HIDDEN.value == "hidden"


class TestHelperFunctions:
    """Tests for mode helper functions."""

    def test_is_primary_true(self):
        assert is_primary("primary") is True

    def test_is_primary_false(self):
        assert is_primary("subagent") is False
        assert is_primary("hidden") is False
        assert is_primary("unknown") is False

    def test_is_subagent_true(self):
        assert is_subagent("subagent") is True

    def test_is_subagent_false(self):
        assert is_subagent("primary") is False
        assert is_subagent("hidden") is False

    def test_is_hidden_true(self):
        assert is_hidden("hidden") is True

    def test_is_hidden_false(self):
        assert is_hidden("primary") is False
        assert is_hidden("subagent") is False
