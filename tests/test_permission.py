"""Tests for permission system: PermissionChecker, rule precedence, config loading."""

import pytest

from berserker.permission import (
    PermissionChecker,
    PermissionRule,
    PermissionResult,
    ALLOWED,
    DENIED,
    NEEDS_ASK,
)


# ---------------------------------------------------------------------------
# PermissionResult Tests
# ---------------------------------------------------------------------------


class TestPermissionResult:
    """Test PermissionResult constants."""

    def test_allowed(self):
        assert PermissionResult.ALLOWED == "allowed"

    def test_denied(self):
        assert PermissionResult.DENIED == "denied"

    def test_needs_ask(self):
        assert PermissionResult.NEEDS_ASK == "needs_ask"


# ---------------------------------------------------------------------------
# PermissionRule Tests
# ---------------------------------------------------------------------------


class TestPermissionRule:
    """Test PermissionRule creation."""

    def test_basic_rule(self):
        rule = PermissionRule(tool_name="read", action="allow")
        assert rule.tool_name == "read"
        assert rule.action == "allow"
        assert rule.path_pattern is None

    def test_rule_with_path(self):
        rule = PermissionRule(tool_name="write", action="deny", path_pattern="*.py")
        assert rule.tool_name == "write"
        assert rule.action == "deny"
        assert rule.path_pattern == "*.py"


# ---------------------------------------------------------------------------
# PermissionChecker Tests — Default Rules
# ---------------------------------------------------------------------------


class TestDefaultPermissions:
    """Test default permission rules."""

    def test_read_allowed(self):
        checker = PermissionChecker()
        assert checker.check("read") == ALLOWED

    def test_ls_allowed(self):
        checker = PermissionChecker()
        assert checker.check("ls") == ALLOWED

    def test_glob_allowed(self):
        checker = PermissionChecker()
        assert checker.check("glob") == ALLOWED

    def test_write_needs_ask(self):
        checker = PermissionChecker()
        assert checker.check("write") == NEEDS_ASK

    def test_edit_needs_ask(self):
        checker = PermissionChecker()
        assert checker.check("edit") == NEEDS_ASK

    def test_bash_needs_ask(self):
        checker = PermissionChecker()
        assert checker.check("bash") == NEEDS_ASK

    def test_unknown_tool_needs_ask(self):
        """Unknown tools should default to NEEDS_ASK."""
        checker = PermissionChecker()
        assert checker.check("unknown_tool") == NEEDS_ASK


# ---------------------------------------------------------------------------
# Custom Rules Tests
# ---------------------------------------------------------------------------


class TestCustomRules:
    """Test custom rule configuration."""

    def test_empty_rules_all_need_ask(self):
        """With no rules, everything should be NEEDS_ASK."""
        checker = PermissionChecker(rules=[])
        assert checker.check("read") == NEEDS_ASK
        assert checker.check("write") == NEEDS_ASK
        assert checker.check("bash") == NEEDS_ASK

    def test_allow_all(self):
        """Should allow all tools when rules say so."""
        rules = [
            PermissionRule(tool_name="read", action="allow"),
            PermissionRule(tool_name="write", action="allow"),
            PermissionRule(tool_name="bash", action="allow"),
        ]
        checker = PermissionChecker(rules=rules)
        assert checker.check("read") == ALLOWED
        assert checker.check("write") == ALLOWED
        assert checker.check("bash") == ALLOWED

    def test_deny_all(self):
        """Should deny all tools when rules say so."""
        rules = [
            PermissionRule(tool_name="read", action="deny"),
            PermissionRule(tool_name="write", action="deny"),
        ]
        checker = PermissionChecker(rules=rules)
        assert checker.check("read") == DENIED
        assert checker.check("write") == DENIED


# ---------------------------------------------------------------------------
# Path-based Rule Tests
# ---------------------------------------------------------------------------


class TestPathBasedRules:
    """Test glob path pattern matching."""

    def test_path_match_allows(self):
        """Should allow when path matches pattern."""
        rules = [
            PermissionRule(tool_name="read", action="allow", path_pattern="*.py"),
        ]
        checker = PermissionChecker(rules=rules)
        assert checker.check("read", path="test.py") == ALLOWED

    def test_path_no_match_falls_through(self):
        """Should fall through when path doesn't match."""
        rules = [
            PermissionRule(tool_name="read", action="allow", path_pattern="*.py"),
        ]
        checker = PermissionChecker(rules=rules)
        # No match on path, no default rule for read → NEEDS_ASK
        assert checker.check("read", path="test.txt") == NEEDS_ASK

    def test_path_pattern_with_wildcard(self):
        """Should match ** patterns."""
        rules = [
            PermissionRule(tool_name="write", action="allow", path_pattern="src/**/*.py"),
        ]
        checker = PermissionChecker(rules=rules)
        assert checker.check("write", path="src/utils/helper.py") == ALLOWED

    def test_path_none_matches_all(self):
        """Rule with no path_pattern should match any path."""
        rules = [
            PermissionRule(tool_name="read", action="allow"),
        ]
        checker = PermissionChecker(rules=rules)
        assert checker.check("read", path="anything.txt") == ALLOWED


