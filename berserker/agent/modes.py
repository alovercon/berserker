"""
Agent mode enumeration for berserker.

Defines:
- AgentMode enum: PRIMARY, SUBAGENT, HIDDEN
- AgentVisibility enum: VISIBLE, HIDDEN
- Helper functions: is_primary(), is_subagent(), is_hidden()

Python 3.8.10 compatible: uses enum.Enum, type comments.
"""

from __future__ import annotations

from enum import Enum


class AgentMode(Enum):
    """Enumeration of agent execution modes.

    Members:
        PRIMARY: Top-level agent that interacts directly with the user.
        SUBAGENT: Agent delegated by a primary agent for subtasks.
        HIDDEN: Internal agent used for system operations (compaction, titles, etc.).
    """

    PRIMARY = "primary"
    SUBAGENT = "subagent"
    HIDDEN = "hidden"

    @classmethod
    def from_string(cls, value):
        # type: (str) -> AgentMode
        """Convert a string to an AgentMode enum value.

        Args:
            value: String representation of the mode (case-insensitive).

        Returns:
            The corresponding AgentMode enum member.

        Raises:
            ValueError: If the string does not match any known mode.
        """
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(
                "Invalid agent mode '{}'. Must be one of: {}".format(
                    value, ", ".join(m.value for m in cls)
                )
            )

    @classmethod
    def valid_values(cls):
        # type: () -> List[str]
        """Return a list of valid mode string values.

        Returns:
            List of valid mode strings.
        """
        return [m.value for m in cls]


class AgentVisibility(Enum):
    """Enumeration of agent visibility levels.

    Members:
        VISIBLE: Agent is shown in UI and agent lists.
        HIDDEN: Agent is internal and not shown to users.
    """

    VISIBLE = "visible"
    HIDDEN = "hidden"


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def is_primary(mode):
    # type: (str) -> bool
    """Check if a mode string represents a primary agent.

    Args:
        mode: Mode string to check.

    Returns:
        True if the mode is 'primary', False otherwise.
    """
    return mode == AgentMode.PRIMARY.value


def is_subagent(mode):
    # type: (str) -> bool
    """Check if a mode string represents a subagent.

    Args:
        mode: Mode string to check.

    Returns:
        True if the mode is 'subagent', False otherwise.
    """
    return mode == AgentMode.SUBAGENT.value


def is_hidden(mode):
    # type: (str) -> bool
    """Check if a mode string represents a hidden agent.

    Args:
        mode: Mode string to check.

    Returns:
        True if the mode is 'hidden', False otherwise.
    """
    return mode == AgentMode.HIDDEN.value


# Import List at module level for type comments
from typing import List  # noqa: E402, F401
