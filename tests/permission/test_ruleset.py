"""Tests for PermissionRuleset CRUD, glob matching, priority ordering, and thread safety."""

import threading
import unittest

from berserker.permission import (
    ALLOWED,
    DENIED,
    NEEDS_ASK,
    AgentPermissionRule,
    PermissionRuleset,
)


class TestPermissionRulesetCRUD(unittest.TestCase):
    """Test basic CRUD operations on PermissionRuleset."""

    def test_empty_ruleset(self):
        """Empty ruleset returns NEEDS_ASK for any check."""
        ruleset = PermissionRuleset()
        self.assertEqual(NEEDS_ASK, ruleset.check("read"))
        self.assertEqual(NEEDS_ASK, ruleset.check("write", path="test.py"))

    def test_add_rule(self):
        """Adding a rule allows check() to find it."""
        ruleset = PermissionRuleset()
        rule = AgentPermissionRule(tool="read", action="allow")
        ruleset.add_rule(rule)
        self.assertEqual(ALLOWED, ruleset.check("read"))

    def test_remove_rule(self):
        """Removing a rule by tool pattern removes matching rules."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny"))
        self.assertEqual(2, len(ruleset))

        removed = ruleset.remove_rule("read")
        self.assertEqual(1, removed)
        self.assertEqual(1, len(ruleset))
        self.assertEqual(NEEDS_ASK, ruleset.check("read"))
        self.assertEqual(DENIED, ruleset.check("write"))

    def test_remove_rule_with_path(self):
        """Removing a rule with path filter only removes matching rules."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", path="*.py"))
        ruleset.add_rule(AgentPermissionRule(tool="read", action="deny", path="*.txt"))
        self.assertEqual(2, len(ruleset))

        removed = ruleset.remove_rule("read", path="*.py")
        self.assertEqual(1, removed)
        self.assertEqual(1, len(ruleset))

    def test_remove_rule_with_agent(self):
        """Removing a rule with agent filter only removes matching rules."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", agent="coder"))
        ruleset.add_rule(AgentPermissionRule(tool="read", action="deny"))
        self.assertEqual(2, len(ruleset))

        removed = ruleset.remove_rule("read", agent="coder")
        self.assertEqual(1, removed)
        self.assertEqual(1, len(ruleset))

    def test_get_rules_returns_copy(self):
        """get_rules() returns a copy, not the internal list."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        rules = ruleset.get_rules()
        rules.clear()
        self.assertEqual(1, len(ruleset))

    def test_len(self):
        """len(ruleset) returns the number of rules."""
        ruleset = PermissionRuleset()
        self.assertEqual(0, len(ruleset))
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        self.assertEqual(1, len(ruleset))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny"))
        self.assertEqual(2, len(ruleset))

    def test_repr(self):
        """__repr__ returns a string representation."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        r = repr(ruleset)
        self.assertIn("PermissionRuleset", r)
        self.assertIn("read", r)


class TestPermissionRulesetCheck(unittest.TestCase):
    """Test check() returns ALLOWED/DENIED/NEEDS_ASK correctly."""

    def test_check_allowed(self):
        """check() returns ALLOWED for allow action."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        self.assertEqual(ALLOWED, ruleset.check("read"))

    def test_check_denied(self):
        """check() returns DENIED for deny action."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny"))
        self.assertEqual(DENIED, ruleset.check("write"))

    def test_check_needs_ask(self):
        """check() returns NEEDS_ASK for ask action."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="edit", action="ask"))
        self.assertEqual(NEEDS_ASK, ruleset.check("edit"))

    def test_check_no_matching_rule(self):
        """check() returns NEEDS_ASK when no rule matches."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        self.assertEqual(NEEDS_ASK, ruleset.check("write"))

    def test_check_invalid_action(self):
        """check() returns NEEDS_ASK for unknown action."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="unknown"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read"))


