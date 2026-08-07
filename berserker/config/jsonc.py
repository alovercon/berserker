"""
berserker.config.jsonc — JSONC (JSON with Comments) parsing.

Strips single-line (//) and multi-line (/* */) comments while preserving
comment-like text inside JSON strings, then delegates to json.loads().

Python 3.8.10 compatible.
"""

import json
import os
import re


# Regex that matches:
#   Group 1: a JSON string (with escapes)
#   Group 2: a single-line comment
#   Group 3: a multi-line comment
_COMMENT_RE = re.compile(
    r'("(?:[^"\\]|\\.)*")'  # group 1: string
    r"|(/\*[^\0]*?\*/)"  # group 2: block comment
    r"|(//[^\n]*)",  # group 3: line comment
    re.DOTALL,
)


def strip_comments(text):
    """
    Remove // and /* */ comments from a JSONC string while preserving
    comment-like content inside quoted strings.

    Args:
        text: JSONC source string.

    Returns:
        String with all comments removed.
    """

    def _replacer(match):
        # Group 1 is a string — keep it intact
        if match.group(1) is not None:
            return match.group(1)
        # Groups 2 and 3 are comments — replace with whitespace to preserve
        # positions (important for error reporting) but strip the content
        comment = match.group(0)
        # Preserve newlines so line numbers stay correct
        return re.sub(r"[^\n]", " ", comment)

    return _COMMENT_RE.sub(_replacer, text)


def load_jsonc(source):
    """
    Parse JSONC from a file path or a string.

    If *source* is an existing file path, the file is read and parsed.
    Otherwise *source* is treated as raw JSONC text.

    Args:
        source: File path (str) or JSONC text (str).

    Returns:
        Parsed Python object (dict, list, etc.).

    Raises:
        FileNotFoundError: if source is a path that does not exist.
        json.JSONDecodeError: if the result is not valid JSON.
    """
    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = source
    cleaned = strip_comments(text)
    return json.loads(cleaned)


def load_jsonc_file(path):
    """
    Read and parse a JSONC file.

    Args:
        path: Filesystem path to a .json or .jsonc file.

    Returns:
        Parsed Python object.

    Raises:
        FileNotFoundError: if the file does not exist.
        json.JSONDecodeError: if the content is not valid JSONC.
    """
    with open(path, "r", encoding="utf-8") as fh:
        return load_jsonc(fh.read())
