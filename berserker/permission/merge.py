"""Merge strategies for combining PermissionRulesets.

Provides three strategies: OVERRIDE, APPEND, and INTERSECT.

Compatible with Python 3.8.10+.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from berserker.permission.ruleset import AgentPermissionRule, PermissionRuleset


class MergeStrategy(Enum):
    """Strategy for merging two PermissionRulesets.

    Members:
        OVERRIDE: Rules from the override ruleset replace base rules
                  with the same tool+agent combination.
        APPEND: All rules from both rulesets are included. Override rules
                get higher priority (priority + 1000).
        INTERSECT: Only rules that exist in both rulesets are kept.
                   The base rule's action is used.
    """

    OVERRIDE = "override"
    APPEND = "append"
    INTERSECT = "intersect"


def merge_permissions(base, override, strategy):
    # type: (PermissionRuleset, PermissionRuleset, MergeStrategy) -> PermissionRuleset
    """Merge two PermissionRulesets using the specified strategy.

    Args:
        base: The base ruleset.
        override: The override ruleset.
        strategy: The MergeStrategy to use.

    Returns:
        A new PermissionRuleset with merged rules.
    """
    from berserker.permission.ruleset import (
        AgentPermissionRule,
        PermissionRuleset,
    )

    result = PermissionRuleset()

    if strategy == MergeStrategy.OVERRIDE:
        _merge_override(base, override, result)
    elif strategy == MergeStrategy.APPEND:
        _merge_append(base, override, result)
    elif strategy == MergeStrategy.INTERSECT:
        _merge_intersect(base, override, result)

    return result


def _merge_override(base, override, result):
    # type: (PermissionRuleset, PermissionRuleset, PermissionRuleset) -> None
    """OVERRIDE: override rules replace base rules with same tool+agent."""
    base_rules = base.get_rules()
    override_rules = override.get_rules()

    # Build a set of (tool, agent) keys from override rules
    override_keys = set()
    for rule in override_rules:
        override_keys.add((rule.tool, rule.agent))

    # Add base rules that are NOT overridden
    for rule in base_rules:
        if (rule.tool, rule.agent) not in override_keys:
            result.add_rule(rule)

    # Add all override rules
    for rule in override_rules:
        result.add_rule(rule)


def _merge_append(base, override, result):
    # type: (PermissionRuleset, PermissionRuleset, PermissionRuleset) -> None
    """APPEND: all rules from both, override rules get higher priority."""
    from berserker.permission.ruleset import AgentPermissionRule

    # Add all base rules as-is
    for rule in base.get_rules():
        result.add_rule(rule)

    # Add override rules with boosted priority
    for rule in override.get_rules():
        boosted_rule = AgentPermissionRule(
            tool=rule.tool,
            action=rule.action,
            path=rule.path,
            agent=rule.agent,
            priority=rule.priority + 1000,
        )
        result.add_rule(boosted_rule)


def _merge_intersect(base, override, result):
    # type: (PermissionRuleset, PermissionRuleset, PermissionRuleset) -> None
    """INTERSECT: only rules that exist in both (same tool+agent+path)."""
    base_rules = base.get_rules()
    override_rules = override.get_rules()

    # Build a set of (tool, agent, path) keys from override rules
    override_keys = set()
    for rule in override_rules:
        override_keys.add((rule.tool, rule.agent, rule.path))

    # Add base rules that also exist in override
    for rule in base_rules:
        if (rule.tool, rule.agent, rule.path) in override_keys:
            result.add_rule(rule)
