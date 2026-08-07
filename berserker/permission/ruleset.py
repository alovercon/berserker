"""Agent-aware, priority-ordered, thread-safe permission ruleset.

Extends the basic PermissionRule with agent-specific rules, priority ordering,
and glob matching on both tool names and paths.

Compatible with Python 3.8.10+.
"""

from __future__ import annotations

import fnmatch
import threading
from typing import Any, Dict, List, Optional

from berserker.permission import (
    ALLOWED,
    DENIED,
    NEEDS_ASK,
    PermissionRule,
)

# Internal action mapping
_ACTION_TO_RESULT = {
    "allow": ALLOWED,
    "deny": DENIED,
    "ask": NEEDS_ASK,
}


class AgentPermissionRule(object):
    """An extended permission rule with agent and priority support.

    Attributes:
        tool: Tool name pattern (supports fnmatch glob).
        action: One of 'allow', 'deny', 'ask'.
        path: Optional file path pattern (supports fnmatch glob).
        agent: Optional agent name. None means global rule.
        priority: Integer priority. Higher values are evaluated first.
    """

    def __init__(
        self,
        tool,  # type: str
        action,  # type: str
        path=None,  # type: Optional[str]
        agent=None,  # type: Optional[str]
        priority=0,  # type: int
    ):
        # type: (...) -> None
        self.tool = tool
        self.action = action
        self.path = path
        self.agent = agent
        self.priority = priority

    def matches(self, tool, agent=None, path=None):
        # type: (str, Optional[str], Optional[str]) -> bool
        """Check if this rule matches the given tool, agent, and path.

        A rule matches if:
        - The tool name matches (fnmatch glob).
        - If the rule has an agent, it must match the provided agent.
          If the rule is global (agent=None), it matches any agent.
        - If the rule has a path pattern, the provided path must match.
          If the rule has no path pattern, it matches any path.
        """
        # Tool must match
        if not fnmatch.fnmatch(tool, self.tool):
            return False

        # Agent matching: agent-specific rules only match that agent
        if self.agent is not None:
            if agent is None or agent != self.agent:
                return False

        # Path matching
        if self.path is not None:
            if path is None:
                return False
            if not fnmatch.fnmatch(path, self.path):
                return False

        return True

    def __eq__(self, other):
        # type: (object) -> bool
        if not isinstance(other, AgentPermissionRule):
            return NotImplemented
        return (
            self.tool == other.tool
            and self.action == other.action
            and self.path == other.path
            and self.agent == other.agent
            and self.priority == other.priority
        )

    def __repr__(self):
        # type: () -> str
        return (
            "AgentPermissionRule(tool={!r}, action={!r}, path={!r}, "
            "agent={!r}, priority={!r})"
        ).format(self.tool, self.action, self.path, self.agent, self.priority)


class PermissionRuleset(object):
    """Agent-aware, priority-ordered, thread-safe permission ruleset.

    Rules are evaluated in order:
    1. Sorted by priority descending (higher priority first).
    2. Within the same priority, agent-specific rules before global rules.
    3. First matching rule wins.
    """

    def __init__(self):
        # type: () -> None
        self._rules = []  # type: List[AgentPermissionRule]
        self._lock = threading.RLock()

    def add_rule(self, rule):
        # type: (AgentPermissionRule) -> None
        """Add a rule to the ruleset.

        Args:
            rule: An AgentPermissionRule instance.
        """
        with self._lock:
            self._rules.append(rule)

    def remove_rule(self, tool, path=None, agent=None):
        # type: (str, Optional[str], Optional[str]) -> int
        """Remove rules matching the given criteria.

        Args:
            tool: Tool name pattern to match (fnmatch glob).
            path: Optional path pattern to match.
            agent: Optional agent name to match.

        Returns:
            Number of rules removed.
        """
        with self._lock:
            original_count = len(self._rules)
            self._rules = [
                r
                for r in self._rules
                if not (
                    fnmatch.fnmatch(r.tool, tool)
                    and (path is None or r.path == path)
                    and (agent is None or r.agent == agent)
                )
            ]
            return original_count - len(self._rules)

    def check(self, tool, agent=None, path=None):
        # type: (str, Optional[str], Optional[str]) -> str
        """Check permission for a tool, optionally scoped to agent and path.

        Args:
            tool: Tool name (supports fnmatch glob matching against rules).
            agent: Optional agent name. Agent-specific rules take precedence
                   over global rules at the same priority level.
            path: Optional file path (supports fnmatch glob matching).

        Returns:
            One of ALLOWED, DENIED, or NEEDS_ASK.
        """
        with self._lock:
            # Sort rules: priority desc, then agent-specific before global
            sorted_rules = sorted(
                self._rules,
                key=lambda r: (r.priority, 1 if r.agent is not None else 0),
                reverse=True,
            )

            for rule in sorted_rules:
                if rule.matches(tool, agent=agent, path=path):
                    return _ACTION_TO_RESULT.get(rule.action, NEEDS_ASK)

            return NEEDS_ASK

    def load_from_config(self, config):
        # type: (Dict[str, Any]) -> None
        """Load rules from a configuration dictionary.

        Expected format:
            {
                "permissions": [
                    {
                        "tool": "read",
                        "action": "allow",
                        "path": "*.py",       # optional
                        "agent": "coder",     # optional
                        "priority": 10        # optional, default 0
                    },
                ]
            }

        Args:
            config: Dictionary with a "permissions" key containing a list of
                    rule dictionaries.
        """
        permissions = config.get("permissions", [])  # type: List[Dict[str, Any]]
        with self._lock:
            for entry in permissions:
                tool = entry.get("tool", "")  # type: str
                action = entry.get("action", "ask")  # type: str
                path = entry.get("path", None)  # type: Optional[str]
                agent = entry.get("agent", None)  # type: Optional[str]
                priority = entry.get("priority", 0)  # type: int
                self._rules.append(
                    AgentPermissionRule(
                        tool=tool,
                        action=action,
                        path=path,
                        agent=agent,
                        priority=priority,
                    )
                )

    def merge(self, other):
        # type: (PermissionRuleset) -> PermissionRuleset
        """Merge this ruleset with another, returning a new ruleset.

        The merged ruleset contains all rules from both rulesets.
        Rules from 'other' get their priority increased by 1000 to ensure
        they take precedence over rules from 'self'.

        Args:
            other: Another PermissionRuleset to merge with.

        Returns:
            A new PermissionRuleset containing merged rules.
        """
        from berserker.permission.merge import (
            MergeStrategy,
            merge_permissions,
        )

        return merge_permissions(self, other, MergeStrategy.APPEND)

    def get_rules(self):
        # type: () -> List[AgentPermissionRule]
        """Return a copy of the rules list.

        Returns:
            A shallow copy of the internal rules list.
        """
        with self._lock:
            return list(self._rules)

    def __len__(self):
        # type: () -> int
        """Return the number of rules in the ruleset."""
        with self._lock:
            return len(self._rules)

    def __repr__(self):
        # type: () -> str
        with self._lock:
            return "PermissionRuleset(rules={!r})".format(self._rules)
