"""
Tests for berserker.agent.config module.

Covers:
- strip_jsonc_comments() — JSONC comment stripping.
- load_agents_from_config() — loading from JSON/JSONC files.
- load_agents_from_dict() — loading from dictionaries.
- merge_agent_configs() — merging multiple config layers.
- Edge cases: invalid formats, missing keys, empty configs.

Python 3.8.10 compatible.
"""

import json
import os
import tempfile

import pytest

from berserker.agent.config import (
    load_agents_from_config,
    load_agents_from_dict,
    merge_agent_configs,
    strip_jsonc_comments,
)
from berserker.agent.schema import AgentSchema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_agent_dict():
    """Return a minimal valid agent dictionary."""
    return {
        "name": "test-agent",
        "description": "A test agent",
        "mode": "subagent",
        "model": "gpt-4o",
        "prompt": "You are a test agent.",
    }


@pytest.fixture
def valid_config_dict(valid_agent_dict):
    """Return a valid config dictionary with agents list."""
    return {"agents": [valid_agent_dict]}


@pytest.fixture
def multi_agent_config():
    """Return a config with multiple agents."""
    return {
        "agents": [
            {
                "name": "agent-one",
                "description": "First agent",
                "mode": "subagent",
                "model": "gpt-4o",
                "prompt": "You are agent one.",
            },
            {
                "name": "agent-two",
                "description": "Second agent",
                "mode": "primary",
                "model": "claude-sonnet-4-20250514",
                "prompt": "You are agent two.",
            },
        ]
    }


@pytest.fixture
def temp_json_file(valid_config_dict):
    """Create a temporary JSON config file."""
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(valid_config_dict, f)
        yield path
    finally:
        os.unlink(path)


@pytest.fixture
def temp_jsonc_file():
    """Create a temporary JSONC config file with comments."""
    fd, path = tempfile.mkstemp(suffix=".jsonc")
    content = """// This is a config file
{
    // Agent definitions
    "agents": [
        {
            "name": "commented-agent",
            "description": "Agent from JSONC",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a commented agent."
        }
    ]
}
/* Block comment at end */
"""
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        yield path
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# JSONC Comment Stripping Tests
# ---------------------------------------------------------------------------


class TestStripJsoncComments:
    """Tests for strip_jsonc_comments function."""

    def test_no_comments(self):
        text = '{"name": "test"}'
        assert strip_jsonc_comments(text) == text

    def test_line_comment(self):
        text = '{"name": "test"} // comment'
        result = strip_jsonc_comments(text)
        assert '"name": "test"' in result
        assert "//" not in result

    def test_block_comment(self):
        text = '{"name": /* comment */ "test"}'
        result = strip_jsonc_comments(text)
        assert '"name":' in result
        assert '"test"' in result
        assert "/*" not in result

    def test_multiline_block_comment(self):
        text = """{
    /* This is a
       multiline comment */
    "name": "test"
}"""
        result = strip_jsonc_comments(text)
        assert '"name": "test"' in result
        assert "/*" not in result

    def test_comment_in_string_preserved(self):
        """Comments inside strings should NOT be stripped."""
        text = '{"prompt": "Use // for comments"}'
        result = strip_jsonc_comments(text)
        assert result == text

    def test_block_comment_in_string_preserved(self):
        """Block comment syntax inside strings should NOT be stripped."""
        text = '{"prompt": "Use /* */ for blocks"}'
        result = strip_jsonc_comments(text)
        assert result == text

    def test_multiple_line_comments(self):
        text = """// First comment
{"agents": [
    // Second comment
    {"name": "test"}
]}
// Third comment"""
        result = strip_jsonc_comments(text)
        assert "//" not in result
        # Should still be valid JSON
        parsed = json.loads(result)
        assert "agents" in parsed

    def test_empty_string(self):
        assert strip_jsonc_comments("") == ""

    def test_only_comments(self):
        text = "// Just a comment"
        result = strip_jsonc_comments(text).strip()
        assert result == ""

    def test_unterminated_block_comment(self):
        text = '{"name": "test" /* unterminated'
        result = strip_jsonc_comments(text)
        # Should strip everything after /*
        assert "/*" not in result


# ---------------------------------------------------------------------------
# load_agents_from_dict Tests
# ---------------------------------------------------------------------------


