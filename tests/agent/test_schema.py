"""
Tests for berserker.agent.schema module.

Covers:
- AgentSchema dataclass creation with valid/invalid data.
- Field validation (required, mode, permission, sampling params, name format).
- validate_agent_schema() function.
- to_dict() and from_dict() round-trip.
"""

import pytest

from berserker.agent.schema import AgentSchema, validate_agent_schema
from berserker.agent.exceptions import AgentSchemaValidationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_schema_dict():
    """Return a minimal valid schema dictionary."""
    return {
        "name": "test-agent",
        "description": "A test agent",
        "mode": "subagent",
        "model": "gpt-4o",
        "prompt": "You are a test agent.",
    }


@pytest.fixture
def full_schema_dict():
    """Return a fully populated schema dictionary."""
    return {
        "name": "full-agent",
        "description": "A fully configured agent",
        "mode": "primary",
        "native": True,
        "hidden": False,
        "top_p": 0.9,
        "temperature": 0.7,
        "color": "#FF5733",
        "permission": "full",
        "model": "gpt-4o",
        "variant": "turbo",
        "prompt": "You are a full agent.",
        "options": {"max_steps": 50},
        "steps": 50,
    }


# ---------------------------------------------------------------------------
# Valid Schema Tests
# ---------------------------------------------------------------------------


class TestAgentSchemaValid:
    """Tests for valid AgentSchema creation."""

    def test_minimal_schema(self, valid_schema_dict):
        schema = AgentSchema.from_dict(valid_schema_dict)
        assert schema.name == "test-agent"
        assert schema.description == "A test agent"
        assert schema.mode == "subagent"
        assert schema.model == "gpt-4o"
        assert schema.prompt == "You are a test agent."

    def test_full_schema(self, full_schema_dict):
        schema = AgentSchema.from_dict(full_schema_dict)
        assert schema.name == "full-agent"
        assert schema.native is True
        assert schema.hidden is False
        assert schema.top_p == 0.9
        assert schema.temperature == 0.7
        assert schema.color == "#FF5733"
        assert schema.permission == "full"
        assert schema.variant == "turbo"
        assert schema.options == {"max_steps": 50}
        assert schema.steps == 50

    def test_defaults(self, valid_schema_dict):
        schema = AgentSchema.from_dict(valid_schema_dict)
        assert schema.native is False
        assert schema.hidden is False
        assert schema.permission == "full"
        assert schema.options == {}
        assert schema.top_p is None
        assert schema.temperature is None
        assert schema.color is None
        assert schema.variant is None
        assert schema.steps is None

    def test_all_modes_valid(self):
        for mode in ["primary", "subagent", "hidden"]:
            data = {
                "name": "mode-test",
                "description": "Test",
                "mode": mode,
                "model": "gpt-4o",
                "prompt": "Test prompt",
            }
            schema = AgentSchema.from_dict(data)
            assert schema.mode == mode

    def test_both_permissions_valid(self):
        for perm in ["full", "restricted"]:
            data = {
                "name": "perm-test",
                "description": "Test",
                "mode": "subagent",
                "model": "gpt-4o",
                "prompt": "Test prompt",
                "permission": perm,
            }
            schema = AgentSchema.from_dict(data)
            assert schema.permission == perm


# ---------------------------------------------------------------------------
# Invalid Schema Tests
# ---------------------------------------------------------------------------


