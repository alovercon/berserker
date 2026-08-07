"""Tests for agent-specific permission overrides and global rule interactions."""

import unittest

from berserker.permission import (
    ALLOWED,
    DENIED,
    NEEDS_ASK,
    AgentPermissionRule,
    PermissionRuleset,
)


class TestAgentSpecificRules(unittest.TestCase):
    """Test agent-specific rule matching."""

    def test_agent_specific_rule_matches_only_that_agent(self):
        """Agent-specific rule only applies to the named agent."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", agent="coder"))

        self.assertEqual(ALLOWED, ruleset.check("read", agent="coder"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", agent="reviewer"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", agent=None))

    def test_agent_specific_deny(self):
        """Agent-specific deny only affects that agent."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny", agent="intern"))

        self.assertEqual(DENIED, ruleset.check("write", agent="intern"))
        self.assertEqual(NEEDS_ASK, ruleset.check("write", agent="senior"))
        self.assertEqual(NEEDS_ASK, ruleset.check("write", agent=None))


class TestGlobalRules(unittest.TestCase):
    """Test global rules (agent=None) match any agent."""

    def test_global_rule_matches_any_agent(self):
        """Global rule applies to all agents."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))

        self.assertEqual(ALLOWED, ruleset.check("read", agent="coder"))
        self.assertEqual(ALLOWED, ruleset.check("read", agent="reviewer"))
        self.assertEqual(ALLOWED, ruleset.check("read", agent=None))

    def test_global_rule_matches_no_agent_provided(self):
        """Global rule matches when no agent is provided."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="ls", action="allow"))

        self.assertEqual(ALLOWED, ruleset.check("ls"))


class TestAgentOverridesGlobal(unittest.TestCase):
    """Test agent-specific overrides global at same priority."""

    def test_agent_specific_overrides_global_same_priority(self):
        """Agent-specific rule takes precedence over global at same priority."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", priority=0))
        ruleset.add_rule(
            AgentPermissionRule(tool="read", action="deny", agent="coder", priority=0)
        )

        # coder gets denied (agent-specific wins)
        self.assertEqual(DENIED, ruleset.check("read", agent="coder"))
        # others get allowed (global rule)
        self.assertEqual(ALLOWED, ruleset.check("read", agent="reviewer"))
        self.assertEqual(ALLOWED, ruleset.check("read", agent=None))

    def test_agent_specific_overrides_global_different_actions(self):
        """Agent-specific allow overrides global deny."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny", priority=0))
        ruleset.add_rule(
            AgentPermissionRule(tool="write", action="allow", agent="admin", priority=0)
        )

        self.assertEqual(ALLOWED, ruleset.check("write", agent="admin"))
        self.assertEqual(DENIED, ruleset.check("write", agent="user"))
        self.assertEqual(DENIED, ruleset.check("write", agent=None))

    def test_global_overrides_lower_priority_agent_specific(self):
        """Higher priority global overrides lower priority agent-specific."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(
            AgentPermissionRule(tool="read", action="deny", agent="coder", priority=0)
        )
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", priority=10))

        # Higher priority global wins for everyone
        self.assertEqual(ALLOWED, ruleset.check("read", agent="coder"))
        self.assertEqual(ALLOWED, ruleset.check("read", agent="reviewer"))


class TestMultipleAgents(unittest.TestCase):
    """Test multiple agents with different permissions for same tool."""

    def test_different_agents_different_permissions(self):
        """Multiple agents have independent permissions for same tool."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", agent="coder"))
        ruleset.add_rule(AgentPermissionRule(tool="read", action="deny", agent="reviewer"))
        ruleset.add_rule(AgentPermissionRule(tool="read", action="ask", agent="tester"))

        self.assertEqual(ALLOWED, ruleset.check("read", agent="coder"))
        self.assertEqual(DENIED, ruleset.check("read", agent="reviewer"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", agent="tester"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", agent=None))

    def test_agent_with_multiple_tools(self):
        """Same agent has different permissions for different tools."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", agent="coder"))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny", agent="coder"))
        ruleset.add_rule(AgentPermissionRule(tool="bash", action="ask", agent="coder"))

        self.assertEqual(ALLOWED, ruleset.check("read", agent="coder"))
        self.assertEqual(DENIED, ruleset.check("write", agent="coder"))
        self.assertEqual(NEEDS_ASK, ruleset.check("bash", agent="coder"))


class TestAgentSpecificPathRestrictions(unittest.TestCase):
    """Test agent-specific path restrictions."""

    def test_agent_specific_path_allow(self):
        """Agent can only access specific paths."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(
            AgentPermissionRule(tool="read", action="allow", path="src/*.py", agent="coder")
        )

        self.assertEqual(ALLOWED, ruleset.check("read", agent="coder", path="src/main.py"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", agent="coder", path="test/main.py"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", agent="reviewer", path="src/main.py"))

    def test_agent_specific_path_deny(self):
        """Agent is denied access to specific paths."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        ruleset.add_rule(
            AgentPermissionRule(tool="read", action="deny", path="secret/*", agent="intern")
        )

        self.assertEqual(DENIED, ruleset.check("read", agent="intern", path="secret/config.py"))
        self.assertEqual(ALLOWED, ruleset.check("read", agent="intern", path="src/main.py"))
        self.assertEqual(ALLOWED, ruleset.check("read", agent="senior", path="secret/config.py"))

    def test_agent_specific_path_with_priority(self):
        """Agent-specific path rules with priority ordering."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(
            AgentPermissionRule(tool="write", action="deny", path="*.py", agent="coder", priority=0)
        )
        ruleset.add_rule(
            AgentPermissionRule(tool="write", action="allow", path="src/*.py", agent="coder", priority=10)
        )

        # Higher priority allows writing to src/*.py
        self.assertEqual(ALLOWED, ruleset.check("write", agent="coder", path="src/main.py"))
        # Lower priority denies writing to other *.py
        self.assertEqual(DENIED, ruleset.check("write", agent="coder", path="test/main.py"))


class TestAgentRuleMatching(unittest.TestCase):
    """Test edge cases in agent rule matching."""

    def test_agent_specific_rule_no_agent_provided(self):
        """Agent-specific rule doesn't match when no agent is provided."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow", agent="coder"))

        self.assertEqual(NEEDS_ASK, ruleset.check("read"))

    def test_global_and_agent_specific_combined(self):
        """Global and agent-specific rules coexist correctly."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="deny", agent="intern"))
        ruleset.add_rule(AgentPermissionRule(tool="write", action="allow", agent="admin"))

        # read is allowed for everyone
        self.assertEqual(ALLOWED, ruleset.check("read", agent="intern"))
        self.assertEqual(ALLOWED, ruleset.check("read", agent="admin"))

        # write depends on agent
        self.assertEqual(DENIED, ruleset.check("write", agent="intern"))
        self.assertEqual(ALLOWED, ruleset.check("write", agent="admin"))
        self.assertEqual(NEEDS_ASK, ruleset.check("write", agent=None))

    def test_agent_specific_with_glob_tool(self):
        """Agent-specific rule with glob tool pattern."""
        ruleset = PermissionRuleset()
        ruleset.add_rule(
            AgentPermissionRule(tool="read*", action="allow", agent="coder")
        )

        self.assertEqual(ALLOWED, ruleset.check("read", agent="coder"))
        self.assertEqual(ALLOWED, ruleset.check("read_file", agent="coder"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read", agent="reviewer"))
        self.assertEqual(NEEDS_ASK, ruleset.check("read_file", agent="reviewer"))


if __name__ == "__main__":
    unittest.main()
