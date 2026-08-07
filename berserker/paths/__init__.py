"""
berserker.paths - XDG Base Directory style path resolution (Windows adapted).

Provides platform-aware path resolution for application directories:
- get_config_dir()  — configuration files
- get_data_dir()    — data files (databases, etc.)
- get_cache_dir()   — cache files
- get_state_dir()   — state files
- get_log_dir()     — log files
- get_bin_dir()     — binary/executable files

Environment variable overrides (highest priority):
- BERSERKER_CONFIG_DIR, BERSERKER_DATA_DIR, BERSERKER_CACHE_DIR,
  BERSERKER_STATE_DIR, BERSERKER_LOG_DIR, BERSERKER_BIN_DIR
"""

from berserker.paths.resolver import (
    get_config_dir,
    get_data_dir,
    get_cache_dir,
    get_state_dir,
    get_log_dir,
    get_bin_dir,
)

__all__ = [
    "get_config_dir",
    "get_data_dir",
    "get_cache_dir",
    "get_state_dir",
    "get_log_dir",
    "get_bin_dir",
]
