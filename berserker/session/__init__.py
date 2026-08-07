"""
berserker.session — Session management with persistence and event publishing.

Public API:
    SessionManager      — Class for creating, loading, listing, deleting sessions
                          and managing messages within sessions.
    session_manager     — Module-level singleton instance (project_id='default')
    InstructionLoader   — Class for discovering and loading AGENTS.md instruction files
    instruction_loader  — Module-level singleton InstructionLoader instance
    TextPart            — Text content part (dataclass)
    ToolPart            — Tool invocation/result content part (dataclass)
    FilePart            — File content part (dataclass)
    CompactionPart      — Conversation compaction/summary content part (dataclass)
    SnapshotPart        — Git snapshot/diff content part (dataclass)
    Part                — Union type alias for all Part variants
    MessageV2           — Version-2 message composed of Parts (dataclass)
    parts_to_chat_messages  — Convert MessageV2 to List[ChatMessage]
    chat_messages_to_parts  — Convert List[ChatMessage] to List[MessageV2]
"""

from berserker.session.manager import SessionManager, session_manager
from berserker.session.instruction import InstructionLoader, instruction_loader
from berserker.session.parts import (
    TextPart,
    ToolPart,
    FilePart,
    CompactionPart,
    SnapshotPart,
    Part,
    MessageV2,
    parts_to_chat_messages,
    chat_messages_to_parts,
)
from berserker.session.context import SessionContext, SessionState

__all__ = [
    "SessionManager",
    "session_manager",
    "InstructionLoader",
    "instruction_loader",
    "TextPart",
    "ToolPart",
    "FilePart",
    "CompactionPart",
    "SnapshotPart",
    "Part",
    "MessageV2",
    "parts_to_chat_messages",
    "chat_messages_to_parts",
    "SessionContext",
    "SessionState",
]
