"""
berserker.session.message_utils — Shared message loading utility.

Provides unified dict-to-ChatMessage conversion used by both CLI and GUI
modes, eliminating duplicated conversion logic.

Python 3.8.10 compatible: uses type comments, no | union syntax.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from berserker.provider.base import ChatMessage

logger = logging.getLogger(__name__)


def chat_messages_from_session(messages, filter_empty=False):
    # type: (List[Dict[str, Any]], bool) -> List[ChatMessage]
    """Convert session message dicts to ChatMessage objects.

    Besides straight conversion this repairs tool-call pairing, which the
    chat API strictly requires (e.g. DeepSeek: "An assistant message with
    'tool_calls' must be followed by tool messages responding to each
    'tool_call_id'").  Persisted history can contain broken pairs when a
    turn was interrupted between the assistant tool-call message and the
    tool result (abort, error, process restart):

    - Unanswered tool_calls get a synthetic placeholder tool message
      inserted immediately after the assistant message.
    - Orphan tool messages (no assistant tool_call waiting for them) are
      dropped.

    Args:
        messages: List of message dicts from session.get_messages() or
                  session.build_messages_for_llm().
        filter_empty: If True, skip messages with no content AND no tool
                      data (tool_calls / tool_call_id). Used by callers
                      that have not pre-filtered their messages.

    Returns:
        List of ChatMessage objects ready for LLM API consumption.
    """
    result = []  # type: List[ChatMessage]
    # (tool_call_id, tool_name) pairs declared by the most recent assistant
    # message and not yet answered by a tool message.
    pending = []  # type: list

    def _flush_pending():
        while pending:
            tcid, tname = pending.pop(0)
            result.append(
                ChatMessage(
                    role="tool",
                    content="[Tool call interrupted — no result was recorded]",
                    tool_call_id=tcid,
                    name=tname or None,
                )
            )

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id") or msg.get("tool_result_for")

        # Skip messages with no role
        if not role:
            continue

        if role == "tool":
            if tool_call_id is None:
                continue  # unpairable
            hit = None
            for i, (tcid, _tname) in enumerate(pending):
                if tcid == tool_call_id:
                    hit = i
                    break
            if hit is None:
                # Orphan tool message — no assistant tool_call is waiting
                # for it.  The API rejects unpaired tool messages.
                continue
            pending.pop(hit)
            result.append(
                ChatMessage(
                    role="tool",
                    content=content,
                    tool_call_id=tool_call_id,
                    name=msg.get("name"),
                )
            )
            continue

        # A non-tool message starts here: close any still-unanswered
        # tool_calls of the previous assistant message first.
        _flush_pending()

        # Optional filtering: skip messages with no content and no tool data
        has_tool_calls = tool_calls is not None
        has_tool_id = tool_call_id is not None
        if filter_empty and not content and not has_tool_calls and not has_tool_id:
            continue

        result.append(
            ChatMessage(
                role=role,
                content=content,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                name=msg.get("name"),
                reasoning_content=msg.get("reasoning_content") if role == "assistant" else None,
            )
        )
        if role == "assistant" and tool_calls:
            for tc in tool_calls:
                tcid = tc.get("id") or (tc.get("function", {}) or {}).get("id")
                tname = (tc.get("function", {}) or {}).get("name") or tc.get("name")
                if tcid:
                    pending.append((tcid, tname))

    _flush_pending()
    return result
