"""
Output truncation utility for berserker tool system.

Truncates tool output when it exceeds a maximum token limit.
Uses tiktoken for accurate token counting when available,
falls back to character-based estimation otherwise.

Python 3.8.10 compatible: uses type comments, Optional, no match/case.
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_TOKENS = 4096
DEFAULT_COMPACTION_BUFFER = 20000
TRUNCATION_MARKER = "\n\n[Output truncated: exceeded {max} tokens]"


# ---------------------------------------------------------------------------
# Dynamic Token Calculation
# ---------------------------------------------------------------------------


def calculate_dynamic_max_tokens(context_info, default_max, default_buffer):
    # type: (Optional[Dict[str, Any]], int, int) -> int
    """Calculate dynamic max tokens based on context information.

    Args:
        context_info: Optional dict with 'current_tokens', 'context_limit', 'compaction_buffer'
        default_max: Default maximum tokens (e.g., 4096)
        default_buffer: Default compaction buffer when context_info is None

    Returns:
        Maximum tokens available, at least default_max
    """
    if context_info is None:
        return default_max

    current_tokens = int(context_info.get("current_tokens", 0))
    context_limit = int(context_info.get("context_limit", 0))
    compaction_buffer = int(context_info.get("compaction_buffer", default_buffer))

    available = context_limit - current_tokens - compaction_buffer
    return max(available, default_max)


# ---------------------------------------------------------------------------
# Token Counting
# ---------------------------------------------------------------------------

# Detect tiktoken availability once at module load time
_TIKTOKEN_AVAILABLE = False
try:
    import tiktoken as _tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    pass


def _count_tokens_tiktoken(text):
    # type: (str) -> int
    """Count tokens using tiktoken (accurate)."""
    if not _TIKTOKEN_AVAILABLE:
        return -1
    try:
        enc = _tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception as e:
        logger.warning("tiktoken encoding error: %s", e)
        return -1


def _count_tokens_estimate(text):
    # type: (str) -> int
    """Estimate token count using character-based heuristic.

    Rough approximation: ~4 characters per token for English text.
    """
    return max(1, len(text) // 4)


def count_tokens(text):
    # type: (str) -> int
    """Count tokens in text, using tiktoken if available.

    Args:
        text: The text to count tokens for.

    Returns:
        Approximate token count.
    """
    result = _count_tokens_tiktoken(text)
    if result >= 0:
        return result
    return _count_tokens_estimate(text)


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def truncate_output(text, max_tokens=None):
    # type: (str, Optional[int]) -> Tuple[str, bool]
    """Truncate text output if it exceeds the maximum token limit.

    If the text fits within max_tokens, returns it unchanged.
    Otherwise, truncates from the end and appends a truncation marker.

    Args:
        text: The text output to potentially truncate.
        max_tokens: Maximum number of tokens allowed (default: 4096).

    Returns:
        Tuple of (truncated_text, was_truncated).
        If was_truncated is True, the text has been shortened and
        a truncation marker appended.
    """
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS

    if text is None:
        return "", False

    total_tokens = count_tokens(text)
    if total_tokens <= max_tokens:
        return text, False

    logger.info(
        "[TRUNCATE] Output exceeds limit: %d tokens > %d max (text len: %d)",
        total_tokens, max_tokens, len(text),
    )

    # Binary search for the truncation point
    # We need to find the largest prefix that fits within max_tokens
    # Reserve space for the truncation marker
    marker = TRUNCATION_MARKER.format(max=max_tokens)
    marker_tokens = count_tokens(marker)
    available = max_tokens - marker_tokens

    if available <= 0:
        # Even the marker doesn't fit, return just the marker
        return marker, True

    # Binary search on character position
    low = 0
    high = len(text)
    best_cut = 0

    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid]
        candidate_tokens = count_tokens(candidate)
        if candidate_tokens <= available:
            best_cut = mid
            low = mid + 1
        else:
            high = mid - 1

    # Trim to complete lines if possible
    truncated = text[:best_cut]
    last_newline = truncated.rfind("\n")
    if last_newline > len(truncated) // 2:
        truncated = truncated[:last_newline]

    result = truncated + marker
    return result, True


def truncate_result(result, max_tokens=None):
    # type: (Dict[str, Any], Optional[int]) -> Dict[str, Any]
    """Truncate a tool result dict if output exceeds max_tokens.

    This is the version used by ToolRegistry.execute() to process
    ToolResult objects returned as dicts.

    Args:
        result: Dict with 'output' key (and optionally 'metadata').
        max_tokens: Maximum tokens allowed (default: 4096).

    Returns:
        Modified result dict with truncated output and metadata flag.
    """
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS

    output = result.get("output", "")
    truncated_output, was_truncated = truncate_output(output, max_tokens)

    if was_truncated:
        result["output"] = truncated_output
        if "metadata" not in result:
            result["metadata"] = {}
        result["metadata"]["truncated"] = True

    return result
