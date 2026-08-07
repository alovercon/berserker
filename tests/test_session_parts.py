"""Tests for Part-based message model in berserker.session.parts."""

from __future__ import annotations

import pytest
from uuid import uuid4

from berserker.provider.base import ChatMessage
from berserker.session.parts import (
    TextPart,
    ToolPart,
    FilePart,
    CompactionPart,
    SnapshotPart,
    MessageV2,
    parts_to_chat_messages,
    chat_messages_to_parts,
)


# ---------------------------------------------------------------------------
# Part Types Tests
# ---------------------------------------------------------------------------


class TestPartTypes:
    """Test all Part types creation with defaults and custom values."""

    def test_text_part_creation(self):
        """Test TextPart creation with defaults and custom values."""
        # Default values
        part1 = TextPart()
        assert part1.type == "text"
        assert part1.id == ""
        assert part1.text == ""
        assert part1.metadata is None

        # Custom values
        part2 = TextPart(
            id="test-id",
            text="Hello world",
            metadata={"key": "value"},
        )
        assert part2.type == "text"
        assert part2.id == "test-id"
        assert part2.text == "Hello world"
        assert part2.metadata == {"key": "value"}

    def test_tool_part_creation(self):
        """Test ToolPart creation with defaults and custom values."""
        # Default values
        part1 = ToolPart()
        assert part1.type == "tool"
        assert part1.id == ""
        assert part1.tool == ""
        assert part1.output == ""
        assert part1.tool_call_id is None
        assert part1.metadata is None

        # Custom values
        part2 = ToolPart(
            id="tool-id",
            tool="bash",
            output="Command executed",
            tool_call_id="call-123",
            metadata={"duration": 1.5},
        )
        assert part2.type == "tool"
        assert part2.id == "tool-id"
        assert part2.tool == "bash"
        assert part2.output == "Command executed"
        assert part2.tool_call_id == "call-123"
        assert part2.metadata == {"duration": 1.5}

    def test_file_part_creation(self):
        """Test FilePart creation with defaults and custom values."""
        # Default values
        part1 = FilePart()
        assert part1.type == "file"
        assert part1.id == ""
        assert part1.file_path == ""
        assert part1.content == ""
        assert part1.metadata is None

        # Custom values
        part2 = FilePart(
            id="file-id",
            file_path="/path/to/file.txt",
            content="File content here",
            metadata={"size": 1024},
        )
        assert part2.type == "file"
        assert part2.id == "file-id"
        assert part2.file_path == "/path/to/file.txt"
        assert part2.content == "File content here"
        assert part2.metadata == {"size": 1024}

    def test_compaction_part_creation(self):
        """Test CompactionPart creation with defaults and custom values."""
        # Default values
        part1 = CompactionPart()
        assert part1.type == "compaction"
        assert part1.id == ""
        assert part1.summary == ""
        assert part1.tokens_before == 0
        assert part1.tokens_after == 0
        assert part1.metadata is None

        # Custom values
        part2 = CompactionPart(
            id="compact-id",
            summary="Summarized content",
            tokens_before=1000,
            tokens_after=100,
            metadata={"ratio": 0.1},
        )
        assert part2.type == "compaction"
        assert part2.id == "compact-id"
        assert part2.summary == "Summarized content"
        assert part2.tokens_before == 1000
        assert part2.tokens_after == 100
        assert part2.metadata == {"ratio": 0.1}

    def test_snapshot_part_creation(self):
        """Test SnapshotPart creation with defaults and custom values."""
        # Default values
        part1 = SnapshotPart()
        assert part1.type == "snapshot"
        assert part1.id == ""
        assert part1.snapshot_id == ""
        assert part1.additions == 0
        assert part1.deletions == 0
        assert part1.files == 0
        assert part1.metadata is None

        # Custom values
        part2 = SnapshotPart(
            id="snapshot-id",
            snapshot_id="snap-123",
            additions=5,
            deletions=3,
            files=2,
            metadata={"branch": "main"},
        )
        assert part2.type == "snapshot"
        assert part2.id == "snapshot-id"
        assert part2.snapshot_id == "snap-123"
        assert part2.additions == 5
        assert part2.deletions == 3
        assert part2.files == 2
        assert part2.metadata == {"branch": "main"}


# ---------------------------------------------------------------------------
# MessageV2 Tests
# ---------------------------------------------------------------------------


