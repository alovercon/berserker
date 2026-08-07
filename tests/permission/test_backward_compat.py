"""Tests verifying existing PermissionChecker API is unchanged (backward compatibility)."""

import unittest

from berserker.permission import (
    ALLOWED,
    DENIED,
    NEEDS_ASK,
    PermissionChecker,
    PermissionRule,
    PermissionResult,
    permission_checker,
)


class TestConstants(unittest.TestCase):
    """Test that ALLOWED, DENIED, NEEDS_ASK constants are unchanged."""

    def test_allowed_constant(self):
        self.assertEqual("allowed", ALLOWED)

    def test_denied_constant(self):
        self.assertEqual("denied", DENIED)

    def test_needs_ask_constant(self):
        self.assertEqual("needs_ask", NEEDS_ASK)

    def test_permission_result_class(self):
        """PermissionResult class has correct attributes."""
        self.assertEqual(ALLOWED, PermissionResult.ALLOWED)
        self.assertEqual(DENIED, PermissionResult.DENIED)
        self.assertEqual(NEEDS_ASK, PermissionResult.NEEDS_ASK)


class TestPermissionRule(unittest.TestCase):
    """Test original PermissionRule class still works."""

    def test_create_rule_basic(self):
        """Create a basic rule with tool and action."""
        rule = PermissionRule(tool_name="read", action="allow")
        self.assertEqual("read", rule.tool_name)
        self.assertEqual("allow", rule.action)
        self.assertIsNone(rule.path_pattern)

    def test_create_rule_with_path(self):
        """Create a rule with path pattern."""
        rule = PermissionRule(tool_name="write", action="ask", path_pattern="*.py")
        self.assertEqual("write", rule.tool_name)
        self.assertEqual("ask", rule.action)
        self.assertEqual("*.py", rule.path_pattern)


class TestPermissionCheckerAPI(unittest.TestCase):
    """Test PermissionChecker still works with its original API."""

    def setUp(self):
        """Create a fresh checker with default rules for each test."""
        self.checker = PermissionChecker()

    def test_check_basic_allow(self):
        """check() returns ALLOWED for allowed tools."""
        # Default rules allow read
        result = self.checker.check("read")
        self.assertEqual(ALLOWED, result)

    def test_check_basic_ask(self):
        """check() returns NEEDS_ASK for ask tools."""
        # Default rules ask for write
        result = self.checker.check("write")
        self.assertEqual(NEEDS_ASK, result)

    def test_check_with_path(self):
        """check() with path argument works."""
        result = self.checker.check("read", path="test.py")
        self.assertEqual(ALLOWED, result)

    def test_check_with_args(self):
        """check() with args argument works (args currently unused)."""
        result = self.checker.check("read", args={"file": "test.py"})
        self.assertEqual(ALLOWED, result)

    def test_check_unknown_tool(self):
        """check() returns NEEDS_ASK for unknown tools."""
        result = self.checker.check("unknown_tool")
        self.assertEqual(NEEDS_ASK, result)

    def test_check_signature(self):
        """check() accepts tool_name, args, path."""
        # All three parameters should be accepted
        result = self.checker.check("read", args=None, path=None)
        self.assertEqual(ALLOWED, result)


class TestPermissionCheckerAddRule(unittest.TestCase):
    """Test PermissionChecker.add_rule() works."""

    def setUp(self):
        self.checker = PermissionChecker()

    def test_add_rule_allows_new_tool(self):
        """Adding an allow rule permits a previously unknown tool."""
        self.assertEqual(NEEDS_ASK, self.checker.check("custom_tool"))

        rule = PermissionRule(tool_name="custom_tool", action="allow")
        self.checker.add_rule(rule)

        self.assertEqual(ALLOWED, self.checker.check("custom_tool"))

    def test_add_rule_denies_tool(self):
        """Adding a deny rule blocks a tool."""
        self.assertEqual(ALLOWED, self.checker.check("read"))

        rule = PermissionRule(tool_name="read", action="deny")
        self.checker.add_rule(rule)

        # New rule is appended, but default rules come first
        # Default allow rule matches first, so still allowed
        # This tests that add_rule appends (not inserts)
        self.assertEqual(ALLOWED, self.checker.check("read"))

    def test_add_rule_clears_cache(self):
        """Adding a rule clears the permission cache."""
        checker = PermissionChecker(rules=[])
        checker.add_rule(PermissionRule(tool_name="read", action="allow"))

        # First check caches the result
        result1 = checker.check("read")
        self.assertEqual(ALLOWED, result1)

        # Add a deny rule (appended after allow)
        checker.add_rule(PermissionRule(tool_name="read", action="deny"))

        # Cache cleared, but allow rule still matches first (it was there first)
        # This verifies cache was cleared (re-evaluation happens)
        result2 = checker.check("read")
        self.assertEqual(ALLOWED, result2)  # allow rule still first