class TestAgentSchemaInvalid:
    """Tests for invalid AgentSchema creation."""

    def test_missing_name(self, valid_schema_dict):
        del valid_schema_dict["name"]
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "name"

    def test_empty_name(self, valid_schema_dict):
        valid_schema_dict["name"] = ""
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "name"

    def test_missing_description(self, valid_schema_dict):
        del valid_schema_dict["description"]
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "description"

    def test_missing_mode(self, valid_schema_dict):
        del valid_schema_dict["mode"]
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "mode"

    def test_missing_model(self, valid_schema_dict):
        del valid_schema_dict["model"]
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "model"

    def test_missing_prompt(self, valid_schema_dict):
        del valid_schema_dict["prompt"]
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "prompt"

    def test_invalid_mode(self, valid_schema_dict):
        valid_schema_dict["mode"] = "invalid-mode"
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "mode"

    def test_invalid_permission(self, valid_schema_dict):
        valid_schema_dict["permission"] = "admin"
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "permission"

    def test_top_p_out_of_range_high(self, valid_schema_dict):
        valid_schema_dict["top_p"] = 1.5
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "top_p"

    def test_top_p_out_of_range_low(self, valid_schema_dict):
        valid_schema_dict["top_p"] = -0.1
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "top_p"

    def test_temperature_out_of_range_high(self, valid_schema_dict):
        valid_schema_dict["temperature"] = 2.5
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "temperature"

    def test_temperature_out_of_range_low(self, valid_schema_dict):
        valid_schema_dict["temperature"] = -0.1
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "temperature"

    def test_top_p_wrong_type(self, valid_schema_dict):
        valid_schema_dict["top_p"] = "high"
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "top_p"

    def test_temperature_wrong_type(self, valid_schema_dict):
        valid_schema_dict["temperature"] = "hot"
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "temperature"

    def test_name_uppercase_invalid(self, valid_schema_dict):
        valid_schema_dict["name"] = "Test-Agent"
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "name"

    def test_name_starts_with_number_invalid(self, valid_schema_dict):
        valid_schema_dict["name"] = "1test-agent"
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "name"

    def test_name_with_underscore_invalid(self, valid_schema_dict):
        valid_schema_dict["name"] = "test_agent"
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "name"

    def test_name_with_spaces_invalid(self, valid_schema_dict):
        valid_schema_dict["name"] = "test agent"
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            AgentSchema.from_dict(valid_schema_dict)
        assert exc_info.value.field == "name"

    def test_unknown_fields_ignored(self, valid_schema_dict):
        valid_schema_dict["unknown_field"] = "should be ignored"
        schema = AgentSchema.from_dict(valid_schema_dict)
        assert schema.name == "test-agent"
        # unknown_field should not be an attribute
        assert not hasattr(schema, "unknown_field")


# ---------------------------------------------------------------------------
# validate_agent_schema() Tests
# ---------------------------------------------------------------------------


class TestValidateAgentSchema:
    """Tests for the validate_agent_schema() function."""

    def test_valid_dict(self, valid_schema_dict):
        schema = validate_agent_schema(valid_schema_dict)
        assert isinstance(schema, AgentSchema)
        assert schema.name == "test-agent"

    def test_non_dict_raises(self):
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            validate_agent_schema("not a dict")
        assert exc_info.value.field == "data"
        assert "Expected a dictionary" in str(exc_info.value)

    def test_list_raises(self):
        with pytest.raises(AgentSchemaValidationError) as exc_info:
            validate_agent_schema([{"name": "test"}])
        assert exc_info.value.field == "data"


# ---------------------------------------------------------------------------
# Serialization Tests
# ---------------------------------------------------------------------------


class TestSchemaSerialization:
    """Tests for to_dict() and from_dict() round-trip."""

    def test_to_dict_minimal(self, valid_schema_dict):
        schema = AgentSchema.from_dict(valid_schema_dict)
        result = schema.to_dict()
        assert isinstance(result, dict)
        assert result["name"] == "test-agent"
        assert result["mode"] == "subagent"
        assert result["model"] == "gpt-4o"

    def test_to_dict_has_all_13_fields(self, valid_schema_dict):
        schema = AgentSchema.from_dict(valid_schema_dict)
        result = schema.to_dict()
        expected_keys = {
            "name", "description", "mode", "native", "hidden",
            "top_p", "temperature", "color", "permission",
            "model", "variant", "prompt", "options", "steps",
        }
        assert set(result.keys()) == expected_keys

    def test_roundtrip_full(self, full_schema_dict):
        schema1 = AgentSchema.from_dict(full_schema_dict)
        data = schema1.to_dict()
        schema2 = AgentSchema.from_dict(data)
        assert schema1.name == schema2.name
        assert schema1.mode == schema2.mode
        assert schema1.model == schema2.model
        assert schema1.prompt == schema2.prompt
        assert schema1.top_p == schema2.top_p
        assert schema1.temperature == schema2.temperature
        assert schema1.options == schema2.options