class TestMessageV2:
    """Test MessageV2 creation and functionality."""

    def test_message_v2_creation_with_empty_parts(self):
        """Test MessageV2 creation with empty parts list."""
        msg = MessageV2()
        assert msg.id == ""
        assert msg.role == ""
        assert msg.parts == []
        assert msg.tokens is None
        assert msg.summary is None
        assert msg.created_at is None

    def test_message_v2_creation_with_parts(self):
        """Test MessageV2 creation with multiple parts of different types."""
        text_part = TextPart(text="Hello")
        tool_part = ToolPart(tool="bash", output="Done")
        file_part = FilePart(file_path="test.txt", content="Content")

        msg = MessageV2(
            id="msg-123",
            role="user",
            parts=[text_part, tool_part, file_part],
            tokens=100,
            summary="Test message",
        )

        assert msg.id == "msg-123"
        assert msg.role == "user"
        assert len(msg.parts) == 3
        assert msg.parts[0].type == "text"
        assert msg.parts[1].type == "tool"
        assert msg.parts[2].type == "file"
        assert msg.tokens == 100
        assert msg.summary == "Test message"


# ---------------------------------------------------------------------------
# parts_to_chat_messages() Tests
# ---------------------------------------------------------------------------


class TestPartsToChatMessages:
    """Test conversion from MessageV2 to List[ChatMessage]."""

    def test_text_part_conversion(self):
        """Test TextPart → ChatMessage(role=msg.role, content=part.text)."""
        text_part = TextPart(text="Hello world")
        msg = MessageV2(role="user", parts=[text_part])
        chat_messages = parts_to_chat_messages(msg)

        assert len(chat_messages) == 1
        assert chat_messages[0].role == "user"
        assert chat_messages[0].content == "Hello world"
        assert chat_messages[0].tool_call_id is None

    def test_tool_part_conversion(self):
        """Test ToolPart → ChatMessage(role="tool", content=part.output, tool_call_id=...)."""
        tool_part = ToolPart(
            tool="bash",
            output="Command result",
            tool_call_id="call-123",
        )
        msg = MessageV2(role="assistant", parts=[tool_part])
        chat_messages = parts_to_chat_messages(msg)

        assert len(chat_messages) == 1
        assert chat_messages[0].role == "tool"
        assert chat_messages[0].content == "Command result"
        assert chat_messages[0].tool_call_id == "call-123"

    def test_file_part_conversion(self):
        """Test FilePart → ChatMessage(role=msg.role, content=part.content)."""
        file_part = FilePart(file_path="test.txt", content="File content")
        msg = MessageV2(role="user", parts=[file_part])
        chat_messages = parts_to_chat_messages(msg)

        assert len(chat_messages) == 1
        assert chat_messages[0].role == "user"
        assert chat_messages[0].content == "File content"
        assert chat_messages[0].tool_call_id is None

    def test_compaction_part_conversion(self):
        """Test CompactionPart → ChatMessage(role="system", content=part.summary)."""
        compaction_part = CompactionPart(summary="Summarized conversation")
        msg = MessageV2(role="user", parts=[compaction_part])
        chat_messages = parts_to_chat_messages(msg)

        assert len(chat_messages) == 1
        assert chat_messages[0].role == "system"
        assert chat_messages[0].content == "Summarized conversation"
        assert chat_messages[0].tool_call_id is None

    def test_snapshot_part_conversion(self):
        """Test SnapshotPart → ChatMessage(role="system", content="[Snapshot: N additions, M deletions, K files]")."""
        snapshot_part = SnapshotPart(additions=5, deletions=3, files=2)
        msg = MessageV2(role="user", parts=[snapshot_part])
        chat_messages = parts_to_chat_messages(msg)

        assert len(chat_messages) == 1
        assert chat_messages[0].role == "system"
        expected_content = "[Snapshot: 5 additions, 3 deletions, 2 files]"
        assert chat_messages[0].content == expected_content
        assert chat_messages[0].tool_call_id is None

    def test_multiple_parts_conversion(self):
        """Test MessageV2 with multiple parts of different types."""
        parts = [
            TextPart(text="Hello"),
            ToolPart(tool="bash", output="Executed", tool_call_id="call-1"),
            FilePart(file_path="file.txt", content="File data"),
            CompactionPart(summary="Summary"),
            SnapshotPart(additions=1, deletions=2, files=3),
        ]
        msg = MessageV2(role="user", parts=parts)
        chat_messages = parts_to_chat_messages(msg)

        assert len(chat_messages) == 5

        # TextPart → user role
        assert chat_messages[0].role == "user"
        assert chat_messages[0].content == "Hello"

        # ToolPart → tool role
        assert chat_messages[1].role == "tool"
        assert chat_messages[1].content == "Executed"
        assert chat_messages[1].tool_call_id == "call-1"

        # FilePart → user role
        assert chat_messages[2].role == "user"
        assert chat_messages[2].content == "File data"

        # CompactionPart → system role
        assert chat_messages[3].role == "system"
        assert chat_messages[3].content == "Summary"

        # SnapshotPart → system role
        assert chat_messages[4].role == "system"
        assert chat_messages[4].content == "[Snapshot: 1 additions, 2 deletions, 3 files]"

    def test_empty_parts_list(self):
        """Test empty parts list conversion."""
        msg = MessageV2(role="user", parts=[])
        chat_messages = parts_to_chat_messages(msg)
        assert chat_messages == []