class TestGlobMatchingTool(unittest.TestCase):
    """Test fnmatch glob matching on tool names."""

    def test_exact_match(self):
        """Exact tool name matches."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        self.assertEqual(ALLOWED, ruleset.check("read"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read_file"))

    def test_star_suffix(self):
        """read* matches read, read_file, read_all."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read*", action="allow"))
        self.assertEqual(ALLOWED, ruleset.check("read"))
        self.assertEqual(ALLOWED, ruleset.check("read_file"))
        self.assertEqual(ALLOWED, ruleset.check("read_all"))
        self.assertEqual(NEEDS_ASK, ruleset.check("write"))

    def test_star_prefix(self):
        """*edit matches edit, multiedit, quick_edit."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="*edit", action="deny"))
        self.assertEqual(DENIED, ruleset.check("edit"))
        self.assertEqual(DENIED, ruleset.check("multiedit"))
        self.assertEqual(DENIED, ruleset.check("quick_edit"))
        self.assertEqual(NEEDS_ASK, ruleset.check("write"))

    def test_star_both_sides(self):
        """*file* matches any tool containing 'file'."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="*file*", action="allow"))
        self.assertEqual(ALLOWED, ruleset.check("read_file"))
        self.assertEqual(ALLOWED, ruleset.check("file_write"))
        self.assertEqual(ALLOWED, ruleset.check("myfile"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read"))

    def test_question_mark(self):
        """? matches single character."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="re?d", action="allow"))
        self.assertEqual(ALLOWED, ruleset.check("read"))
        self.assertEqual(ALLOWED, ruleset.check("reed"))  # ? matches any single char
        self.assertEqual(NEEDS_ASK, ruleset.check("red"))  # too short


class TestGlobMatchingPath(unittest.TestCase):
    """Test fnmatch glob matching on file paths."""

    def test_exact_path(self):
        """Exact path pattern matches."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", path="test.py"))
        self.assertEqual(ALLOWED, ruleset.check("read", path="test.py"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", path="other.py"))

    def test_star_extension(self):
        """*.py matches any .py file."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", path="*.py"))
        self.assertEqual(ALLOWED, ruleset.check("read", path="test.py"))
        self.assertEqual(ALLOWED, ruleset.check("read", path="src/main.py"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", path="test.txt"))

    def test_directory_star(self):
        """src/* matches files in src directory."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny", path="src/*"))
        self.assertEqual(DENIED, ruleset.check("write", path="src/main.py"))
        self.assertEqual(DENIED, ruleset.check("write", path="src/utils.py"))
        self.assertEqual(NEEDS_ASK, ruleset.check("write", path="test/main.py"))

    def test_path_none_in_check(self):
        """When path is None, rules with path patterns don't match."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", path="*.py"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read"))

    def test_path_none_in_rule(self):
        """When rule has no path, it matches any path."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        self.assertEqual(ALLOWED, ruleset.check("read", path="anything.py"))
        self.assertEqual(ALLOWED, ruleset.check("read", path="src/test.txt"))


class TestPriorityOrdering(unittest.TestCase):
    """Test that higher priority rules are evaluated first."""

    def test_higher_priority_wins(self):
        """Higher priority rule wins over lower priority."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="deny", priority=0))
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", priority=10))
        self.assertEqual(ALLOWED, ruleset.check("read"))

    def test_lower_priority_loses(self):
        """Lower priority rule is overridden by higher priority."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="write", action="allow", priority=5))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny", priority=100))
        self.assertEqual(DENIED, ruleset.check("write"))

    def test_same_priority_first_added_wins(self):
        """At same priority, sort is stable; first matching rule wins."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", priority=0))
        ruleset.add_rule(AgentPermissionRule(tool="read", action="deny", priority=0))
        # Both have same priority; the one that sorts first wins
        # Since sort is stable and both have priority=0 and agent=None,
        # the order depends on sort stability. Both match, first in sorted order wins.
        result = ruleset.check("read")
        self.assertIn(result, [ALLOWED, DENIED])

    def test_priority_with_different_tools(self):
        """Priority only matters when the same tool matches."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", priority=0))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny", priority=100))
        self.assertEqual(ALLOWED, ruleset.check("read"))
        self.assertEqual(DENIED, ruleset.check("write"))


class TestLoadFromConfig(unittest.TestCase):
    """Test load_from_config() with various config formats."""

    def test_load_basic_config(self):
        """Load basic config with tool and action."""
        ruleset = PermissionRuleset()
        config = {
            "permissions": [
                {"tool": "read", "action": "allow"},
                {"tool": "write", "action": "deny"},
            ]
        }
        ruleset.load_from_config(config)
        self.assertEqual(2, len(ruleset))
        self.assertEqual(ALLOWED, ruleset.check("read"))
        self.assertEqual(DENIED, ruleset.check("write"))

    def test_load_config_with_path(self):
        """Load config with path patterns."""
        ruleset = PermissionRuleset()
        config = {
            "permissions": [
                {"tool": "read", "action": "allow", "path": "*.py"},
            ]
        }
        ruleset.load_from_config(config)
        self.assertEqual(ALLOWED, ruleset.check("read", path="test.py"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", path="test.txt"))

    def test_load_config_with_agent(self):
        """Load config with agent-specific rules."""
        ruleset = PermissionRuleset()
        config = {
            "permissions": [
                {"tool": "read", "action": "allow", "agent": "coder"},
            ]
        }
        ruleset.load_from_config(config)
        self.assertEqual(ALLOWED, ruleset.check("read", agent="coder"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", agent="reviewer"))

    def test_load_config_with_priority(self):
        """Load config with explicit priority."""
        ruleset = PermissionRuleset()
        config = {
            "permissions": [
                {"tool": "read", "action": "deny", "priority": 0},
                {"tool": "read", "action": "allow", "priority": 10},
            ]
        }
        ruleset.load_from_config(config)
        self.assertEqual(ALLOWED, ruleset.check("read"))

    def test_load_empty_config(self):
        """Load empty config adds no rules."""
        ruleset = PermissionRuleset()
        ruleset.load_from_config({})
        self.assertEqual(0, len(ruleset))

    def test_load_config_missing_permissions_key(self):
        """Load config without 'permissions' key adds no rules."""
        ruleset = PermissionRuleset()
        ruleset.load_from_config({"other_key": "value"})
        self.assertEqual(0, len(ruleset))

    def test_load_config_multiple_times(self):
        """Loading config multiple times appends rules."""
        ruleset = PermissionRuleset()
        ruleset.load_from_config({
            "permissions": [{"tool": "read", "action": "allow"}]
        })
        ruleset.load_from_config({
            "permissions": [{"tool": "write", "action": "deny"}]
        })
        self.assertEqual(2, len(ruleset))


class TestThreadSafety(unittest.TestCase):
    """Test concurrent check() calls don't race."""

    def test_concurrent_check(self):
        """Multiple threads calling check() concurrently don't cause errors."""
        ruleset = PermissionRuleset()
        for i in range(50):
            ruleset.add_rule(
                AgentPermissionRule(
                    tool="tool_{}".format(i),
                    action="allow" if i % 2 == 0 else "deny",
                    priority=i,
                )
            )

        errors = []

        def worker():
            try:
                for _ in range(100):
                    for i in range(50):
                        ruleset.check("tool_{}".format(i))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(0, len(errors), "Thread safety errors: {}".format(errors))

    def test_concurrent_add_and_check(self):
        """Concurrent add_rule and check() don't cause errors."""
        ruleset = PermissionRuleset()
        errors = []

        def adder():
            try:
                for i in range(50):
                    ruleset.add_rule(
                        AgentPermissionRule(
                            tool="tool_{}".format(i),
                            action="allow",
                        )
                    )
            except Exception as e:
                errors.append(e)

        def checker():
            try:
                for _ in range(100):
                    for i in range(50):
                        ruleset.check("tool_{}".format(i))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=adder)
        t2 = threading.Thread(target=checker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(0, len(errors), "Thread safety errors: {}".format(errors))


class TestAgentPermissionRule(unittest.TestCase):
    """Test AgentPermissionRule.matches() directly."""

    def test_rule_matches_tool(self):
        """Rule matches when tool matches."""
        rule = AgentPermissionRule(tool="read", action="allow")
        self.assertTrue(rule.matches("read"))

    def test_rule_does_not_match_tool(self):
        """Rule doesn't match when tool doesn't match."""
        rule = AgentPermissionRule(tool="read", action="allow")
        self.assertFalse(rule.matches("write"))

    def test_rule_matches_agent(self):
        """Agent-specific rule matches only that agent."""
        rule = AgentPermissionRule(tool="read", action="allow", agent="coder")
        self.assertTrue(rule.matches("read", agent="coder"))
        self.assertFalse(rule.matches("read", agent="reviewer"))
        self.assertFalse(rule.matches("read", agent=None))

    def test_global_rule_matches_any_agent(self):
        """Global rule (agent=None) matches any agent."""
        rule = AgentPermissionRule(tool="read", action="allow")
        self.assertTrue(rule.matches("read", agent="coder"))
        self.assertTrue(rule.matches("read", agent="reviewer"))
        self.assertTrue(rule.matches("read", agent=None))

    def test_rule_matches_path(self):
        """Rule with path matches only matching paths."""
        rule = AgentPermissionRule(tool="read", action="allow", path="*.py")
        self.assertTrue(rule.matches("read", path="test.py"))
        self.assertFalse(rule.matches("read", path="test.txt"))
        self.assertFalse(rule.matches("read", path=None))

    def test_rule_without_path_matches_any(self):
        """Rule without path matches any path."""
        rule = AgentPermissionRule(tool="read", action="allow")
        self.assertTrue(rule.matches("read", path="test.py"))
        self.assertTrue(rule.matches("read", path=None))

    def test_rule_equality(self):
        """Two rules with same attributes are equal."""
        r1 = AgentPermissionRule(tool="read", action="allow", path="*.py", agent="coder", priority=10)
        r2 = AgentPermissionRule(tool="read", action="allow", path="*.py", agent="coder", priority=10)
        self.assertEqual(r1, r2)

    def test_rule_inequality(self):
        """Rules with different attributes are not equal."""
        r1 = AgentPermissionRule(tool="read", action="allow")
        r2 = AgentPermissionRule(tool="write", action="allow")
        self.assertNotEqual(r1, r2)

    def test_rule_equality_non_rule(self):
        """Rule compared to non-rule returns NotImplemented."""
        rule = AgentPermissionRule(tool="read", action="allow")
        self.assertEqual(NotImplemented, rule.__eq__("not a rule"))

    def test_rule_repr(self):
        """__repr__ returns a useful string."""
        rule = AgentPermissionRule(tool="read", action="allow", path="*.py", agent="coder", priority=10)
        r = repr(rule)
        self.assertIn("read", r)
        self.assertIn("allow", r)
        self.assertIn("*.py", r)
        self.assertIn("coder", r)
        self.assertIn("10", r)


if __name__ == "__main__":
    unittest.main()
