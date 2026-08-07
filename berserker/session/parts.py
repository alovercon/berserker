"""
berserker.session.parts — Part-based message model with backward compatibility.

Defines a granular Part model (TextPart, ToolPart, FilePart, CompactionPart,
SnapshotPart) and MessageV2, with conversion functions to/from the legacy
ChatMessage dataclass for backward compatibility.

Python 3.8.10 compatible: uses `from __future__ import annotations`,
`typing.Union` for unions, and type comments for all function signatures.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from berserker.provider.base import ChatMessage


# ---------------------------------------------------------------------------
# Part Types
# ---------------------------------------------------------------------------


@dataclass
class TextPart:
    """A plain text content part.

    Attributes:
        type: Discriminator literal, always "text".
        id: Unique identifier for this part.
        text: The text content.
        metadata: Optional key-value metadata.
    """

    type: str = "text"
    id: str = ""
    text: str = ""
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ToolPart:
    """A tool invocation/result content part.

    Attributes:
        type: Discriminator literal, always "tool".
        id: Unique identifier for this part.
        tool: The tool name that was invoked.
        output: The tool's output/result text.
        tool_call_id: Optional ID linking to the original tool call.
        metadata: Optional key-value metadata.
    """

    type: str = "tool"
    id: str = ""
    tool: str = ""
    output: str = ""
    tool_call_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class FilePart:
    """A file content part.

    Attributes:
        type: Discriminator literal, always "file".
        id: Unique identifier for this part.
        file_path: Path to the file.
        content: The file's content (text).
        metadata: Optional key-value metadata.
    """

    type: str = "file"
    id: str = ""
    file_path: str = ""
    content: str = ""
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class CompactionPart:
    """A conversation compaction/summary content part.

    Attributes:
        type: Discriminator literal, always "compaction".
        id: Unique identifier for this part.
        summary: The compacted summary text.
        tokens_before: Token count before compaction.
        tokens_after: Token count after compaction.
        metadata: Optional key-value metadata.
    """

    type: str = "compaction"
    id: str = ""
    summary: str = ""
    tokens_before: int = 0
    tokens_after: int = 0
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SnapshotPart:
    """A git snapshot/diff content part.

    Attributes:
        type: Discriminator literal, always "snapshot".
        id: Unique identifier for this part.
        snapshot_id: Unique identifier for the snapshot.
        additions: Number of lines added.
        deletions: Number of lines deleted.
        files: Number of files changed.
        metadata: Optional key-value metadata.
    """

    type: str = "snapshot"
    id: str = ""
    snapshot_id: str = ""
    additions: int = 0
    deletions: int = 0
    files: int = 0
    metadata: Optional[Dict[str, Any]] = None


# Union type alias for all Part variants
Part = Union[TextPart, ToolPart, FilePart, CompactionPart, SnapshotPart]


# ---------------------------------------------------------------------------
# MessageV2
# ---------------------------------------------------------------------------


@dataclass
class MessageV2:
    """A version-2 message composed of one or more Parts.

    Attributes:
        id: Unique identifier for this message.
        role: Message role ("system", "user", "assistant", "tool").
        parts: Ordered list of content parts.
        tokens: Optional total token count for this message.
        summary: Optional short summary of the message content.
        created_at: Optional Unix timestamp of creation.
    """

    id: str = ""
    role: str = ""
    parts: List[Part] = field(default_factory=list)
    tokens: Optional[int] = None
    summary: Optional[str] = None
    created_at: Optional[int] = None


# ---------------------------------------------------------------------------
# Conversion: MessageV2 -> List[ChatMessage]
# ---------------------------------------------------------------------------


def parts_to_chat_messages(msg):
    # type: (MessageV2) -> List[ChatMessage]
    """Convert a MessageV2 into a list of legacy ChatMessage objects.

    Each Part is converted to one or more ChatMessages:
        - TextPart    -> ChatMessage(role=msg.role, content=part.text)
        - ToolPart    -> ChatMessage(role="tool", content=part.output,
                                     tool_call_id=part.tool_call_id)
        - FilePart    -> ChatMessage(role=msg.role, content=part.content)
        - CompactionPart -> ChatMessage(role="system", content=part.summary)
        - SnapshotPart   -> ChatMessage(role="system", content="[Snapshot: ...]")

    Args:
        msg: A MessageV2 instance to convert.

    Returns:
        List of ChatMessage objects representing the same content.
    """
    result = []  # type: List[ChatMessage]

    for part in msg.parts:
        if part.type == "text":
            result.append(
                ChatMessage(
                    role=msg.role,
                    content=part.text,
                )
            )
        elif part.type == "tool":
            result.append(
                ChatMessage(
                    role="tool",
                    content=part.output,
                    tool_call_id=part.tool_call_id,
                )
            )
        elif part.type == "file":
            result.append(
                ChatMessage(
                    role=msg.role,
                    content=part.content,
                )
            )
        elif part.type == "compaction":
            result.append(
                ChatMessage(
                    role="system",
                    content=part.summary,
                )
            )
        elif part.type == "snapshot":
            snapshot_text = "[Snapshot: {} additions, {} deletions, {} files]".format(
                part.additions,
                part.deletions,
                part.files,
            )
            result.append(
                ChatMessage(
                    role="system",
                    content=snapshot_text,
                )
            )

    return result


# ---------------------------------------------------------------------------
# Conversion: List[ChatMessage] -> List[MessageV2]
# ---------------------------------------------------------------------------


def _message_to_part(message):
    # type: (ChatMessage) -> Part
    """Convert a single ChatMessage to the most appropriate Part type.

    Heuristics:
        - role="tool" -> ToolPart
        - content starts with "[Snapshot:" -> SnapshotPart (parsed)
        - role="system" with compaction-like content -> CompactionPart
        - Otherwise -> TextPart
    """
    part_id = uuid4().hex[:8]

    if message.role == "tool":
        return ToolPart(
            id=part_id,
            tool="",
            output=message.content,
            tool_call_id=message.tool_call_id,
        )

    # Detect snapshot messages
    content = message.content
    if content.startswith("[Snapshot:") and content.endswith("]"):
        # Parse: [Snapshot: N additions, M deletions, K files]
        inner = content[len("[Snapshot:") : -1].strip()
        additions = 0
        deletions = 0
        files = 0
        try:
            # "N additions, M deletions, K files"
            segments = inner.split(",")
            for seg in segments:
                seg = seg.strip()
                if "additions" in seg:
                    additions = int(seg.split()[0])
                elif "deletions" in seg:
                    deletions = int(seg.split()[0])
                elif "files" in seg:
                    files = int(seg.split()[0])
        except (ValueError, IndexError):
            pass

        return SnapshotPart(
            id=part_id,
            snapshot_id=uuid4().hex[:8],
            additions=additions,
            deletions=deletions,
            files=files,
        )

    # Default: TextPart for all other roles
    return TextPart(
        id=part_id,
        text=content,
    )


def chat_messages_to_parts(messages):
    # type: (List[ChatMessage]) -> List[MessageV2]
    """Convert a list of legacy ChatMessage objects into MessageV2 instances.

    Groups consecutive messages by role into single MessageV2 instances.
    Each ChatMessage is converted to the appropriate Part type.

    Args:
        messages: List of ChatMessage objects to convert.

    Returns:
        List of MessageV2 instances, one per consecutive role group.
    """
    if not messages:
        return []

    result = []  # type: List[MessageV2]
    current_role = None  # type: Optional[str]
    current_parts = []  # type: List[Part]
    current_id = uuid4().hex[:8]  # type: str

    for msg in messages:
        if msg.role != current_role:
            # Flush previous group
            if current_role is not None and current_parts:
                result.append(
                    MessageV2(
                        id=current_id,
                        role=current_role,
                        parts=current_parts,
                        created_at=int(time.time()),
                    )
                )
            # Start new group
            current_role = msg.role
            current_parts = []
            current_id = uuid4().hex[:8]

        current_parts.append(_message_to_part(msg))

    # Flush last group
    if current_role is not None and current_parts:
        result.append(
            MessageV2(
                id=current_id,
                role=current_role,
                parts=current_parts,
                created_at=int(time.time()),
            )
        )

    return result
