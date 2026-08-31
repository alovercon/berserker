"""
berserker.config.loader — Multi-layer config loading and merging.

Layer merge order (later overrides earlier):
  0. defaults  — built-in default values (subagent_timeout: 3600)
  1. managed   — enterprise/MDM settings (NOT implemented, returns {})
  2. global    — {config_dir}/config.json
  3. custom    — user-specified config file path
  4. project   — {cwd}/berserker.json or {cwd}/.berserker/config.json
  5. dotfile   — {cwd}/.berserker/config.json (if not already loaded as project)
  6. inline    — dict passed directly to load_config()

Python 3.8.10 compatible.
"""

import copy
import os
from typing import Any, Dict

from berserker.config.jsonc import load_jsonc_file
from berserker.config.substitution import substitute_all
from berserker.paths import get_config_dir


# Default configuration values — lowest priority layer, overridden by all others.
DEFAULT_CONFIG = {
    "subagent_timeout": 3600,  # 1 hour for subagent execution (long-running tasks)
}


def deep_merge(base, override):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """
    Recursively merge two dicts.  Later (override) values win.

    - Nested dicts are merged recursively.
    - Lists are replaced entirely (not concatenated).
    - Scalar values from override replace base.

    Args:
        base: Base dict.
        override: Dict whose values take precedence.

    Returns:
        New dict with merged values.
    """
    result = copy.deepcopy(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


def _safe_load(path):
    """
    Load a JSONC file, returning {} on any error.

    Args:
        path: Filesystem path to a config file.

    Returns:
        Parsed dict, or {} if the file is missing or invalid.
    """
    if not os.path.isfile(path):
        return {}
    try:
        data = load_jsonc_file(path)
        if isinstance(data, dict):
            return data
        return {}
    except (OSError, IOError, ValueError):
        return {}


def _global_config():
    """
    Load the global config file from the config directory.

    Tries config.json first, then falls back to berserker.json / berserker.jsonc.

    Returns:
        Dict of global config values.
    """
    config_dir = get_config_dir()

    # Try config.json first
    candidate = os.path.join(config_dir, "config.json")
    result = _safe_load(candidate)
    if result:
        return result

    # Fallback: berserker.json
    candidate = os.path.join(config_dir, "berserker.json")
    result = _safe_load(candidate)
    if result:
        return result

    # Fallback: berserker.jsonc
    candidate = os.path.join(config_dir, "berserker.jsonc")
    return _safe_load(candidate)


def _project_config(cwd=None):
    """
    Load project-level config from the current working directory.

    Search order:
      1. {cwd}/berserker.json
      2. {cwd}/berserker.jsonc
      3. {cwd}/.berserker/config.json
      4. {cwd}/.berserker/config.jsonc

    Args:
        cwd: Working directory. Defaults to os.getcwd().

    Returns:
        Tuple of (config_dict, base_dir) where base_dir is the directory
        of the loaded config file (for {file:...} resolution).
    """
    if cwd is None:
        cwd = os.getcwd()

    candidates = [
        os.path.join(cwd, "berserker.json"),
        os.path.join(cwd, "berserker.jsonc"),
        os.path.join(cwd, ".berserker", "config.json"),
        os.path.join(cwd, ".berserker", "config.jsonc"),
    ]

    for path in candidates:
        if os.path.isfile(path):
            try:
                data = load_jsonc_file(path)
                if isinstance(data, dict):
                    return data, os.path.dirname(path)
            except (OSError, IOError, ValueError):
                continue

    return {}, cwd


def load_jsonc(path):
    """
    Parse a single JSONC file.

    Args:
        path: Filesystem path to a .json or .jsonc file.

    Returns:
        Parsed dict.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError: if the content is not valid JSONC.
    """
    return load_jsonc_file(path)


def load_config(
    config_path=None,  # type: str | None
    cwd=None,  # type: str | None
    inline=None,  # type: Dict[str, Any] | None
):
    # type: (...) -> Dict[str, Any]
    """
    Load and merge all applicable config layers.

    Merge order (later overrides earlier):
      0. defaults  — built-in defaults (subagent_timeout: 3600)
      1. managed   — always empty (not implemented)
      2. global    — {config_dir}/config.json
      3. custom    — config_path if provided
      4. project   — {cwd}/berserker.json[c] or {cwd}/.berserker/config.json[c]
      5. inline    — inline dict if provided

    After merging, applies {env:...} and {file:...} substitution.

    Args:
        config_path: Optional path to a custom config file.
        cwd: Working directory for project config resolution.
             Defaults to os.getcwd().
        inline: Optional dict of inline config values (highest priority).

    Returns:
        Fully merged and substituted config dict.
    """
    # Layer 1: defaults (lowest priority)
    result = copy.deepcopy(DEFAULT_CONFIG)  # type: Dict[str, Any]

    # Layer 2: managed (not implemented)
    # result stays as defaults

    # Layer 2: global
    result = deep_merge(result, _global_config())

    # Layer 3: custom config file
    if config_path is not None:
        custom_data = _safe_load(config_path)
        result = deep_merge(result, custom_data)

    # Layer 4: project config
    project_data, project_dir = _project_config(cwd)
    result = deep_merge(result, project_data)

    # Layer 5: inline overrides
    if inline is not None:
        result = deep_merge(result, inline)

    # Apply substitutions
    # Determine base_dir for {file:...} resolution
    base_dir = project_dir if project_data else (cwd or os.getcwd())
    result = substitute_all(result, base_dir=base_dir)

    return result
