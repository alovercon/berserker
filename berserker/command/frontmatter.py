"""
YAML frontmatter parser for Markdown-based command files.

Parses standard YAML frontmatter blocks delimited by ``---`` at the
start of a file. Returns the parsed dictionary and the remaining body
content. Gracefully handles missing or malformed YAML.

Python 3.8.10 compatible: uses type comments, typing module imports.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

import yaml

logger = logging.getLogger(__name__)

# Regex-free frontmatter detection: look for leading "---\n" marker
_FM_START = "---\n"
_FM_END = "\n---"


def parse_frontmatter(content):
    # type: (str) -> Tuple[Dict[str, Any], str]
    """Parse YAML frontmatter from markdown content.

    Detects a YAML frontmatter block delimited by ``---`` markers
    at the very beginning of the string. If the content does not
    start with ``---``, returns the full content as body with an
    empty frontmatter dict.

    Args:
        content: Raw string content of a Markdown file.

    Returns:
        A tuple of (frontmatter_dict, body_string).
        - frontmatter_dict: Parsed YAML data (empty dict if none or invalid).
        - body_string: Content after the frontmatter block (or full content
          if no frontmatter was detected).
    """
    if not content:
        return {}, ""

    # Check if content starts with frontmatter marker
    if not content.startswith(_FM_START):
        return {}, content

    # Find the closing marker
    end_idx = content.find(_FM_END, len(_FM_START))
    if end_idx == -1:
        # Opening marker but no closing marker — treat as body
        return {}, content

    # Extract YAML section and body
    yaml_text = content[len(_FM_START):end_idx]
    body = content[end_idx + len(_FM_END):]

    # If body starts with a newline, strip exactly one
    if body.startswith("\n"):
        body = body[1:]

    # Parse YAML — return empty dict on any failure
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse YAML frontmatter: %s", exc)
        return {}, content

    if not isinstance(data, dict):
        # yaml.safe_load can return None, scalar, or list — treat as empty
        logger.warning(
            "YAML frontmatter did not produce a dict (got %s); ignoring",
            type(data).__name__,
        )
        return {}, body

    # Normalise hyphenated keys to underscored keys for Python access
    normalised = {}  # type: Dict[str, Any]
    for key, value in data.items():
        if isinstance(key, str) and "-" in key:
            normalised[key.replace("-", "_")] = value
        else:
            normalised[key] = value

    return normalised, body
