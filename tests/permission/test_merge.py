"""Tests for merge strategies: OVERRIDE, APPEND, INTERSECT."""

import unittest

from berserker.permission import (
    ALLOWED,
    DENIED,
    NEEDS_ASK,
    AgentPermissionRule,
    PermissionRuleset,
    MergeStrategy,
    merge_permissions,
)


class TestMergeStrategyEnum(unittest.TestCase):
    """Test MergeStrategy enum values."""

    def test_override_value(self):
        self.assertEqual("override", MergeStrategy.OVERRIDE.value)

    def test_append_value(self):
        self.assertEqual("append", MergeStrategy.APPEND.value)

    def test_intersect_value(self):
        self.assertEqual("intersect", MergeStrategy.INTERSECT.value)


class TestMergeOverride(unittest.TestCase):
    """Test OVERRIDE merge strategy."""

    def test_override_replaces_base_rule(self):
        """Override rules replace base rules with same (tool, agent)."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", agent=None))
        base.add_rule(AgentPermissionRule(tool="write", action="ask", agent=None))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="deny", agent=None))

        result = merge_permissions(base, override, MergeStrategy.OVERRIDE)

        # read should be denied (from override), write should still be ask (from base)
        self.assertEqual(DENIED, result.check("read"))
        self.assertEqual(NEEDS_ASK, result.check("write"))

    def test_override_keeps_non_overridden_base_rules(self):
        """Base rules not in override are kept."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow"))
        base.add_rule(AgentPermissionRule(tool="ls", action="allow"))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="deny"))

        result = merge_permissions(base, override, MergeStrategy.OVERRIDE)
        self.assertEqual(2, len(result))
        self.assertEqual(DENIED, result.check("read"))
        self.assertEqual(ALLOWED, result.check("ls"))

    def test_override_agent_specific(self):
        """Override replaces base rule with same (tool, agent) combination."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", agent="coder"))
        base.add_rule(AgentPermissionRule(tool="read", action="deny"))  # global

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="deny", agent="coder"))

        result = merge_permissions(base, override, MergeStrategy.OVERRIDE)

        # coder's read should be denied (override replaced base's allow)
        self.assertEqual(DENIED, result.check("read", agent="coder"))
        # global rule still denies
        self.assertEqual(DENIED, result.check("read"))

    def test_override_empty_base(self):
        """Override with empty base returns only override rules."""
        base = PermissionRuleset()
        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="allow"))

        result = merge_permissions(base, override, MergeStrategy.OVERRIDE)
        self.assertEqual(1, len(result))
        self.assertEqual(ALLOWED, result.check("read"))

    def test_override_empty_override(self):
        """Empty override returns all base rules."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow"))
        override = PermissionRuleset()

        result = merge_permissions(base, override, MergeStrategy.OVERRIDE)
        self.assertEqual(1, len(result))
        self.assertEqual(ALLOWED, result.check("read"))

    def test_override_both_empty(self):
        """Both empty returns empty ruleset."""
        base = PermissionRuleset()
        override = PermissionRuleset()

        result = merge_permissions(base, override, MergeStrategy.OVERRIDE)
        self.assertEqual(0, len(result))


class TestMergeAppend(unittest.TestCase):
    """Test APPEND merge strategy."""

    def test_append_includes_all_rules(self):
        """All rules from both rulesets are included."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow"))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="write", action="deny"))

        result = merge_permissions(base, override, MergeStrategy.APPEND)
        self.assertEqual(2, len(result))

    def test_append_boosts_override_priority(self):
        """Override rules get priority + 1000."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", priority=0))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="deny", priority=0))

        result = merge_permissions(base, override, MergeStrategy.APPEND)

        # Override rule should win due to boosted priority
        self.assertEqual(DENIED, result.check("read"))

    def test_append_preserves_base_priority(self):
        """Base rules keep their original priority."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", priority=50))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="write", action="deny", priority=10))

        result = merge_permissions(base, override, MergeStrategy.APPEND)

        # read should still be allowed (base rule at priority 50)
        self.assertEqual(ALLOWED, result.check("read"))
        # write should be denied (override rule at priority 1010)
        self.assertEqual(DENIED, result.check("write"))

    def test_append_empty_base(self):
        """Append with empty base returns only override rules (boosted)."""
        base = PermissionRuleset()
        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="allow", priority=5))

        result = merge_permissions(base, override, MergeStrategy.APPEND)
        self.assertEqual(1, len(result))
        # Priority should be 1005
        rules = result.get_rules()
        self.assertEqual(1005, rules[0].priority)

    def test_append_empty_override(self):
        """Empty override returns all base rules unchanged."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", priority=10))
        override = PermissionRuleset()

        result = merge_permissions(base, override, MergeStrategy.APPEND)
        self.assertEqual(1, len(result))
        rules = result.get_rules()
        self.assertEqual(10, rules[0].priority)

    def test_append_both_empty(self):
        """Both empty returns empty ruleset."""
        base = PermissionRuleset()
        override = PermissionRuleset()

        result = merge_permissions(base, override, MergeStrategy.APPEND)
        self.assertEqual(0, len(result))


