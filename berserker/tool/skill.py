"""
Skill tool for berserker: Load skill instructions on demand.

Allows the LLM to load specialized skill markdown files that provide
domain-specific instructions and workflows.

Parameters:
    skill_name (str, required): Name of the skill to load.

Python 3.8.10 compatible: uses type comments, Optional, no match/case.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Try to import yaml for frontmatter parsing
HAS_YAML = False
try:
    import yaml  # type: ignore

    HAS_YAML = True
except ImportError:
    pass

from berserker.paths import get_config_dir
from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolError, ToolResult


def _get_skill_bases():
    # type: () -> List[str]
    """Get all base directories to search for skills.

    Search order:
      1. Local project: .berserker/skills/
      2. Global config: {config_dir}/skills/
      3. Custom paths from config: skills.paths

    Returns:
        List of base directory paths.
    """
    bases = [".berserker"]
    config_dir = get_config_dir()
    if os.path.isdir(config_dir):
        bases.append(config_dir)

    # Load custom paths from config
    try:
        from berserker.config.loader import load_config

        config = load_config()
        custom_paths = config.get("skills", {}).get("paths", [])  # type: List[str]
        for custom_path in custom_paths:
            # Expand ~ to home directory
            if custom_path.startswith("~/"):
                custom_path = os.path.join(os.path.expanduser("~"), custom_path[2:])
            # Relative paths are relative to cwd
            if not os.path.isabs(custom_path):
                custom_path = os.path.join(os.getcwd(), custom_path)
            if os.path.isdir(custom_path):
                bases.append(custom_path)
    except Exception as e:
        logger.warning("Failed to load custom skill paths from config: %s", e)
        pass  # Config loading failure should not break skill discovery

    return bases


# Default skill search paths (used for glob matching)
_SKILL_PATHS = [
    os.path.join(".berserker", "skills", "*", "SKILL.md"),
    os.path.join(".berserker", "skills", "*", "skill.md"),
]  # type: List[str]


def _find_skill(skill_name):
    # type: (str) -> Optional[str]
    """Find a skill markdown file by name.

    Searches in:
      1. .berserker/skills/<skill_name>/SKILL.md (local project)
      2. {config_dir}/skills/<skill_name>/SKILL.md (global config)

    Args:
        skill_name: Name of the skill to find.

    Returns:
        Path to the skill markdown file, or None if not found.
    """
    # Try exact path first in all base directories
    for base in _get_skill_bases():
        for filename in ["SKILL.md", "skill.md", "README.md"]:
            path = os.path.join(base, "skills", skill_name, filename)
            if os.path.isfile(path):
                return path

    # Try glob pattern matching
    for pattern in _SKILL_PATHS:
        matches = glob.glob(pattern)
        for match in matches:
            # Check if the skill name matches the directory name
            dir_name = os.path.basename(os.path.dirname(match))
            if dir_name.lower() == skill_name.lower():
                return match

    return None


def _load_skill(path):
    # type: (str) -> str
    """Load and return the content of a skill markdown file.

    Args:
        path: Path to the skill markdown file.

    Returns:
        Content of the skill file as a string (without frontmatter).
    """
    parsed = _parse_frontmatter(path)
    return parsed["content"]


def _list_available_skills():
    # type: () -> List[Dict[str, str]]
    """List all available skills.

    Searches in both local project and global config directories.

    Returns:
        List of dicts with 'name', 'path', and 'description' keys.
    """
    skills = []  # type: List[Dict[str, str]]
    seen = set()  # type: set
    for base in _get_skill_bases():
        skills_dir = os.path.join(base, "skills")
        if os.path.isdir(skills_dir):
            for entry in os.listdir(skills_dir):
                if entry in seen:
                    continue
                skill_path = os.path.join(skills_dir, entry)
                if os.path.isdir(skill_path):
                    for filename in ["SKILL.md", "skill.md", "README.md"]:
                        md_path = os.path.join(skill_path, filename)
                        if os.path.isfile(md_path):
                            # Parse frontmatter to get name and description
                            parsed = _parse_frontmatter(md_path)
                            skills.append(
                                {
                                    "name": parsed["name"],
                                    "path": md_path,
                                    "description": parsed["description"],
                                }
                            )
                            seen.add(entry)
                            break
    return skills


def _format_skills_concise(skills):
    # type: (List[Dict[str, str]]) -> str
    """Format skills list as concise markdown for tool description."""
    if not skills:
        return ""
    lines = ["## Available Skills"]
    for skill in skills:
        name = skill.get("name", "unknown")
        desc = skill.get("description", "")
        if desc:
            # Truncate long descriptions
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append("- **{}**: {}".format(name, desc))
        else:
            lines.append("- **{}**".format(name))
    return "\n".join(lines)


def _path_to_file_url(path):
    # type: (str) -> str
    """Convert a filesystem path to a file:// URL."""
    normalized = path.replace("\\", "/")
    return "file://{}".format(normalized)


