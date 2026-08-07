"""
Config-based agent loading for berserker.

Provides:
- load_agents_from_config(config_path) — load agents from JSON/JSONC file.
- load_agents_from_dict(config) — load agents from dict.
- merge_agent_configs(configs) — merge multiple config layers (later overrides win).
- strip_jsonc_comments(text) — strip // and /* */ comments from JSONC.

Config format: {"agents": [{...AgentSchema fields...}]}

Python 3.8.10 compatible: uses type comments, typing module imports.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from berserker.agent.prompt_loader import PromptLoader
from berserker.agent.schema import AgentSchema, validate_agent_schema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in Prompt Fallback Mapping
# ---------------------------------------------------------------------------

# Inline fallback prompts for built-in agents — used when external prompt files
# are not found and user config omits 'prompt' for a built-in agent.
# These are imported from factory.py to avoid duplication.
_BUILTIN_PROMPT_NAMES = frozenset([
    "berserker", "plan", "general", "explore",
    "compaction", "title", "summary",
    "consultant", "critic", "executor",
])


def _get_builtin_prompt(name):
    # type: (str) -> str
    """Get prompt for a built-in agent, preferring external file with inline fallback.

    Args:
        name: Built-in agent name (e.g., 'build', 'plan').

    Returns:
        The prompt text from external file or inline fallback.
    """
    try:
        loader = PromptLoader()
        return loader.load(name)
    except FileNotFoundError:
        # Fall back to inline prompts from factory.py
        from berserker.agent.factory import (
            _SYSTEM_PROMPT_BUILD,
            _SYSTEM_PROMPT_PLAN,
            _SYSTEM_PROMPT_GENERAL,
            _SYSTEM_PROMPT_EXPLORE,
            _SYSTEM_PROMPT_COMPACTION,
            _SYSTEM_PROMPT_TITLE,
            _SYSTEM_PROMPT_SUMMARY,
            _SYSTEM_PROMPT_CONSULTANT,
            _SYSTEM_PROMPT_CRITIC,
            _SYSTEM_PROMPT_ORCHESTRATOR,
        )
        inline_map = {
            "berserker": _SYSTEM_PROMPT_BUILD,
            "plan": _SYSTEM_PROMPT_PLAN,
            "general": _SYSTEM_PROMPT_GENERAL,
            "explore": _SYSTEM_PROMPT_EXPLORE,
            "compaction": _SYSTEM_PROMPT_COMPACTION,
            "title": _SYSTEM_PROMPT_TITLE,
            "summary": _SYSTEM_PROMPT_SUMMARY,
            "consultant": _SYSTEM_PROMPT_CONSULTANT,
            "critic": _SYSTEM_PROMPT_CRITIC,
            "executor": _SYSTEM_PROMPT_ORCHESTRATOR,
        }
        return inline_map.get(name, "")


# ---------------------------------------------------------------------------
# JSONC Comment Stripping
# ---------------------------------------------------------------------------

# Regex patterns for JSONC comment removal.
# Order matters: block comments must be checked before line comments.
_JSONC_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
_JSONC_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_JSONC_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


def strip_jsonc_comments(text):
    # type: (str) -> str
    """Strip // and /* */ comments from JSONC text, preserving strings.

    Uses a token-by-token approach to avoid stripping comment-like
    sequences that appear inside string values.

    Args:
        text: JSONC text with comments.

    Returns:
        Clean JSON text with comments removed.
    """
    result = []  # type: List[str]
    pos = 0
    length = len(text)

    while pos < length:
        # Check for string literal
        if text[pos] == '"':
            # Find the end of the string
            end = pos + 1
            while end < length:
                if text[end] == '\\':
                    end += 2  # Skip escaped character
                    continue
                if text[end] == '"':
                    end += 1
                    break
                end += 1
            result.append(text[pos:end])
            pos = end
        # Check for block comment
        elif text[pos:pos + 2] == '/*':
            end = text.find('*/', pos + 2)
            if end == -1:
                pos = length  # Unterminated comment, skip rest
            else:
                pos = end + 2
        # Check for line comment
        elif text[pos:pos + 2] == '//':
            end = text.find('\n', pos)
            if end == -1:
                pos = length  # Comment extends to end
            else:
                pos = end + 1
        else:
            result.append(text[pos])
            pos += 1

    return ''.join(result)


# ---------------------------------------------------------------------------
# Config Loading
# ---------------------------------------------------------------------------


def load_agents_from_config(config_path):
    # type: (str) -> List[AgentSchema]
    """Load agent definitions from a JSON or JSONC config file.

    Reads the file, strips JSONC comments if present, parses JSON,
    and validates each agent against AgentSchema.

    Args:
        config_path: Path to the JSON/JSONC config file.

    Returns:
        List of validated AgentSchema instances.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config format is invalid.
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        raw_text = f.read()

    # Strip JSONC comments
    clean_text = strip_jsonc_comments(raw_text)

    # Parse JSON
    try:
        config = json.loads(clean_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            "Invalid JSON in config file '{}': {}".format(config_path, str(e))
        )

    return load_agents_from_dict(config)


