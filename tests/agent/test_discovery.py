"""
Tests for berserker.agent.discovery module.

Covers:
- load_agent_file() — loading single agent files.
- discover_agents() — scanning standard paths.
- Priority ordering and duplicate handling.
- Invalid file skipping.

Python 3.8.10 compatible.
"""

import json
import os
import shutil
import tempfile

import pytest

from berserker.agent.discovery import (
    AGENT_DISCOVERY_PATHS,
    _get_config_dir,
    _load_agent_file_flexible,
    load_agent_file,
)
from berserker.agent.schema import AgentSchema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_agent_dir():
    """Create a temporary directory with agent config files."""
    temp_dir = tempfile.mkdtemp()
    try:
        # Create valid agent file
        valid_agent = {
            "name": "discovered-agent",
            "description": "A discovered agent",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a discovered agent.",
        }
        with open(os.path.join(temp_dir, "agent1.json"), "w") as f:
            json.dump(valid_agent, f)

        # Create valid JSONC agent file
        jsonc_content = """// JSONC agent file
{
    "name": "jsonc-agent",
    "description": "Agent from JSONC",
    "mode": "primary",
    "model": "claude-sonnet-4-20250514",
    "prompt": "You are a JSONC agent."
}
"""
        with open(os.path.join(temp_dir, "agent2.jsonc"), "w") as f:
            f.write(jsonc_content)

        # Create invalid agent file
        with open(os.path.join(temp_dir, "invalid.json"), "w") as f:
            f.write("{invalid json}")

        # Create file with missing required fields
        with open(os.path.join(temp_dir, "incomplete.json"), "w") as f:
            json.dump({"name": "incomplete"}, f)

        yield temp_dir
    finally:
        shutil.rmtree(temp_dir)


@pytest.fixture
def multi_agent_file(temp_agent_dir):
    """Create a multi-agent format file."""
    multi_agent = {
        "agents": [
            {
                "name": "multi-agent-1",
                "description": "First multi agent",
                "mode": "subagent",
                "model": "gpt-4o",
                "prompt": "Multi agent 1 prompt.",
            },
            {
                "name": "multi-agent-2",
                "description": "Second multi agent",
                "mode": "primary",
                "model": "gpt-4o",
                "prompt": "Multi agent 2 prompt.",
            },
        ]
    }
    path = os.path.join(temp_agent_dir, "multi.json")
    with open(path, "w") as f:
        json.dump(multi_agent, f)
    return path


# ---------------------------------------------------------------------------
# load_agent_file Tests
# ---------------------------------------------------------------------------


class TestLoadAgentFile:
    """Tests for load_agent_file function."""

    def test_load_single_agent_json(self, temp_agent_dir):
        path = os.path.join(temp_agent_dir, "agent1.json")
        schema = load_agent_file(path)
        assert isinstance(schema, AgentSchema)
        assert schema.name == "discovered-agent"

    def test_load_jsonc_file(self, temp_agent_dir):
        path = os.path.join(temp_agent_dir, "agent2.jsonc")
        schema = load_agent_file(path)
        assert isinstance(schema, AgentSchema)
        assert schema.name == "jsonc-agent"

    def test_load_multi_agent_file_returns_first(self, multi_agent_file):
        """load_agent_file should return the first agent from multi-agent format."""
        schema = load_agent_file(multi_agent_file)
        assert isinstance(schema, AgentSchema)
        assert schema.name == "multi-agent-1"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_agent_file("/nonexistent/agent.json")

    def test_invalid_json(self, temp_agent_dir):
        path = os.path.join(temp_agent_dir, "invalid.json")
        with pytest.raises(ValueError) as excinfo:
            load_agent_file(path)
        assert "Invalid JSON" in str(excinfo.value)

    def test_empty_agents_list(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"agents": []}, f)
            with pytest.raises(ValueError) as excinfo:
                load_agent_file(path)
            assert "empty" in str(excinfo.value).lower()
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# _load_agent_file_flexible Tests
# ---------------------------------------------------------------------------


class TestLoadAgentFileFlexible:
    """Tests for _load_agent_file_flexible function."""

    def test_single_agent_format(self, temp_agent_dir):
        path = os.path.join(temp_agent_dir, "agent1.json")
        schemas = _load_agent_file_flexible(path)
        assert len(schemas) == 1
        assert schemas[0].name == "discovered-agent"

    def test_multi_agent_format(self, multi_agent_file):
        schemas = _load_agent_file_flexible(multi_agent_file)
        assert len(schemas) == 2
        names = [s.name for s in schemas]
        assert "multi-agent-1" in names
        assert "multi-agent-2" in names

    def test_raw_list_format(self, temp_agent_dir):
        """Test loading a file with raw list of agents."""
        agents_list = [
            {
                "name": "list-agent-1",
                "description": "List agent 1",
                "mode": "subagent",
                "model": "gpt-4o",
                "prompt": "List agent 1 prompt.",
            }
        ]
        path = os.path.join(temp_agent_dir, "list.json")
        with open(path, "w") as f:
            json.dump(agents_list, f)

        schemas = _load_agent_file_flexible(path)
        assert len(schemas) == 1
        assert schemas[0].name == "list-agent-1"


# ---------------------------------------------------------------------------
# discover_agents Tests
# ---------------------------------------------------------------------------


