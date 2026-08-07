"""Rules-based permission checking system for berserker tools.

Provides glob path matching, default permissions, and caching.
Compatible with Python 3.8.10+.
"""

from __future__ import annotations

import fnmatch
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# Permission result constants
ALLOWED = "allowed"
DENIED = "denied"
NEEDS_ASK = "needs_ask"


class PermissionResult(object):
    """Enum-like class for permission check results."""

    ALLOWED = ALLOWED
    DENIED = DENIED
    NEEDS_ASK = NEEDS_ASK


class PermissionRule(object):
    """A single permission rule for a tool action."""

    def __init__(self, tool_name, action, path_pattern=None):
        # type: (str, str, Optional[str]) -> None
        self.tool_name = tool_name  # type: str
        self.action = action  # type: str  # 'allow', 'deny', or 'ask'
        self.path_pattern = path_pattern  # type: Optional[str]


# Default permission mappings: tool_name -> action
_DEFAULT_PERMISSIONS = {
    "read": "allow",
    "ls": "allow",
    "glob": "allow",
    "grep": "allow",
    "write": "ask",
    "edit": "ask",
    "multiedit": "ask",
    "apply_patch": "ask",
    "bash": "ask",
    "task": "ask",
    # File operation tools
    "mkdir": "ask",
    "rmdir": "ask",
    "mv": "ask",
    "cp": "ask",
    "rm": "ask",
    "touch": "ask",
}

_ACTION_TO_RESULT = {
    "allow": ALLOWED,
    "deny": DENIED,
    "ask": NEEDS_ASK,
}


def _build_default_rules():
    # type: () -> List[PermissionRule]
    """Build the default permission rules list."""
    rules = []  # type: List[PermissionRule]
    for tool_name, action in _DEFAULT_PERMISSIONS.items():
        rules.append(PermissionRule(tool_name=tool_name, action=action))
    return rules


class PermissionChecker(object):
    """Rules-based permission checker with glob path matching and caching."""

    def __init__(self, rules=None):
        # type: (Optional[List[PermissionRule]]) -> None
        """Initialize with optional list of PermissionRule objects.

        If rules is None, default rules are loaded.
        """
        self._lock = threading.Lock()
        if rules is None:
            self._rules = _build_default_rules()  # type: List[PermissionRule]
        else:
            self._rules = list(rules)
        self._cache = OrderedDict()  # type: OrderedDict[Tuple[str, Optional[str]], str]
        self._cache_maxsize = 500

    def check(self, tool_name, args=None, path=None):
        # type: (str, Optional[Any], Optional[str]) -> str
        """Check if a tool action is allowed.

        Args:
            tool_name: Name of the tool to check.
            args: Optional tool arguments (currently unused in matching).
            path: Optional file path for path-based permission checks.

        Returns:
            One of ALLOWED, DENIED, or NEEDS_ASK.

        Logic:
            - If a cache hit exists for (tool_name, path), return cached result.
            - Iterate rules in order; first matching rule wins.
            - A rule matches if tool_name matches and (path_pattern is None or path matches glob).
            - If no rule matches, return NEEDS_ASK.
        """
        cache_key = (tool_name, path)  # type: Tuple[str, Optional[str]]
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        result = NEEDS_ASK  # type: str

        for rule in self._rules:
            if rule.tool_name != tool_name:
                continue
            if rule.path_pattern is not None:
                if path is None:
                    continue
                if not fnmatch.fnmatch(path, rule.path_pattern):
                    continue
            # First matching rule wins
            result = _ACTION_TO_RESULT.get(rule.action, NEEDS_ASK)
            break

        with self._lock:
            self._cache[cache_key] = result
            if len(self._cache) > self._cache_maxsize:
                self._cache.popitem(last=False)  # Remove oldest (FIFO eviction)
        return result

    def add_rule(self, rule):
        # type: (PermissionRule) -> None
        """Add a rule to the end of the rules list and clear cache."""
        with self._lock:
            self._rules.append(rule)
            self.clear_cache()

    def load_from_config(self, config_dict):
        # type: (Dict[str, Any]) -> None
        """Load rules from a config dict.

        Expected format:
            {
                'permissions': [
                    {'tool': 'read', 'action': 'allow'},
                    {'tool': 'write', 'action': 'ask', 'path': '*.py'},
                ]
            }

        User config rules are inserted at the BEGINNING of the rules list
        so they take precedence over default rules (first matching rule wins).
        """
        permissions = config_dict.get("permissions", [])  # type: List[Dict[str, Any]]
        # Insert at beginning so user rules override defaults
        new_rules = []  # type: List[PermissionRule]
        for entry in permissions:
            tool = entry.get("tool", "")  # type: str
            action = entry.get("action", "ask")  # type: str
            path_pattern = entry.get("path", None)  # type: Optional[str]
            new_rules.append(
                PermissionRule(tool_name=tool, action=action, path_pattern=path_pattern)
            )
        with self._lock:
            if new_rules:
                self._rules = new_rules + self._rules
            self.clear_cache()

    def clear_cache(self):
        # type: () -> None
        """Clear the permission cache."""
        self._cache.clear()


# Singleton instance with default rules pre-loaded
permission_checker = PermissionChecker()

# Re-export ruleset module classes
from berserker.permission.ruleset import (
    AgentPermissionRule,
    PermissionRuleset,
)
from berserker.permission.merge import (
    MergeStrategy,
    merge_permissions,
)

__all__ = [
    # Existing exports (backward compatible)
    "PermissionChecker",
    "PermissionRule",
    "PermissionResult",
    "permission_checker",
    "ALLOWED",
    "DENIED",
    "NEEDS_ASK",
    # New exports
    "AgentPermissionRule",
    "PermissionRuleset",
    "MergeStrategy",
    "merge_permissions",
]
