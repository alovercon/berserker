"""
berserker.config.substitution — Variable and file-content substitution.

Supports two patterns inside string values:
  {env:VAR_NAME}  → os.environ.get('VAR_NAME', '')
  {file:path}     → content of file at path (relative to base_dir)

Recursively processes nested dicts and lists.
Python 3.8.10 compatible.
"""

import os
import re

# Pattern: {env:VAR_NAME} or {file:relative/path}
_SUB_RE = re.compile(r"\{(env|file):([^}]+)\}")


def _substitute_value(value, base_dir):
    """
    Substitute all {env:...} and {file:...} patterns in a single string.

    Args:
        value: A string that may contain substitution patterns.
        base_dir: Base directory for resolving {file:...} paths.

    Returns:
        String with all patterns replaced.
    """

    def _replacer(match):
        kind = match.group(1)
        key = match.group(2)

        if kind == "env":
            return os.environ.get(key, "")

        if kind == "file":
            file_path = os.path.join(base_dir, key) if base_dir else key
            try:
                with open(file_path, "r", encoding="utf-8") as fh:
                    return fh.read()
            except (OSError, IOError):
                return ""

        return match.group(0)

    return _SUB_RE.sub(_replacer, value)


def _walk(obj, base_dir):
    """
    Recursively walk a config structure and substitute patterns in strings.

    Args:
        obj: Any JSON-like value (dict, list, str, int, etc.).
        base_dir: Base directory for {file:...} resolution.

    Returns:
        New structure with substitutions applied.
    """
    if isinstance(obj, dict):
        return {k: _walk(v, base_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(item, base_dir) for item in obj]
    if isinstance(obj, str):
        return _substitute_value(obj, base_dir)
    return obj


def substitute_env(config_dict):
    """
    Replace all {env:VAR_NAME} patterns in a config dict.

    Only string values are processed. Nested dicts and lists are
    traversed recursively.

    Args:
        config_dict: Config dict that may contain {env:...} patterns.

    Returns:
        New dict with all env patterns replaced.
    """
    return _walk(config_dict, base_dir=None)


def substitute_files(config_dict, base_dir):
    """
    Replace all {file:path} patterns in a config dict.

    File paths are resolved relative to base_dir.

    Args:
        config_dict: Config dict that may contain {file:...} patterns.
        base_dir: Base directory for resolving relative file paths.

    Returns:
        New dict with all file patterns replaced.
    """
    return _walk(config_dict, base_dir=base_dir)


def substitute_all(config_dict, base_dir=None):
    """
    Apply both env and file substitution in a single pass.

    Args:
        config_dict: Config dict with substitution patterns.
        base_dir: Base directory for {file:...} resolution.

    Returns:
        New dict with all patterns replaced.
    """
    return _walk(config_dict, base_dir=base_dir)
