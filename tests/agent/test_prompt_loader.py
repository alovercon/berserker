"""
Tests for PromptLoader — prompt loading, templating, and discovery.

Python 3.8.10 compatible.
"""

import os
import shutil
import tempfile
import unittest

from berserker.agent.prompt_loader import (
    PromptLoader,
    load_prompt,
    discover_prompts,
)


class TestPromptLoaderLoad(unittest.TestCase):
    """Test PromptLoader.load() method."""

    def setUp(self):
        # Create a temporary directory with test prompt files
        self.tmpdir = tempfile.mkdtemp()
        self.loader = PromptLoader(prompt_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_prompt(self, name, content):
        """Helper to write a prompt file."""
        path = os.path.join(self.tmpdir, "{}.md".format(name))
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_load_existing_prompt(self):
        """Load a prompt that exists."""
        self._write_prompt("test", "Hello, this is a test prompt.")
        result = self.loader.load("test")
        self.assertEqual(result, "Hello, this is a test prompt.")

    def test_load_prompt_with_newlines(self):
        """Load a multi-line prompt."""
        content = "Line 1\nLine 2\nLine 3\n"
        self._write_prompt("multiline", content)
        result = self.loader.load("multiline")
        self.assertEqual(result, content)

    def test_load_missing_prompt_raises(self):
        """Loading a non-existent prompt raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.loader.load("nonexistent")

    def test_load_prompt_without_md_extension(self):
        """Load prompt by name without .md extension."""
        self._write_prompt("build", "Build prompt content")
        result = self.loader.load("build")
        self.assertEqual(result, "Build prompt content")

    def test_load_prompt_with_unicode(self):
        """Load a prompt containing unicode characters."""
        content = "Hello \u4e16\u754c \u2014 em dash test"
        self._write_prompt("unicode", content)
        result = self.loader.load("unicode")
        self.assertEqual(result, content)


class TestPromptLoaderTemplate(unittest.TestCase):
    """Test PromptLoader.load_with_template() method."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.loader = PromptLoader(prompt_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_prompt(self, name, content):
        path = os.path.join(self.tmpdir, "{}.md".format(name))
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_template_single_variable(self):
        """Substitute a single template variable."""
        self._write_prompt("greeting", "Hello, {name}!")
        result = self.loader.load_with_template("greeting", {"name": "World"})
        self.assertEqual(result, "Hello, World!")

    def test_template_multiple_variables(self):
        """Substitute multiple template variables."""
        self._write_prompt(
            "info",
            "OS: {os}, Python: {py}, Dir: {cwd}",
        )
        result = self.loader.load_with_template(
            "info", {"os": "Linux", "py": "3.8", "cwd": "/home"}
        )
        self.assertEqual(result, "OS: Linux, Python: 3.8, Dir: /home")

    def test_template_partial_substitution_raises(self):
        """Partial substitution raises KeyError for missing variables."""
        self._write_prompt("partial", "Hello {name}, age {age}")
        # Python str.format raises KeyError for missing keys
        with self.assertRaises(KeyError):
            self.loader.load_with_template(
                "partial", {"name": "Alice"}
            )

    def test_template_no_variables_in_prompt(self):
        """Prompt with no template variables returns unchanged."""
        self._write_prompt("static", "No variables here.")
        result = self.loader.load_with_template("static", {"unused": "value"})
        self.assertEqual(result, "No variables here.")

    def test_template_empty_variables_dict(self):
        """Empty variables dict returns prompt unchanged."""
        self._write_prompt("test", "Hello {name}")
        result = self.loader.load_with_template("test", {})
        self.assertEqual(result, "Hello {name}")

    def test_template_os_environment(self):
        """Test the {os_environment} variable used by build agent."""
        self._write_prompt("build", "Build agent\n\n{os_environment}")
        os_env = "## Runtime Environment\n- OS: Windows"
        result = self.loader.load_with_template(
            "build", {"os_environment": os_env}
        )
        self.assertIn("## Runtime Environment", result)
        self.assertIn("- OS: Windows", result)
        self.assertNotIn("{os_environment}", result)


class TestPromptLoaderDiscover(unittest.TestCase):
    """Test PromptLoader.discover_prompts() method."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.loader = PromptLoader(prompt_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_prompt(self, name, content=""):
        path = os.path.join(self.tmpdir, "{}.md".format(name))
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_discover_empty_directory(self):
        """Discover prompts in an empty directory returns empty list."""
        result = self.loader.discover_prompts()
        self.assertEqual(result, [])

    def test_discover_single_prompt(self):
        """Discover a single prompt."""
        self._write_prompt("build", "content")
        result = self.loader.discover_prompts()
        self.assertEqual(result, ["build"])

    def test_discover_multiple_prompts_sorted(self):
        """Discover multiple prompts, returned in sorted order."""
        self._write_prompt("zebra", "z")
        self._write_prompt("alpha", "a")
        self._write_prompt("middle", "m")
        result = self.loader.discover_prompts()
        self.assertEqual(result, ["alpha", "middle", "zebra"])

    def test_discover_ignores_non_md_files(self):
        """Non-.md files are ignored."""
        self._write_prompt("build", "content")
        # Write a non-.md file
        with open(os.path.join(self.tmpdir, "readme.txt"), "w") as f:
            f.write("not a prompt")
        result = self.loader.discover_prompts()
        self.assertEqual(result, ["build"])

    def test_discover_nonexistent_directory(self):
        """Discover prompts in a non-existent directory returns empty list."""
        loader = PromptLoader(prompt_dir="/nonexistent/path/that/does/not/exist")
        result = loader.discover_prompts()
        self.assertEqual(result, [])


class TestConvenienceFunctions(unittest.TestCase):
    """Test module-level convenience functions."""

    def test_load_prompt_default_loader(self):
        """load_prompt() uses the default loader."""
        # The default prompts directory should exist in the project
        result = load_prompt("build")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_load_prompt_with_variables(self):
        """load_prompt() with variables substitutes template."""
        result = load_prompt("build", {"os_environment": "test env"})
        self.assertIn("test env", result)
        self.assertNotIn("{os_environment}", result)

    def test_discover_prompts_default_loader(self):
        """discover_prompts() returns available prompts."""
        result = discover_prompts()
        self.assertIsInstance(result, list)
        # Should include all 7 built-in prompts
        for name in ["build", "plan", "general", "explore", "compaction", "title", "summary"]:
            self.assertIn(name, result)


if __name__ == "__main__":
    unittest.main()
