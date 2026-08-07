"""
PromptLoader — Load external system prompts from markdown files.

Provides:
- PromptLoader class for loading prompts by name with template variable substitution
- Module-level convenience functions: load_prompt(), discover_prompts()
- Fallback to inline prompts from factory.py when external files are missing

Python 3.8.10 compatible: uses typing module, no walrus, no match/case.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional


class PromptLoader(object):
    """Loads system prompts from external markdown files.

    Prompts are stored in a directory (default: prompts/ sibling to this file).
    Each prompt is a .md file named after the agent (e.g., build.md, plan.md).

    Supports template variable substitution: {variable} placeholders in the
    prompt text are replaced with values from a provided dictionary.

    Usage:
        loader = PromptLoader()
        prompt = loader.load("build")
        prompt = loader.load_with_template("build", {"os_environment": "..."})
    """

    def __init__(self, prompt_dir=None):
        # type: (Optional[str]) -> None
        """Initialize the PromptLoader.

        Args:
            prompt_dir: Path to the directory containing prompt .md files.
                        Defaults to the 'prompts/' directory sibling to this file.
        """
        if prompt_dir is None:
            prompt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
        self.prompt_dir = prompt_dir

    def load(self, name):
        # type: (str) -> str
        """Load a prompt by name (without .md extension).

        Args:
            name: Prompt name (e.g., "build", "plan", "general").

        Returns:
            The prompt text as a string.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
        """
        file_path = os.path.join(self.prompt_dir, "{}.md".format(name))
        if not os.path.exists(file_path):
            raise FileNotFoundError(
                "Prompt file not found: {}".format(file_path)
            )
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def load_with_template(self, name, variables):
        # type: (str, Dict[str, str]) -> str
        """Load a prompt and substitute template variables.

        Template variables use {variable} syntax in the prompt text.
        Only variables present in the prompt will be substituted; extra
        variables in the dict are ignored.

        Args:
            name: Prompt name (e.g., "build", "plan").
            variables: Dictionary mapping variable names to their values.

        Returns:
            The prompt text with variables substituted.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
        """
        prompt = self.load(name)
        if not variables:
            return prompt
        return prompt.format(**variables)

    def discover_prompts(self):
        # type: () -> List[str]
        """List available prompt names from the prompt directory.

        Returns:
            Sorted list of prompt names (without .md extension).
            Returns empty list if directory does not exist.
        """
        if not os.path.isdir(self.prompt_dir):
            return []
        names = []
        for filename in os.listdir(self.prompt_dir):
            if filename.endswith(".md"):
                names.append(filename[:-3])
        return sorted(names)


# Module-level convenience functions

_default_loader = None  # type: Optional[PromptLoader]


def _get_default_loader():
    # type: () -> PromptLoader
    """Get or create the default PromptLoader (lazy singleton)."""
    global _default_loader
    if _default_loader is None:
        _default_loader = PromptLoader()
    return _default_loader


def load_prompt(name, variables=None):
    # type: (str, Optional[Dict[str, str]]) -> str
    """Load a prompt by name with optional template variable substitution.

    Convenience function using the default PromptLoader.

    Args:
        name: Prompt name (e.g., "build", "plan").
        variables: Optional dictionary of template variables.

    Returns:
        The prompt text (with variables substituted if provided).

    Raises:
        FileNotFoundError: If the prompt file does not exist.
    """
    loader = _get_default_loader()
    if variables:
        return loader.load_with_template(name, variables)
    return loader.load(name)


def discover_prompts():
    # type: () -> List[str]
    """List available prompt names from the default prompt directory.

    Returns:
        Sorted list of prompt names.
    """
    return _get_default_loader().discover_prompts()