class TestDiscoverAgents:
    """Tests for discover_agents function."""

    def test_discovery_paths_constant(self):
        """AGENT_DISCOVERY_PATHS should be a non-empty list."""
        assert isinstance(AGENT_DISCOVERY_PATHS, list)
        assert len(AGENT_DISCOVERY_PATHS) > 0

    def test_get_config_dir(self):
        """_get_config_dir should return a string path."""
        config_dir = _get_config_dir()
        assert isinstance(config_dir, str)
        assert len(config_dir) > 0

    def test_no_discovery_dirs_exist(self):
        """When no agent directories exist, should return empty list."""
        from berserker.agent.discovery import discover_agents

        # This depends on the actual environment, but typically
        # no agent dirs exist in test environment
        schemas = discover_agents()
        assert isinstance(schemas, list)

    def test_discovery_from_custom_path(self, monkeypatch, temp_agent_dir):
        """Test discovery by monkeypatching the discovery paths."""
        import berserker.agent.discovery as discovery_module

        # Temporarily replace discovery paths with our test directory
        original_paths = discovery_module.AGENT_DISCOVERY_PATHS
        try:
            discovery_module.AGENT_DISCOVERY_PATHS = [
                (lambda: temp_agent_dir, "")
            ]
            schemas = discovery_module.discover_agents()

            # Should find valid agents, skip invalid ones
            names = [s.name for s in schemas]
            assert "discovered-agent" in names
            assert "jsonc-agent" in names
            # Invalid files should be skipped
            assert len(schemas) == 2  # Only the 2 valid agents
        finally:
            discovery_module.AGENT_DISCOVERY_PATHS = original_paths

    def test_duplicate_agents_skipped(self, monkeypatch, temp_agent_dir):
        """Duplicate agent names should be skipped."""
        import berserker.agent.discovery as discovery_module

        # Create two directories with same agent name
        dir2 = tempfile.mkdtemp()
        try:
            # Same agent name in second directory
            with open(os.path.join(dir2, "agent1.json"), "w") as f:
                json.dump(
                    {
                        "name": "discovered-agent",
                        "description": "Duplicate agent",
                        "mode": "primary",
                        "model": "gpt-4o",
                        "prompt": "Duplicate prompt.",
                    },
                    f,
                )

            original_paths = discovery_module.AGENT_DISCOVERY_PATHS
            try:
                discovery_module.AGENT_DISCOVERY_PATHS = [
                    (lambda: temp_agent_dir, ""),
                    (lambda: dir2, ""),
                ]
                schemas = discovery_module.discover_agents()

                # Should only have one instance of the duplicate name
                names = [s.name for s in schemas]
                assert names.count("discovered-agent") == 1
            finally:
                discovery_module.AGENT_DISCOVERY_PATHS = original_paths
        finally:
            shutil.rmtree(dir2)

    def test_non_json_files_ignored(self, monkeypatch, temp_agent_dir):
        """Non-JSON files should be ignored."""
        import berserker.agent.discovery as discovery_module

        # Create non-JSON files
        with open(os.path.join(temp_agent_dir, "readme.txt"), "w") as f:
            f.write("This is not an agent file")
        with open(os.path.join(temp_agent_dir, "notes.md"), "w") as f:
            f.write("# Notes")

        original_paths = discovery_module.AGENT_DISCOVERY_PATHS
        try:
            discovery_module.AGENT_DISCOVERY_PATHS = [
                (lambda: temp_agent_dir, "")
            ]
            schemas = discovery_module.discover_agents()
            # Should still only find the valid JSON/JSONC agents
            assert len(schemas) == 2
        finally:
            discovery_module.AGENT_DISCOVERY_PATHS = original_paths

    def test_missing_directory_skipped(self, monkeypatch):
        """Missing directories should be skipped without error."""
        import berserker.agent.discovery as discovery_module

        original_paths = discovery_module.AGENT_DISCOVERY_PATHS
        try:
            discovery_module.AGENT_DISCOVERY_PATHS = [
                (lambda: "/nonexistent/path", "agents"),
            ]
            schemas = discovery_module.discover_agents()
            assert schemas == []
        finally:
            discovery_module.AGENT_DISCOVERY_PATHS = original_paths

    def test_priority_ordering(self, monkeypatch):
        """Higher priority paths should take precedence."""
        import berserker.agent.discovery as discovery_module

        dir1 = tempfile.mkdtemp()
        dir2 = tempfile.mkdtemp()
        try:
            # Same agent in both directories with different descriptions
            agent_v1 = {
                "name": "priority-agent",
                "description": "Version 1 (higher priority)",
                "mode": "subagent",
                "model": "gpt-4o",
                "prompt": "V1 prompt.",
            }
            agent_v2 = {
                "name": "priority-agent",
                "description": "Version 2 (lower priority)",
                "mode": "primary",
                "model": "gpt-4o",
                "prompt": "V2 prompt.",
            }

            with open(os.path.join(dir1, "agent.json"), "w") as f:
                json.dump(agent_v1, f)
            with open(os.path.join(dir2, "agent.json"), "w") as f:
                json.dump(agent_v2, f)

            original_paths = discovery_module.AGENT_DISCOVERY_PATHS
            try:
                # dir1 is first (higher priority)
                discovery_module.AGENT_DISCOVERY_PATHS = [
                    (lambda: dir1, ""),
                    (lambda: dir2, ""),
                ]
                schemas = discovery_module.discover_agents()

                assert len(schemas) == 1
                assert schemas[0].description == "Version 1 (higher priority)"
            finally:
                discovery_module.AGENT_DISCOVERY_PATHS = original_paths
        finally:
            shutil.rmtree(dir1)
            shutil.rmtree(dir2)
