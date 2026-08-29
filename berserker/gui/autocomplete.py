"""
berserker.gui.autocomplete — Command/skill autocomplete logic for the GUI input.

Provides pure functions that aggregate slash-command candidates (from the
command registry) and skill candidates (from installed skills), filter them
by prefix, and format them for display. Kept free of any wx import so the
filtering logic is unit-testable without a display.

Python 3.8.10 compatible: uses type comments, no | union syntax.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Limits
MAX_SUGGESTIONS = 12  # type: int


class Suggestion(object):
    """A single autocomplete suggestion.

    Attributes:
        label: The text inserted on completion (with leading '/', e.g. "/help").
        display: The text shown in the dropdown list.
        kind: "command" or "skill".
        description: Optional one-line description shown alongside.
    """

    def __init__(self, label, display, kind, description=""):
        # type: (str, str, str, str) -> None
        self.label = label  # type: str
        self.display = display  # type: str
        self.kind = kind  # type: str
        self.description = description  # type: str

    def as_dict(self):
        # type: () -> Dict[str, str]
        """Serialize to a dict (handy for tests and logging)."""
        return {
            "label": self.label,
            "display": self.display,
            "kind": self.kind,
            "description": self.description,
        }


def _command_suggestions():
    # type: () -> List[Suggestion]
    """Build suggestions from the registered command registry.

    Returns:
        Suggestion list with kind="command", label="/<name>".
    """
    from berserker.command.registry import command_registry

    suggestions = []  # type: List[Suggestion]
    for cmd in command_registry.list_all().values():
        hint = ""
        if cmd.argument_hint:
            hint = " " + cmd.argument_hint
        suggestions.append(
            Suggestion(
                label="/" + cmd.name,
                display="/{}{}".format(cmd.name, hint),
                kind="command",
                description=cmd.description,
            )
        )
    return suggestions


def _skill_suggestions():
    # type: () -> List[Suggestion]
    """Build suggestions from installed skills.

    Returns:
        Suggestion list with kind="skill", label="/<skill-name>".
    """
    from berserker.tool.skill import _list_available_skills

    suggestions = []  # type: List[Suggestion]
    try:
        for skill in _list_available_skills():
            name = skill.get("name", "")
            if not name:
                continue
            suggestions.append(
                Suggestion(
                    label="/" + name,
                    display="/" + name,
                    kind="skill",
                    description=skill.get("description", ""),
                )
            )
    except Exception as e:
        logger.debug("Skill suggestion scan failed: %s", e)
    return suggestions


def get_candidates(query):
    # type: (str) -> List[Suggestion]
    """Return autocomplete candidates for the given query.

    Args:
        query: Text after the leading '/', e.g. "ag" for "/ag...".
               Empty string returns all candidates.

    Returns:
        Sorted, deduplicated list of Suggestion objects, capped at
        MAX_SUGGESTIONS. Command suggestions come first; skills follow.
    """
    prefix = (query or "").lstrip("/").lower()
    registry_cmds = []  # type: List[Suggestion]
    skills = []  # type: List[Suggestion]

    try:
        registry_cmds = _command_suggestions()
    except Exception as e:
        logger.debug("Command suggestion scan failed: %s", e)

    try:
        skills = _skill_suggestions()
    except Exception as e:
        logger.debug("Skill suggestion scan failed: %s", e)

    candidates = []  # type: List[Suggestion]
    seen_labels = set()  # type: set

    for s in registry_cmds + skills:
        # Match on the name only (label without '/'); allow substring match
        # on name for a friendlier search experience.
        name = s.label.lstrip("/")
        if prefix and prefix not in name.lower():
            continue
        if s.label in seen_labels:
            continue
        seen_labels.add(s.label)
        candidates.append(s)

    # Commands first, then skills; alphabetical within each kind.
    candidates.sort(
        key=lambda s: (0 if s.kind == "command" else 1, s.label.lower())
    )
    return candidates[:MAX_SUGGESTIONS]


def get_query_from_text(text, cursor_pos=None):
    # type: (str, Optional[int]) -> Optional[str]
    """Extract the slash-command query from the input text.

    If the cursor is inside a slash command word (the whitespace-delimited
    token containing the cursor starts with '/'), returns the query part
    after the leading '/' — i.e. the text between '/' and the cursor within
    that token. Returns "" when the user just typed "/" (show all
    candidates). Returns None when the cursor is not inside a slash command.

    Args:
        text: Full input text.
        cursor_pos: Character position of the cursor (default: end of text).

    Returns:
        Query string after '/', "" for a bare "/", or None when not in a
        slash command.
    """
    if cursor_pos is None:
        cursor_pos = len(text)

    # Find the whitespace-delimited token that contains the cursor.
    token_start = cursor_pos
    while token_start > 0 and text[token_start - 1] not in (" ", "\n", "\t"):
        token_start -= 1

    # Look ahead from the cursor to the end of the same token (so a programmatic
    # SetValue that leaves the insertion point at the start still works).
    token_end = cursor_pos
    while token_end < len(text) and text[token_end] not in (" ", "\n", "\t"):
        token_end += 1

    token = text[token_start:token_end]
    if not token.startswith("/"):
        return None

    # Query is the part of the token before the cursor (after '/').
    query_end = max(token_start + 1, cursor_pos)
    query = text[token_start + 1:query_end]
    return query
