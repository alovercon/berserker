"""
Core path resolution logic with XDG Base Directory style (Windows adapted).

Provides platform-aware path resolution for config, data, cache, state, log,
and binary directories. Environment variable overrides take highest priority.

Python 3.8.10 compatible.
"""

import os
import platform


def _home():
    """Return the user's home directory (Python 3.8 safe)."""
    return os.path.expanduser("~")


def _is_windows():
    """Return True if running on Windows."""
    return platform.system() == "Windows"


def _resolve(env_key, win_path, unix_path):
    """
    Resolve a directory path with the following priority:
    1. Environment variable override (BERSERKER_*)
    2. Platform-specific default path
    3. Auto-create the directory if it doesn't exist

    Args:
        env_key: Environment variable name for override (e.g. BERSERKER_CONFIG_DIR)
        win_path: Default path on Windows
        unix_path: Default path on Linux/macOS

    Returns:
        Resolved and ensured directory path as a string.
    """
    # Highest priority: environment variable override
    env_value = os.environ.get(env_key)
    if env_value:
        return env_value

    # Platform-specific default
    if _is_windows():
        path = win_path
    else:
        path = unix_path

    # Auto-create directory
    os.makedirs(path, exist_ok=True)

    return path


def get_config_dir():
    """
    Return the directory for configuration files.

    Windows: ~/.config/berserker/
    Linux/macOS: $XDG_CONFIG_HOME/berserker/ or ~/.config/berserker/
    Override: BERSERKER_CONFIG_DIR
    """
    if _is_windows():
        win_path = os.path.join(_home(), ".config", "berserker")
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.join(_home(), ".config"))
        win_path = None  # unused on Unix
        unix_path = os.path.join(xdg_config, "berserker")
        return _resolve("BERSERKER_CONFIG_DIR", "", unix_path)

    return _resolve("BERSERKER_CONFIG_DIR", win_path, "")


def get_data_dir():
    """
    Return the directory for data files (databases, etc.).

    Windows: %LOCALAPPDATA%\\berserker\\
    Linux/macOS: $XDG_DATA_HOME/berserker/ or ~/.local/share/berserker/
    Override: BERSERKER_DATA_DIR
    """
    if _is_windows():
        local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(_home(), "AppData", "Local"))
        win_path = os.path.join(local_appdata, "berserker")
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME", os.path.join(_home(), ".local", "share"))
        unix_path = os.path.join(xdg_data, "berserker")
        return _resolve("BERSERKER_DATA_DIR", "", unix_path)

    return _resolve("BERSERKER_DATA_DIR", win_path, "")


def get_cache_dir():
    """
    Return the directory for cache files.

    Windows: %LOCALAPPDATA%\\berserker\\cache\\
    Linux/macOS: $XDG_CACHE_HOME/berserker/ or ~/.cache/berserker/
    Override: BERSERKER_CACHE_DIR
    """
    if _is_windows():
        local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(_home(), "AppData", "Local"))
        win_path = os.path.join(local_appdata, "berserker", "cache")
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME", os.path.join(_home(), ".cache"))
        unix_path = os.path.join(xdg_cache, "berserker")
        return _resolve("BERSERKER_CACHE_DIR", "", unix_path)

    return _resolve("BERSERKER_CACHE_DIR", win_path, "")


def get_state_dir():
    """
    Return the directory for state files.

    Windows: %LOCALAPPDATA%\\berserker\\state\\
    Linux/macOS: $XDG_STATE_HOME/berserker/ or ~/.local/state/berserker/
    Override: BERSERKER_STATE_DIR
    """
    if _is_windows():
        local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(_home(), "AppData", "Local"))
        win_path = os.path.join(local_appdata, "berserker", "state")
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME", os.path.join(_home(), ".local", "state"))
        unix_path = os.path.join(xdg_state, "berserker")
        return _resolve("BERSERKER_STATE_DIR", "", unix_path)

    return _resolve("BERSERKER_STATE_DIR", win_path, "")


def get_log_dir():
    """
    Return the directory for log files.

    Windows: %LOCALAPPDATA%\\berserker\\log\\
    Linux/macOS: $XDG_STATE_HOME/berserker/log/ or ~/.local/state/berserker/log/
    Override: BERSERKER_LOG_DIR
    """
    if _is_windows():
        local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(_home(), "AppData", "Local"))
        win_path = os.path.join(local_appdata, "berserker", "log")
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME", os.path.join(_home(), ".local", "state"))
        unix_path = os.path.join(xdg_state, "berserker", "log")
        return _resolve("BERSERKER_LOG_DIR", "", unix_path)

    return _resolve("BERSERKER_LOG_DIR", win_path, "")


def get_bin_dir():
    """
    Return the directory for binary/executable files.

    Windows: %LOCALAPPDATA%\\berserker\\bin\\
    Linux/macOS: $XDG_BIN_HOME/ or ~/.local/bin/
    Override: BERSERKER_BIN_DIR
    """
    if _is_windows():
        local_appdata = os.environ.get("LOCALAPPDATA", os.path.join(_home(), "AppData", "Local"))
        win_path = os.path.join(local_appdata, "berserker", "bin")
    else:
        xdg_bin = os.environ.get("XDG_BIN_HOME", os.path.join(_home(), ".local", "bin"))
        unix_path = xdg_bin
        return _resolve("BERSERKER_BIN_DIR", "", unix_path)

    return _resolve("BERSERKER_BIN_DIR", win_path, "")
