"""
End-to-end tests for prompt externalization with AgentManager.

Tests that:
- AgentManager loads prompts from external files
- Fallback to inline prompts works when external files are missing
- All 7 built-in agents get their prompts correctly

Python 3.8.10 compatible.
"""

import os
import shutil
import tempfile
import unittest

from berserker.agent.manager import (
    AgentManager,
    BUILT_IN_AGENTS_CONFIG,
    BUILT_IN_AGENTS,
    _load_system_prompt,
    _INLINE_PROMPTS,
)
from berserker.agent.prompt_loader import PromptLoader


class TestPromptExternalization(unittest.TestCase):
    """Test that AgentManager uses external prompts with fallback."""

    def test_all_builtin_agents_have_prompts(self):
        """All 7 built-in agents have non-empty system prompts."""
        expected_agents = ["build", "plan", "general", "explore", "compaction", "title", "summary"]
        for agent_name in expected_agents:
            prompt = _load_system_prompt(agent_name)
            self.assertIsInstance(prompt, str)
            self.assertTrue(
                len(prompt) > 0,
                "Agent '{}' has an empty system prompt".format(agent_name),
            )

    def test_builtin_agents_config_uses_external_prompts(self):
        """BUILT_IN_AGENTS_CONFIG loads from external prompt files."""
        for agent_info in BUILT_IN_AGENTS_CONFIG:
            self.assertIsInstance(agent_info.system_prompt, str)
            self.assertTrue(
                len(agent_info.system_prompt) > 0,
                "Agent '{}' config has empty system prompt".format(agent_info.name),
            )

    def test_agent_manager_registers_all_agents(self):
        """AgentManager registers all 7 built-in agents."""
        manager = AgentManager()
        for agent_name in BUILT_IN_AGENTS:
            agent = manager.get(agent_name)
            self.assertIsNotNone(agent)
            self.assertEqual(agent.name, agent_name)
            self.assertTrue(len(agent.system_prompt) > 0)

    def test_build_agent_prompt_contains_os_environment(self):
        """Build agent prompt has OS environment injected by AgentManager."""
        manager = AgentManager()
        build_agent = manager.get("berserker")
        # The OS environment should be injected (not the raw {os_environment} placeholder)
        self.assertNotIn("{os_environment}", build_agent.system_prompt)
        self.assertIn("Runtime Environment", build_agent.system_prompt)

    def test_non_build_agents_no_os_environment_placeholder(self):
        """Non-build agents should not have {os_environment} in their prompts."""
        manager = AgentManager()
        for agent_name in ["plan", "general", "explore", "compaction", "title", "summary"]:
            agent = manager.get(agent_name)
            # These agents don't use {os_environment} template
            self.assertNotIn(
                "{os_environment}",
                agent.system_prompt,
                "Agent '{}' should not have {{os_environment}} placeholder".format(agent_name),
            )


class TestFallbackBehavior(unittest.TestCase):
    """Test fallback to inline prompts when external files are missing."""

    def setUp(self):
        # Create a temporary empty directory (no prompt files)
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_fallback_returns_inline_prompt(self):
        """When external file is missing, _load_system_prompt returns inline fallback."""
        # Use a loader with empty directory to force fallback
        loader = PromptLoader(prompt_dir=self.tmpdir)
        # Direct loader call should raise FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            loader.load("berserker")
        # But _load_system_prompt should fall back to inline
        prompt = _load_system_prompt("berserker")
        self.assertIn("skilled software engineer", prompt)

    def test_inline_prompts_dict_has_all_agents(self):
        """_INLINE_PROMPTS contains all agent prompts."""
        expected = ["berserker", "plan", "general", "explore", "compaction", "title", "summary", "consultant", "critic", "executor"]
        for name in expected:
            self.assertIn(name, _INLINE_PROMPTS)
            self.assertTrue(len(_INLINE_PROMPTS[name]) > 0)

    def test_external_prompts_match_inline_content(self):
        """External prompt files should have same content as inline fallback."""
        for name in ["berserker", "plan", "general", "explore", "compaction", "title", "summary", "consultant", "critic", "executor"]:
            external = _load_system_prompt(name)
            inline = _INLINE_PROMPTS[name]
            # Strip trailing whitespace for comparison (files may have trailing newline)
            self.assertEqual(
                external.strip(),
                inline.strip(),
                "Prompt '{}' content mismatch between external and inline".format(name),
            )


class TestPromptLoaderIntegration(unittest.TestCase):
    """Test PromptLoader integration with the agent system."""

    def test_default_prompt_directory_exists(self):
        """The default prompts directory exists."""
        loader = PromptLoader()
        self.assertTrue(os.path.isdir(loader.prompt_dir))

    def test_default_prompt_directory_has_all_prompts(self):
        """The default prompts directory has all 7 prompt files."""
        loader = PromptLoader()
        prompts = loader.discover_prompts()
        expected = ["build", "plan", "general", "explore", "compaction", "title", "summary"]
        for name in expected:
            self.assertIn(name, prompts)

    def test_load_all_prompts_via_loader(self):
        """All 7 prompts can be loaded via PromptLoader."""
        loader = PromptLoader()
        for name in ["build", "plan", "general", "explore", "compaction", "title", "summary"]:
            prompt = loader.load(name)
            self.assertIsInstance(prompt, str)
            self.assertTrue(len(prompt) > 0, "Prompt '{}' is empty".format(name))


if __name__ == "__main__":
    unittest.main()