class TestMergeIntersect(unittest.TestCase):
    """Test INTERSECT merge strategy."""

    def test_intersect_keeps_common_rules(self):
        """Only rules existing in both (same tool+agent+path) are kept."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", path="*.py"))
        base.add_rule(AgentPermissionRule(tool="write", action="ask", path="*.txt"))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="deny", path="*.py"))
        override.add_rule(AgentPermissionRule(tool="edit", action="ask"))

        result = merge_permissions(base, override, MergeStrategy.INTERSECT)

        # Only read+*.py exists in both
        self.assertEqual(1, len(result))
        # Base rule's action is used (allow)
        self.assertEqual(ALLOWED, result.check("read", path="test.py"))

    def test_intersect_no_common_rules(self):
        """No common rules returns empty ruleset."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow"))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="write", action="deny"))

        result = merge_permissions(base, override, MergeStrategy.INTERSECT)
        self.assertEqual(0, len(result))

    def test_intersect_with_agent(self):
        """Intersect considers agent in the key."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", agent="coder"))
        base.add_rule(AgentPermissionRule(tool="read", action="deny"))  # global

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="deny", agent="coder"))

        result = merge_permissions(base, override, MergeStrategy.INTERSECT)

        # Only read+coder exists in both
        self.assertEqual(1, len(result))
        # Base rule's action is used (allow)
        self.assertEqual(ALLOWED, result.check("read", agent="coder"))

    def test_intersect_with_path(self):
        """Intersect considers path in the key."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", path="*.py"))
        base.add_rule(AgentPermissionRule(tool="read", action="deny", path="*.txt"))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="deny", path="*.py"))

        result = merge_permissions(base, override, MergeStrategy.INTERSECT)

        # Only read+*.py exists in both
        self.assertEqual(1, len(result))
        self.assertEqual(ALLOWED, result.check("read", path="test.py"))
        self.assertEqual(NEEDS_ASK, result.check("read", path="test.txt"))

    def test_intersect_empty_base(self):
        """Empty base returns empty result."""
        base = PermissionRuleset()
        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="allow"))

        result = merge_permissions(base, override, MergeStrategy.INTERSECT)
        self.assertEqual(0, len(result))

    def test_intersect_empty_override(self):
        """Empty override returns empty result."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow"))
        override = PermissionRuleset()

        result = merge_permissions(base, override, MergeStrategy.INTERSECT)
        self.assertEqual(0, len(result))

    def test_intersect_both_empty(self):
        """Both empty returns empty ruleset."""
        base = PermissionRuleset()
        override = PermissionRuleset()

        result = merge_permissions(base, override, MergeStrategy.INTERSECT)
        self.assertEqual(0, len(result))

    def test_intersect_identical_rulesets(self):
        """Identical rulesets return all rules."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow"))
        base.add_rule(AgentPermissionRule(tool="write", action="deny"))

        override = PermissionRuleset()
        override.add_rule(AgentPermissionRule(tool="read", action="allow"))
        override.add_rule(AgentPermissionRule(tool="write", action="deny"))

        result = merge_permissions(base, override, MergeStrategy.INTERSECT)
        self.assertEqual(2, len(result))
        self.assertEqual(ALLOWED, result.check("read"))
        self.assertEqual(DENIED, result.check("write"))


class TestPermissionRulesetMerge(unittest.TestCase):
    """Test PermissionRuleset.merge() method (uses APPEND by default)."""

    def test_merge_uses_append(self):
        """ruleset.merge() uses APPEND strategy."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow", priority=0))

        other = PermissionRuleset()
        other.add_rule(AgentPermissionRule(tool="read", action="deny", priority=0))

        result = base.merge(other)

        # Should use APPEND, so override wins due to priority boost
        self.assertEqual(DENIED, result.check("read"))

    def test_merge_returns_new_ruleset(self):
        """merge() returns a new ruleset, doesn't modify originals."""
        base = PermissionRuleset()
        base.add_rule(AgentPermissionRule(tool="read", action="allow"))

        other = PermissionRuleset()
        other.add_rule(AgentPermissionRule(tool="write", action="deny"))

        result = base.merge(other)

        # Originals unchanged
        self.assertEqual(1, len(base))
        self.assertEqual(1, len(other))
        # Result has both
        self.assertEqual(2, len(result))

    def test_merge_empty_rulesets(self):
        """Merging empty rulesets returns empty ruleset."""
        base = PermissionRuleset()
        other = PermissionRuleset()

        result = base.merge(other)
        self.assertEqual(0, len(result))


if __name__ == "__main__":
    unittest.main()
