"""
Agent discovery from standard paths for berserker.

Provides:
- discover_agents() — scan standard paths for agent config files.
- load_agent_file(path) — load a single .json/.jsonc agent file.
- AGENT_DISCOVERY_PATHS — constant list of path patterns.

Discovery paths (in priority order):
1. {config_dir}/agents/ — global agents (platformdirs or expanduser)
2. {cwd}/.berserker/agents/ — project agents
3. {cwd}/agents/ — local agents

Python 3.8.10 compatible: uses type comments, typing module imports.
"""

from __future__ import annotations

import logging
import os
from typing import List

from berserker.agent.config import load_agents_from_config
from berserker.agent.schema import AgentSchema

logger = logging.getLogger(__name__)

# Standard discovery paths (relative patterns).
# Each entry is a tuple: (base_resolver, relative_subpath)
# - base_resolver: callable returning the base directory
# - relative_subpath: subdirectory to append
AGENT_DISCOVERY_PATHS = [
    # Global agents: ~/.config/berserker/agents/ or ~/berserker/agents/
    (lambda: _get_config_dir(), "agents"),
    # Project agents: {cwd}/.berserker/agents/
    (lambda: os.getcwd(), ".berserker/agents"),
    # Local agents: {cwd}/agents/
    (lambda: os.getcwd(), "agents"),
]  # type: List[tuple]


def _get_config_dir():
    # type: () -> str
    """Get the global config directory for berserker.

    Tries platformdirs first, falls back to ~/.config/berserker,
    then ~/berserker as last resort.

    Returns:
        Path to the global config directory.
    """
    # Try platformdirs
    try:
        from platformdirs import user_config_dir
        return user_config_dir("berserker")
    except ImportError:
        pass

    # Fallback to ~/.config/berserker
    home = os.path.expanduser("~")
    config_dir = os.path.join(home, ".config", "berserker")
    if os.path.isdir(config_dir):
        return config_dir

    # Last resort: ~/berserker
    return os.path.join(home, "berserker")


def load_agent_file(path):
    # type: (str) -> AgentSchema
    """Load a single agent from a .json or .jsonc file.

    The file should contain a single agent definition (dict with
    AgentSchema fields), NOT the {"agents": [...]} wrapper format.

    Args:
        path: Path to the .json or .jsonc file.

    Returns:
        Validated AgentSchema instance.

    Raises:
        ValueError: If the file format is invalid.
        FileNotFoundError: If the file does not exist.
    """
    from berserker.agent.config import strip_jsonc_comments
    import json
    from berserker.agent.schema import validate_agent_schema

    with open(path, 'r', encoding='utf-8') as f:
        raw_text = f.read()

    # Strip JSONC comments
    clean_text = strip_jsonc_comments(raw_text)

    # Parse JSON
    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            "Invalid JSON in agent file '{}': {}".format(path, str(e))
        )

    # If the file uses the {"agents": [...]} wrapper, extract first agent
    if isinstance(data, dict) and 'agents' in data:
        agents_list = data['agents']
        if not agents_list:
            raise ValueError("Agent file '{}' has empty 'agents' list".format(path))
        data = agents_list[0]

    return validate_agent_schema(data)


def discover_agents():
    # type: () -> List[AgentSchema]
    """Scan standard paths for agent configuration files.

    Searches in priority order:
    1. {config_dir}/agents/
    2. {cwd}/.berserker/agents/
    3. {cwd}/agents/

    Each directory can contain multiple .json or .jsonc files.
    Files are loaded and validated; invalid files are skipped with warnings.

    Returns:
        List of validated AgentSchema instances from all discovered files.
    """
    all_schemas = []  # type: List[AgentSchema]
    seen_names = set()  # type: set

    for base_resolver, subpath in AGENT_DISCOVERY_PATHS:
        try:
            base_dir = base_resolver()
        except Exception as e:
            logger.debug("Failed to resolve base directory: %s", str(e))
            continue

        agent_dir = os.path.join(base_dir, subpath)
        if not os.path.isdir(agent_dir):
            logger.debug("Agent directory not found: %s", agent_dir)
            continue

        # Scan for .json and .jsonc files
        try:
            files = sorted([
                f for f in os.listdir(agent_dir)
                if f.endswith('.json') or f.endswith('.jsonc')
            ])
        except OSError as e:
            logger.warning("Cannot list directory '%s': %s", agent_dir, str(e))
            continue

        for filename in files:
            filepath = os.path.join(agent_dir, filename)
            if not os.path.isfile(filepath):
                continue

            try:
                # load_agent_file returns a single schema
                # But files might also use the {"agents": [...]} format
                schemas = _load_agent_file_flexible(filepath)
                for schema in schemas:
                    if schema.name in seen_names:
                        logger.debug(
                            "Skipping duplicate agent '%s' from %s "
                            "(already loaded from higher-priority path)",
                            schema.name, filepath
                        )
                        continue
                    seen_names.add(schema.name)
                    all_schemas.append(schema)
                    logger.info("Discovered agent '%s' from %s", schema.name, filepath)
            except Exception as e:
                logger.warning(
                    "Skipping invalid agent file '%s': %s",
                    filepath, str(e)
                )

    return all_schemas


def _load_agent_file_flexible(filepath):
    # type: (str) -> List[AgentSchema]
    """Load agent(s) from a file, supporting both single-agent and multi-agent formats.

    Supports:
    - Single agent: {"name": "...", "description": "...", ...}
    - Multi-agent: {"agents": [{...}, {...}]}

    Args:
        filepath: Path to the .json or .jsonc file.

    Returns:
        List of validated AgentSchema instances.
    """
    from berserker.agent.config import strip_jsonc_comments
    import json
    from berserker.agent.schema import validate_agent_schema

    with open(filepath, 'r', encoding='utf-8') as f:
        raw_text = f.read()

    clean_text = strip_jsonc_comments(raw_text)

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            "Invalid JSON in file '{}': {}".format(filepath, str(e))
        )

    schemas = []  # type: List[AgentSchema]

    if isinstance(data, dict):
        if 'agents' in data:
            # Multi-agent format
            agents_list = data['agents']
            if not isinstance(agents_list, list):
                raise ValueError("'agents' must be a list")
            for agent_dict in agents_list:
                if isinstance(agent_dict, dict):
                    schemas.append(validate_agent_schema(agent_dict))
        else:
            # Single agent format
            schemas.append(validate_agent_schema(data))
    elif isinstance(data, list):
        # Raw list of agents
        for agent_dict in data:
            if isinstance(agent_dict, dict):
                schemas.append(validate_agent_schema(agent_dict))
    else:
        raise ValueError("Unexpected data format in agent file")

    return schemas