# ---------------------------------------------------------------------------
# Rule Precedence Tests
# ---------------------------------------------------------------------------


class TestRulePrecedence:
    """Test first-matching-rule-wins precedence."""

    def test_first_rule_wins(self):
        """First matching rule should win."""
        rules = [
            PermissionRule(tool_name="read", action="deny"),
            PermissionRule(tool_name="read", action="allow"),
        ]
        checker = PermissionChecker(rules=rules)
        assert checker.check("read") == DENIED

    def test_specific_before_general(self):
        """More specific rules should be placed before general ones."""
        rules = [
            PermissionRule(tool_name="write", action="deny", path_pattern="*.conf"),
            PermissionRule(tool_name="write", action="allow"),
        ]
        checker = PermissionChecker(rules=rules)
        assert checker.check("write", path="config.conf") == DENIED
        assert checker.check("write", path="data.txt") == ALLOWED


# ---------------------------------------------------------------------------
# load_from_config Tests
# ---------------------------------------------------------------------------


class TestLoadFromConfig:
    """Test loading rules from config dict."""

    def test_load_basic_rules(self):
        """Should load rules from config dict."""
        checker = PermissionChecker(rules=[])
        checker.load_from_config(
            {
                "permissions": [
                    {"tool": "read", "action": "allow"},
                    {"tool": "write", "action": "allow"},
                ]
            }
        )
        assert checker.check("read") == ALLOWED
        assert checker.check("write") == ALLOWED

    def test_load_with_path(self):
        """Should load rules with path patterns."""
        checker = PermissionChecker(rules=[])
        checker.load_from_config(
            {
                "permissions": [
                    {"tool": "read", "action": "allow", "path": "*.py"},
                ]
            }
        )
        assert checker.check("read", path="test.py") == ALLOWED
        assert checker.check("read", path="test.txt") == NEEDS_ASK

    def test_user_rules_override_defaults(self):
        """User config rules should be inserted at beginning (higher precedence)."""
        checker = PermissionChecker()  # Has default rules (write=ask)
        checker.load_from_config(
            {
                "permissions": [
                    {"tool": "write", "action": "allow"},
                ]
            }
        )
        # User rule should override default
        assert checker.check("write") == ALLOWED

    def test_empty_config_no_change(self):
        """Empty config should not change existing rules."""
        rules = [PermissionRule(tool_name="read", action="deny")]
        checker = PermissionChecker(rules=rules)
        checker.load_from_config({})
        assert checker.check("read") == DENIED


# ---------------------------------------------------------------------------
# Cache Tests
# ---------------------------------------------------------------------------


class TestCache:
    """Test permission caching."""

    def test_cache_hit(self):
        """Should return cached result for repeated checks."""
        checker = PermissionChecker()
        result1 = checker.check("read")
        result2 = checker.check("read")
        assert result1 == result2

    def test_clear_cache(self):
        """Should clear the cache."""
        checker = PermissionChecker()
        checker.check("read")  # Cache this
        checker.clear_cache()
        # After clearing, should re-evaluate
        assert checker.check("read") == ALLOWED

    def test_add_rule_clears_cache(self):
        """Adding a rule should clear the cache."""
        checker = PermissionChecker(rules=[])
        checker.check("read")  # Caches NEEDS_ASK
        checker.add_rule(PermissionRule(tool_name="read", action="allow"))
        # After adding rule, cache should be cleared
        assert checker.check("read") == ALLOWED

    def test_load_from_config_clears_cache(self):
        """Loading from config should clear the cache."""
        checker = PermissionChecker(rules=[])
        checker.check("read")  # Caches NEEDS_ASK
        checker.load_from_config({"permissions": [{"tool": "read", "action": "allow"}]})
        assert checker.check("read") == ALLOWED


# ---------------------------------------------------------------------------
# Singleton Tests
# ---------------------------------------------------------------------------


class TestSingleton:
    """Test the module-level singleton."""

    def test_singleton_exists(self):
        from berserker.permission import permission_checker

        assert permission_checker is not None
        assert isinstance(permission_checker, PermissionChecker)

    def test_singleton_has_defaults(self):
        from berserker.permission import permission_checker

        assert permission_checker.check("read") == ALLOWED
        assert permission_checker.check("write") == NEEDS_ASK