def _format_skills_verbose(skills):
    # type: (List[Dict[str, str]]) -> str
    """Format skills list as verbose XML for system prompt injection.

    Args:
        skills: List of dicts with 'name', 'description', 'path' keys.

    Returns:
        XML-formatted string for LLM ingestion.
    """
    if not skills:
        return ""
    lines = ["<available_skills>"]
    for skill in skills:
        name = skill.get("name", "unknown")
        desc = skill.get("description", "")
        path = skill.get("path", "")
        location = _path_to_file_url(path)
        lines.append("  <skill>")
        lines.append("    <name>{}</name>".format(name))
        if desc:
            lines.append("    <description>{}</description>".format(desc))
        lines.append("    <location>{}</location>".format(location))
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def _filter_skills_by_permission(skills, agent_tools):
    # type: (List[Dict[str, str]], List[str]) -> List[Dict[str, str]]
    """Filter skills based on agent's allowed tools.

    If agent doesn't have 'skill' in its tool list, return empty list.
    Future: support per-skill permission policies.

    Args:
        skills: Full list of available skills.
        agent_tools: List of tool IDs allowed for this agent.

    Returns:
        Filtered list of skills.
    """
    if "skill" not in agent_tools:
        return []
    return skills


def _list_skill_files(skill_dir):
    # type: (str) -> List[str]
    """List files in a skill directory (excluding SKILL.md).

    Args:
        skill_dir: Path to the skill directory.

    Returns:
        List of file paths (up to 10 files).
    """
    files = []  # type: List[str]
    try:
        for entry in os.listdir(skill_dir):
            full_path = os.path.join(skill_dir, entry)
            if os.path.isfile(full_path) and entry.upper() != "SKILL.MD":
                files.append(full_path)
                if len(files) >= 10:
                    break
    except (IOError, OSError):
        pass
    return files


