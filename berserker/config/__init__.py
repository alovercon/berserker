"""
berserker.config — Multi-layer configuration system.

Public API:
    load_jsonc(path)          — Parse a single JSONC file
    load_config(config_path)  — Load and merge all applicable config layers
    deep_merge(base, override) — Recursively merge two dicts
    substitute_env(config)    — Replace {env:VAR} patterns
    substitute_files(config, base_dir) — Replace {file:path} patterns
    substitute_all(config, base_dir)   — Apply both substitutions
"""

from berserker.config.jsonc import (
    load_jsonc_file,
    strip_comments,
    load_jsonc,
)
from berserker.config.loader import (
    deep_merge,
    load_config,
)
from berserker.config.substitution import (
    substitute_env,
    substitute_files,
    substitute_all,
)

__all__ = [
    "load_jsonc",
    "load_jsonc_file",
    "load_config",
    "deep_merge",
    "substitute_env",
    "substitute_files",
    "substitute_all",
    "strip_comments",
]