def load_agents_from_dict(config):
    # type: (Dict[str, Any]) -> List[AgentSchema]
    """Load agent definitions from a dictionary.

    Expects config format: {"agents": [{...AgentSchema fields...}]}

    Args:
        config: Dictionary with 'agents' key containing list of agent dicts.

    Returns:
        List of validated AgentSchema instances.

    Raises:
        ValueError: If config is missing 'agents' key or has invalid format.
    """
    if not isinstance(config, dict):
        raise ValueError("Config must be a dictionary, got {}".format(type(config).__name__))

    if 'agents' not in config:
        raise ValueError("Config must contain 'agents' key")

    agents_list = config['agents']
    if not isinstance(agents_list, list):
        raise ValueError(
            "'agents' must be a list, got {}".format(type(agents_list).__name__)
        )

    schemas = []  # type: List[AgentSchema]
    for i, agent_dict in enumerate(agents_list):
        if not isinstance(agent_dict, dict):
            logger.warning(
                "Skipping agent at index %d: expected dict, got %s",
                i, type(agent_dict).__name__
            )
            continue

        # Auto-fill 'prompt' for built-in agents when omitted in user config
        agent_name = agent_dict.get('name', '')
        if agent_name in _BUILTIN_PROMPT_NAMES and not agent_dict.get('prompt'):
            agent_dict = dict(agent_dict)  # shallow copy to avoid mutating original
            agent_dict['prompt'] = _get_builtin_prompt(agent_name)
            logger.debug(
                "Auto-filled prompt for built-in agent '%s' at index %d",
                agent_name, i
            )

        try:
            schema = validate_agent_schema(agent_dict)
            schemas.append(schema)
        except Exception as e:
            agent_name_display = agent_dict.get('name', '<unknown>')
            logger.warning(
                "Skipping agent '%s' at index %d: %s",
                agent_name_display, i, str(e)
            )

    return schemas


# ---------------------------------------------------------------------------
# Config Merging
# ---------------------------------------------------------------------------


def merge_agent_configs(configs):
    # type: (List[Dict[str, Any]]) -> Dict[str, AgentSchema]
    """Merge multiple config layers into a unified agent dictionary.

    Later configs override earlier configs for agents with the same name.
    This enables a layered configuration approach:
    1. Base config (defaults)
    2. User config (overrides)
    3. Project config (project-specific overrides)

    Args:
        configs: List of config dictionaries, each with 'agents' key.
                 Later entries take precedence.

    Returns:
        Dict mapping agent name to AgentSchema.
    """
    merged = {}  # type: Dict[str, AgentSchema]

    for config in configs:
        try:
            agents = load_agents_from_dict(config)
            for schema in agents:
                merged[schema.name] = schema
        except ValueError as e:
            logger.warning("Skipping invalid config layer: %s", str(e))

    return merged