class TestLoadAgentsFromDict:
    """Tests for load_agents_from_dict function."""

    def test_valid_single_agent(self, valid_config_dict):
        schemas = load_agents_from_dict(valid_config_dict)
        assert len(schemas) == 1
        assert isinstance(schemas[0], AgentSchema)
        assert schemas[0].name == "test-agent"

    def test_valid_multiple_agents(self, multi_agent_config):
        schemas = load_agents_from_dict(multi_agent_config)
        assert len(schemas) == 2
        names = [s.name for s in schemas]
        assert "agent-one" in names
        assert "agent-two" in names

    def test_empty_agents_list(self):
        schemas = load_agents_from_dict({"agents": []})
        assert schemas == []

    def test_missing_agents_key(self):
        with pytest.raises(ValueError) as excinfo:
            load_agents_from_dict({"foo": "bar"})
        assert "'agents'" in str(excinfo.value)

    def test_agents_not_list(self):
        with pytest.raises(ValueError) as excinfo:
            load_agents_from_dict({"agents": "not-a-list"})
        assert "must be a list" in str(excinfo.value)

    def test_config_not_dict(self):
        with pytest.raises(ValueError) as excinfo:
            load_agents_from_dict("not-a-dict")
        assert "must be a dictionary" in str(excinfo.value)

    def test_invalid_agent_skipped_with_warning(self, caplog):
        config = {
            "agents": [
                {
                    "name": "valid-agent",
                    "description": "Valid",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "Valid prompt.",
                },
                {"name": "invalid-agent"},  # Missing required fields
            ]
        }
        schemas = load_agents_from_dict(config)
        assert len(schemas) == 1
        assert schemas[0].name == "valid-agent"
        # Check warning was logged
        assert "Skipping agent 'invalid-agent'" in caplog.text

    def test_non_dict_agent_skipped(self, caplog):
        config = {"agents": ["not-a-dict", 42, None]}
        schemas = load_agents_from_dict(config)
        assert schemas == []
        assert "Skipping agent at index" in caplog.text

    def test_agent_with_optional_fields(self):
        config = {
            "agents": [
                {
                    "name": "full-agent",
                    "description": "Full config",
                    "mode": "primary",
                    "model": "gpt-4o",
                    "prompt": "Full prompt.",
                    "permission": "restricted",
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "hidden": True,
                }
            ]
        }
        schemas = load_agents_from_dict(config)
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema.permission == "restricted"
        assert schema.temperature == 0.7
        assert schema.top_p == 0.9
        assert schema.hidden is True


# ---------------------------------------------------------------------------
# load_agents_from_config Tests
# ---------------------------------------------------------------------------


class TestLoadAgentsFromConfig:
    """Tests for load_agents_from_config function."""

    def test_load_from_json_file(self, temp_json_file):
        schemas = load_agents_from_config(temp_json_file)
        assert len(schemas) == 1
        assert schemas[0].name == "test-agent"

    def test_load_from_jsonc_file(self, temp_jsonc_file):
        schemas = load_agents_from_config(temp_jsonc_file)
        assert len(schemas) == 1
        assert schemas[0].name == "commented-agent"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_agents_from_config("/nonexistent/path/config.json")

    def test_invalid_json(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("{invalid json}")
            with pytest.raises(ValueError) as excinfo:
                load_agents_from_config(path)
            assert "Invalid JSON" in str(excinfo.value)
        finally:
            os.unlink(path)

    def test_missing_agents_key_in_file(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"foo": "bar"}, f)
            with pytest.raises(ValueError) as excinfo:
                load_agents_from_config(path)
            assert "'agents'" in str(excinfo.value)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# merge_agent_configs Tests
# ---------------------------------------------------------------------------


class TestMergeAgentConfigs:
    """Tests for merge_agent_configs function."""

    def test_merge_single_config(self, valid_config_dict):
        merged = merge_agent_configs([valid_config_dict])
        assert len(merged) == 1
        assert "test-agent" in merged

    def test_merge_multiple_configs(self):
        config1 = {
            "agents": [
                {
                    "name": "agent-a",
                    "description": "Agent A",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "Agent A prompt.",
                }
            ]
        }
        config2 = {
            "agents": [
                {
                    "name": "agent-b",
                    "description": "Agent B",
                    "mode": "primary",
                    "model": "claude-sonnet-4-20250514",
                    "prompt": "Agent B prompt.",
                }
            ]
        }
        merged = merge_agent_configs([config1, config2])
        assert len(merged) == 2
        assert "agent-a" in merged
        assert "agent-b" in merged

    def test_later_config_overrides(self):
        """Later configs should override earlier ones for same name."""
        config1 = {
            "agents": [
                {
                    "name": "shared-agent",
                    "description": "Original description",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "Original prompt.",
                }
            ]
        }
        config2 = {
            "agents": [
                {
                    "name": "shared-agent",
                    "description": "Overridden description",
                    "mode": "primary",
                    "model": "claude-sonnet-4-20250514",
                    "prompt": "Overridden prompt.",
                }
            ]
        }
        merged = merge_agent_configs([config1, config2])
        assert len(merged) == 1
        schema = merged["shared-agent"]
        assert schema.description == "Overridden description"
        assert schema.mode == "primary"
        assert schema.model == "claude-sonnet-4-20250514"

    def test_invalid_config_skipped(self, caplog):
        valid = {
            "agents": [
                {
                    "name": "valid",
                    "description": "Valid",
                    "mode": "subagent",
                    "model": "gpt-4o",
                    "prompt": "Valid prompt.",
                }
            ]
        }
        invalid = {"not-agents": []}
        merged = merge_agent_configs([valid, invalid])
        assert len(merged) == 1
        assert "valid" in merged
        assert "Skipping invalid config" in caplog.text

    def test_empty_config_list(self):
        merged = merge_agent_configs([])
        assert merged == {}
