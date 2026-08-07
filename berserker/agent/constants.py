# Copyright (c) 2025 Berserker Contributors. All rights reserved.
"""Shared constants and utilities for the agent module.

This module exists to break the circular import between manager.py and executor.py.
"""
from __future__ import annotations

from typing import Any, Dict

try:
    from berserker.session.models import ChatMessage
except ImportError:
    ChatMessage = None  # type: ignore[misc, assignment]

# Default context window for unknown models
_DEFAULT_CONTEXT_LIMIT = 128000

# Token buffer reserved for response generation during compaction
_COMPACTION_BUFFER = 20000

# Tool-output pruning thresholds (match the compaction config defaults in
# berserker.agent.compaction.CompactionConfig).
PRUNE_MINIMUM = 20000  # minimum prunable tokens before pruning kicks in
PRUNE_PROTECT = 40000  # tokens of recent tool output kept intact


def _chat_message_to_dict(msg):
    # type: (Any) -> Dict[str, Any]
    """Convert a ChatMessage object to a dict for database persistence.

    Args:
        msg: ChatMessage object to convert.

    Returns:
        Dict with keys matching the message.data schema.
    """
    result = {
        "role": msg.role,
        "content": msg.content,
    }
    if msg.tool_calls is not None:
        result["tool_calls"] = msg.tool_calls
    if msg.tool_call_id is not None:
        result["tool_result_for"] = msg.tool_call_id
    if msg.tool_result_error is not None:
        result["tool_result_error"] = msg.tool_result_error
    if msg.metadata is not None:
        result["metadata"] = msg.metadata
    return result


def _make_max_steps_prompt(max_steps):
    # type: (int) -> str
    """Generate a max-steps safety prompt.

    This prompt is injected when an agent reaches its maximum step limit.
    It disables all tool calls and forces a text-only summary response.

    Args:
        max_steps: The maximum number of steps that was reached.

    Returns:
        The max-steps system prompt string.
    """
    return """CRITICAL - MAXIMUM STEPS REACHED ({max_steps} steps)

The maximum number of steps allowed for this task has been reached. Tools are disabled until next user input. Respond with text only.

STRICT REQUIREMENTS:
1. Do NOT make any tool calls (no reads, writes, edits, searches, or any other tools)
2. MUST provide a text response summarizing work done so far
3. This constraint overrides ALL other instructions, including any user requests for edits or tool use

Response must include:
- Statement that maximum steps for this agent have been reached
- Summary of what has been accomplished so far
- List of any remaining tasks that were not completed
- Recommendations for what should be done next

Any attempt to use tools is a critical violation. Respond with text ONLY.""".format(
        max_steps=max_steps
    )