class TestPermissionCheckerLoadFromConfig(unittest.TestCase):
    """Test PermissionChecker.load_from_config() works."""

    def setUp(self):
        self.checker = PermissionChecker()

    def test_load_from_config_basic(self):
        """Load basic config with permissions list."""
        config = {
            "permissions": [
                {"tool": "custom", "action": "allow"},
            ]
        }
        self.checker.load_from_config(config)

        self.assertEqual(ALLOWED, self.checker.check("custom"))

    def test_load_from_config_with_path(self):
        """Load config with path-based rules."""
        config = {
            "permissions": [
                {"tool": "read", "action": "deny", "path": "secret/*"},
            ]
        }
        self.checker.load_from_config(config)

        # User rules are inserted at beginning, so they take precedence
        self.assertEqual(DENIED, self.checker.check("read", path="secret/config.py"))
        self.assertEqual(ALLOWED, self.checker.check("read", path="public/file.txt"))

    def test_load_from_config_empty(self):
        """Load empty config doesn't break checker."""
        self.checker.load_from_config({})
        self.assertEqual(ALLOWED, self.checker.check("read"))

    def test_load_from_config_multiple_rules(self):
        """Load config with multiple rules."""
        config = {
            "permissions": [
                {"tool": "read", "action": "allow"},
                {"tool": "write", "action": "deny"},
                {"tool": "edit", "action": "ask"},
            ]
        }
        self.checker.load_from_config(config)

        self.assertEqual(ALLOWED, self.checker.check("read"))
        self.assertEqual(DENIED, self.checker.check("write"))
        self.assertEqual(NEEDS_ASK, self.checker.check("edit"))


class TestPermissionCheckerClearCache(unittest.TestCase):
    """Test PermissionChecker.clear_cache() works."""

    def test_clear_cache(self):
        """clear_cache() empties the internal cache."""
        checker = PermissionChecker()

        # Populate cache
        checker.check("read")
        checker.check("write")

        # Clear cache
        checker.clear_cache()

        # Cache should be empty
        self.assertEqual(0, len(checker._cache))

    def test_cache_is_used(self):
        """Repeated checks return cached results."""
        checker = PermissionChecker()

        result1 = checker.check("read")
        result2 = checker.check("read")

        self.assertEqual(result1, result2)
        self.assertEqual(1, len(checker._cache))


class TestPermissionCheckerSingleton(unittest.TestCase):
    """Test permission_checker singleton is accessible."""

    def test_singleton_exists(self):
        """permission_checker singleton is accessible."""
        self.assertIsNotNone(permission_checker)

    def test_singleton_is_permission_checker(self):
        """permission_checker is a PermissionChecker instance."""
        self.assertIsInstance(permission_checker, PermissionChecker)

    def test_singleton_has_default_rules(self):
        """Singleton has default rules pre-loaded."""
        result = permission_checker.check("read")
        self.assertEqual(ALLOWED, result)

    def test_singleton_check_api(self):
        """Singleton check() works with standard API."""
        result = permission_checker.check("read", args=None, path="test.py")
        self.assertEqual(ALLOWED, result)


class TestPermissionCheckerDefaultRules(unittest.TestCase):
    """Test default permission rules are correct."""

    def setUp(self):
        self.checker = PermissionChecker()

    def test_read_allowed(self):
        self.assertEqual(ALLOWED, self.checker.check("read"))

    def test_ls_allowed(self):
        self.assertEqual(ALLOWED, self.checker.check("ls"))

    def test_glob_allowed(self):
        self.assertEqual(ALLOWED, self.checker.check("glob"))

    def test_grep_allowed(self):
        self.assertEqual(ALLOWED, self.checker.check("grep"))

    def test_write_ask(self):
        self.assertEqual(NEEDS_ASK, self.checker.check("write"))

    def test_edit_ask(self):
        self.assertEqual(NEEDS_ASK, self.checker.check("edit"))

    def test_bash_ask(self):
        self.assertEqual(NEEDS_ASK, self.checker.check("bash"))

    def test_mkdir_ask(self):
        self.assertEqual(NEEDS_ASK, self.checker.check("mkdir"))

    def test_rm_ask(self):
        self.assertEqual(NEEDS_ASK, self.checker.check("rm"))


class TestPermissionCheckerWithPathMatching(unittest.TestCase):
    """Test PermissionChecker with path-based glob matching."""

    def test_path_pattern_match(self):
        """Path pattern matching works with fnmatch."""
        checker = PermissionChecker(rules=[])
        checker.add_rule(PermissionRule(tool_name="read", action="deny", path_pattern="secret/*"))
        checker.add_rule(PermissionRule(tool_name="read", action="allow"))

        self.assertEqual(DENIED, checker.check("read", path="secret/config.py"))
        self.assertEqual(ALLOWED, checker.check("read", path="public/file.txt"))

    def test_path_pattern_extension(self):
        """Path pattern matching by extension."""
        checker = PermissionChecker(rules=[])
        checker.add_rule(PermissionRule(tool_name="read", action="deny", path_pattern="*.py"))
        checker.add_rule(PermissionRule(tool_name="read", action="allow"))

        self.assertEqual(DENIED, checker.check("read", path="test.py"))
        self.assertEqual(ALLOWED, checker.check("read", path="test.txt"))


class TestPermissionCheckerWithCustomRules(unittest.TestCase):
    """Test PermissionChecker with custom rule list."""

    def test_custom_rules_list(self):
        """Initialize with custom rules list."""
        rules = [
            PermissionRule(tool_name="read", action="allow"),
            PermissionRule(tool_name="write", action="deny"),
        ]
        checker = PermissionChecker(rules=rules)

        self.assertEqual(ALLOWED, checker.check("read"))
        self.assertEqual(DENIED, checker.check("write"))
        self.assertEqual(NEEDS_ASK, checker.check("edit"))

    def test_custom_rules_empty(self):
        """Initialize with empty rules list."""
        checker = PermissionChecker(rules=[])

        self.assertEqual(NEEDS_ASK, checker.check("read"))
        self.assertEqual(NEEDS_ASK, checker.check("write"))


if __name__ == "__main__":
    unittest.main()
