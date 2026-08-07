"""GUI state persistence.

Stores and retrieves GUI-specific state such as the last-used workspace.
State is saved to {state_dir}/gui.json.

Python 3.8.10 compatible.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from berserker.paths import get_state_dir

_STATE_FILE = "gui.json"


def _get_state_path():
    # type: () -> str
    """Return the full path to the GUI state file."""
    return os.path.join(get_state_dir(), _STATE_FILE)


def load_gui_state():
    # type: () -> Dict[str, Any]
    """Load the GUI state dictionary. Returns empty dict if file doesn't exist or is invalid."""
    path = _get_state_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, IOError, OSError):
        return {}


def save_gui_state(state):
    # type: (Dict[str, Any]) -> None
    """Save the GUI state dictionary to disk."""
    path = _get_state_path()
    state_dir = os.path.dirname(path)
    os.makedirs(state_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def get_last_workspace():
    # type: () -> Optional[str]
    """Return the last workspace path, or None if never set or directory no longer exists."""
    state = load_gui_state()
    workspace = state.get("last_workspace")
    if workspace and os.path.isdir(workspace):
        return workspace
    return None


def set_last_workspace(workspace):
    # type: (str) -> None
    """Save the given workspace as the last-used workspace."""
    state = load_gui_state()
    state["last_workspace"] = os.path.abspath(workspace)
    save_gui_state(state)