# ---------------------------------------------------------------------------
# chat_messages_to_parts() Tests
# ---------------------------------------------------------------------------


class TestChatMessagesToParts:
    """Test conversion from List[ChatMessage] to List[MessageV2]."""

    def test_tool_role_conversion(self):
        """Test role="tool" → ToolPart."""
        chat_messages = [ChatMessage(role="tool", content="Tool output", tool_call_id="call-123")]
        message_v2_list = chat_messages_to_parts(chat_messages)

        assert len(message_v2_list) == 1
        msg_v2 = message_v2_list[0]
        assert msg_v2.role == "tool"
        assert len(msg_v2.parts) == 1
        part = msg_v2.parts[0]
        assert isinstance(part, ToolPart)
        assert part.output == "Tool output"
        assert part.tool_call_id == "call-123"

    def test_snapshot_content_conversion(self):
        """Test content starting with "[Snapshot:" → SnapshotPart."""
        chat_messages = [
            ChatMessage(
                role="system",
                content="[Snapshot: 5 additions, 3 deletions, 2 files]",
            )
        ]
        message_v2_list = chat_messages_to_parts(chat_messages)

        assert len(message_v2_list) == 1
        msg_v2 = message_v2_list[0]
        assert msg_v2.role == "system"
        assert len(msg_v2.parts) == 1
        part = msg_v2.parts[0]
        assert isinstance(part, SnapshotPart)
        assert part.additions == 5
        assert part.deletions == 3
        assert part.files == 2

    def test_other_roles_conversion(self):
        """Test other roles → TextPart."""
        chat_messages = [
            ChatMessage(role="user", content="User message"),
            ChatMessage(role="assistant", content="Assistant response"),
            ChatMessage(role="system", content="System prompt"),
        ]
        message_v2_list = chat_messages_to_parts(chat_messages)

        # Each different role should be in separate MessageV2
        assert len(message_v2_list) == 3

        # User message
        assert message_v2_list[0].role == "user"
        assert len(message_v2_list[0].parts) == 1
        assert isinstance(message_v2_list[0].parts[0], TextPart)
        assert message_v2_list[0].parts[0].text == "User message"

        # Assistant message
        assert message_v2_list[1].role == "assistant"
        assert len(message_v2_list[1].parts) == 1
        assert isinstance(message_v2_list[1].parts[0], TextPart)
        assert message_v2_list[1].parts[0].text == "Assistant response"

        # System message (not snapshot)
        assert message_v2_list[2].role == "system"
        assert len(message_v2_list[2].parts) == 1
        assert isinstance(message_v2_list[2].parts[0], TextPart)
        assert message_v2_list[2].parts[0].text == "System prompt"

    def test_consecutive_same_role_grouping(self):
        """Test consecutive same-role messages grouped into single MessageV2."""
        chat_messages = [
            ChatMessage(role="user", content="Message 1"),
            ChatMessage(role="user", content="Message 2"),
            ChatMessage(role="assistant", content="Response 1"),
            ChatMessage(role="assistant", content="Response 2"),
            ChatMessage(role="user", content="Message 3"),
        ]
        message_v2_list = chat_messages_to_parts(chat_messages)

        # Should group consecutive same roles
        assert len(message_v2_list) == 3

        # First user group (2 messages)
        assert message_v2_list[0].role == "user"
        assert len(message_v2_list[0].parts) == 2
        assert message_v2_list[0].parts[0].text == "Message 1"
        assert message_v2_list[0].parts[1].text == "Message 2"

        # Assistant group (2 messages)
        assert message_v2_list[1].role == "assistant"
        assert len(message_v2_list[1].parts) == 2
        assert message_v2_list[1].parts[0].text == "Response 1"
        assert message_v2_list[1].parts[1].text == "Response 2"

        # Second user group (1 message)
        assert message_v2_list[2].role == "user"
        assert len(message_v2_list[2].parts) == 1
        assert message_v2_list[2].parts[0].text == "Message 3"

    def test_empty_message_list(self):
        """Test empty message list conversion."""
        message_v2_list = chat_messages_to_parts([])
        assert message_v2_list == []