def _parse_frontmatter(path):
    # type: (str) -> Dict[str, Any]
    """Parse YAML frontmatter from a skill file.

    Returns dict with:
      - 'name': skill name (from frontmatter or derived from directory name)
      - 'description': skill description (from frontmatter or empty string)
      - 'content': markdown body WITHOUT frontmatter
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, OSError) as e:
        return {
            "name": os.path.basename(os.path.dirname(path)),
            "description": "",
            "content": "Error reading skill file: {}".format(str(e)),
        }

    # Check if file starts with frontmatter delimiter
    if not content.startswith("---"):
        # No frontmatter, return whole content as body
        return {
            "name": os.path.basename(os.path.dirname(path)),
            "description": "",
            "content": content,
        }

    # Find the end of the frontmatter block
    lines = content.splitlines(True)  # Keep line endings
    if len(lines) < 2:
        # Not enough lines for proper frontmatter
        return {
            "name": os.path.basename(os.path.dirname(path)),
            "description": "",
            "content": content,
        }

    # Look for the closing --- delimiter
    frontmatter_end = -1
    for i in range(1, len(lines)):
        line = lines[i].strip()
        if line == "---":
            frontmatter_end = i
            break

    if frontmatter_end == -1:
        # No closing delimiter found, treat as no frontmatter
        return {
            "name": os.path.basename(os.path.dirname(path)),
            "description": "",
            "content": content,
        }

    # Extract frontmatter and content
    frontmatter_text = "".join(lines[1:frontmatter_end])
    markdown_body = "".join(lines[frontmatter_end + 1 :])

    result = {
        "name": os.path.basename(os.path.dirname(path)),
        "description": "",
        "content": markdown_body,
    }

    # Try to parse with YAML first
    if HAS_YAML:
        try:
            import yaml as yaml_lib  # type: ignore

            parsed = yaml_lib.safe_load(frontmatter_text)
            if isinstance(parsed, dict):
                if "name" in parsed:
                    result["name"] = parsed["name"]
                if "description" in parsed:
                    result["description"] = parsed["description"]
                return result
        except Exception as e:
            # YAML parsing failed, fall back to regex
            logger.warning("YAML frontmatter parsing failed, using regex fallback: %s", e)
            pass

    # Regex fallback for name and description
    name_match = re.search(r"^name:\s*(.+)$", frontmatter_text, re.MULTILINE)
    if name_match:
        result["name"] = name_match.group(1).strip()

    desc_match = re.search(r"^description:\s*(.+)$", frontmatter_text, re.MULTILINE)
    if desc_match:
        result["description"] = desc_match.group(1).strip()

    return result


def register_skill_tool(registry):
    # type: (Any) -> None
    """Register all skills as individual tools with the given ToolRegistry.

    Scans all skill directories, parses SKILL.md files, and registers
    each skill as a separate Tool instance.

    Args:
        registry: A ToolRegistry instance to register tools with.
    """
    all_skills = _list_available_skills()
    for skill_info in all_skills:
        skill_tool = SkillAsTool(
            skill_name=skill_info["name"],
            skill_path=skill_info["path"],
            description=skill_info["description"],
        )
        registry.register(skill_tool)


class SkillAsTool(Tool):
    """Individual skill registered as a separate tool.

    Each skill becomes its own tool with ID = skill name.
    When LLM calls this tool, the full SKILL.md content is injected
    into the system prompt for that round only.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=60, max_output_tokens=32768)

    def __init__(self, skill_name, skill_path, description=""):
        # type: (str, str, str) -> None
        self._skill_name = skill_name
        self._skill_path = skill_path
        self._skill_description = description

        super(SkillAsTool, self).__init__(
            id=skill_name,  # Tool ID is the skill name
            description=(
                "Load specialized instructions for: {name}. {desc} "
                "This tool takes NO parameters - simply call it to load the skill's "
                "full instructions into context. After loading, follow the skill's "
                "instructions which may direct you to use other tools (like 'task' for "
                "spawning subagents). Do NOT pass task parameters to this tool."
            ).format(
                name=skill_name,
                desc=description
                if description
                else "Provides domain-specific instructions and workflows.",
            ),
            parameters={
                "type": "object",
                "properties": {},  # No parameters needed - skill is self-contained
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute the skill tool - returns success with full SKILL.md content.

        The actual content injection happens in manager.py when it detects
        this tool call. Here we just return a success message.

        Args:
            args: Empty dict (no parameters).
            ctx: ToolContext with session info.

        Returns:
            ToolResult with success message.
        """
        # Load full skill content
        content = _load_skill(self._skill_path)
        skill_dir = os.path.dirname(self._skill_path)

        # Build output
        base_url = "file://{}".format(skill_dir.replace("\\", "/"))
        output = (
            '<skill_content name="{}">\n'
            "# Skill: {}\n\n"
            "{}\n\n"
            "Base directory for this skill: {}\n"
            "Relative paths in this skill (e.g., scripts/, reference/) are "
            "relative to this base directory.\n"
            "</skill_content>"
        ).format(self._skill_name, self._skill_name, content.strip(), base_url)

        return ToolResult(
            title="Skill Executed: {}".format(self._skill_name),
            output=output,
            metadata={
                "skill_name": self._skill_name,
                "skill_path": self._skill_path,
                "content_length": len(content),
            },
        )