# ---------------------------------------------------------------------------
# Round-trip Conversion Tests
# ---------------------------------------------------------------------------


class TestRoundTripConversion:
    """Test round-trip: ChatMessage list → MessageV2 → ChatMessage list."""

    def test_round_trip_preserves_content(self):
        """Test that round-trip conversion preserves content."""
        original_messages = [
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="tool", content="Tool result", tool_call_id="call-1"),
            ChatMessage(role="assistant", content="Response"),
            ChatMessage(
                role="system",
                content="[Snapshot: 2 additions, 1 deletions, 1 files]",
            ),
        ]

        # Convert to MessageV2
        message_v2_list = chat_messages_to_parts(original_messages)

        # Convert back to ChatMessage
        reconstructed_messages = []
        for msg_v2 in message_v2_list:
            reconstructed_messages.extend(parts_to_chat_messages(msg_v2))

        # Content should be preserved exactly
        assert len(reconstructed_messages) == len(original_messages)
        for orig, recon in zip(original_messages, reconstructed_messages):
            assert orig.role == recon.role
            assert orig.content == recon.content
            assert orig.tool_call_id == recon.tool_call_id

    def test_round_trip_with_consecutive_same_role(self):
        """Test round-trip with consecutive same-role messages."""
        original_messages = [
            ChatMessage(role="user", content="First"),
            ChatMessage(role="user", content="Second"),
            ChatMessage(role="assistant", content="Response"),
        ]

        # Convert to MessageV2 (groups consecutive same roles)
        message_v2_list = chat_messages_to_parts(original_messages)
        assert len(message_v2_list) == 2  # user group + assistant

        # Convert back to ChatMessage
        reconstructed_messages = []
        for msg_v2 in message_v2_list:
            reconstructed_messages.extend(parts_to_chat_messages(msg_v2))

        # Should preserve original content and order
        assert len(reconstructed_messages) == 3
        assert reconstructed_messages[0].content == "First"
        assert reconstructed_messages[1].content == "Second"
        assert reconstructed_messages[2].content == "Response"


# ---------------------------------------------------------------------------
# Edge Cases Tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_snapshot_parsing_edge_cases(self):
        """Test SnapshotPart parsing with malformed content."""
        # Malformed snapshot content should still create SnapshotPart with defaults
        chat_messages = [
            ChatMessage(role="system", content="[Snapshot: invalid format]"),
            ChatMessage(role="system", content="[Snapshot:]"),
            ChatMessage(role="system", content="[Snapshot"),
        ]

        message_v2_list = chat_messages_to_parts(chat_messages)
        # All system messages are grouped into one MessageV2
        assert len(message_v2_list) == 1
        msg_v2 = message_v2_list[0]
        assert msg_v2.role == "system"
        assert len(msg_v2.parts) == 3

        # First two should be SnapshotParts (start with "[Snapshot:")
        assert isinstance(msg_v2.parts[0], SnapshotPart)
        assert isinstance(msg_v2.parts[1], SnapshotPart)
        # Third doesn't start with "[Snapshot:" so it's TextPart
        assert isinstance(msg_v2.parts[2], TextPart)

        # SnapshotParts should have default values (0) for malformed content
        assert msg_v2.parts[0].additions == 0
        assert msg_v2.parts[0].deletions == 0
        assert msg_v2.parts[0].files == 0
        assert msg_v2.parts[1].additions == 0
        assert msg_v2.parts[1].deletions == 0
        assert msg_v2.parts[1].files == 0
        assert msg_v2.parts[2].text == "[Snapshot"

    def test_mixed_snapshot_and_regular_system(self):
        """Test mixed snapshot and regular system messages."""
        chat_messages = [
            ChatMessage(role="system", content="Regular system message"),
            ChatMessage(
                role="system",
                content="[Snapshot: 1 additions, 0 deletions, 1 files]",
            ),
            ChatMessage(role="system", content="Another regular message"),
        ]

        message_v2_list = chat_messages_to_parts(chat_messages)
        # All system messages are grouped into one MessageV2
        assert len(message_v2_list) == 1
        msg_v2 = message_v2_list[0]
        assert msg_v2.role == "system"
        assert len(msg_v2.parts) == 3

        # First: TextPart (regular system message)
        assert isinstance(msg_v2.parts[0], TextPart)
        assert msg_v2.parts[0].text == "Regular system message"

        # Second: SnapshotPart
        assert isinstance(msg_v2.parts[1], SnapshotPart)
        assert msg_v2.parts[1].additions == 1

        # Third: TextPart (another regular message)
        assert isinstance(msg_v2.parts[2], TextPart)
        assert msg_v2.parts[2].text == "Another regular message"
