"""Tests for TodoTool: TodoStore CRUD, stable IDs, status field, and output format."""

import threading

from berserker.tool.base import ToolContext
from berserker.tool.todo import TodoStore, TodoTool, _todo_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(session_id="test-session"):
    # type: (str) -> ToolContext
    return ToolContext(
        session_id=session_id,
        message_id="msg-1",
        agent="build",
        abort=threading.Event(),
    )


# ---------------------------------------------------------------------------
# TodoStore Tests
# ---------------------------------------------------------------------------


class TestTodoStore:
    """Test TodoStore CRUD operations."""

    def setup_method(self):
        """Clear todos before each test to ensure isolation."""
        _todo_store.clear_todos("test-session")
        _todo_store.clear_todos("session-a")
        _todo_store.clear_todos("session-b")

    # -- Add / Get --

    def test_add_todo_returns_created(self):
        """add_todo should return the created todo with all fields."""
        todo = _todo_store.add_todo("test-session", "Fix bug")
        assert todo["id"] == 1
        assert todo["content"] == "Fix bug"
        assert todo["status"] == "pending"
        assert todo["done"] is False
        assert "created_at" in todo

    def test_add_todo_monotonic_ids(self):
        """IDs should be monotonic and never repeat, even after deletion."""
        t1 = _todo_store.add_todo("test-session", "First")
        t2 = _todo_store.add_todo("test-session", "Second")
        t3 = _todo_store.add_todo("test-session", "Third")
        assert t1["id"] == 1
        assert t2["id"] == 2
        assert t3["id"] == 3

        # Delete #2
        _todo_store.delete_todo("test-session", 2)

        # Next ID should be 4, NOT 3 (no collision)
        t4 = _todo_store.add_todo("test-session", "Fourth")
        assert t4["id"] == 4

    def test_get_todos_returns_copy(self):
        """get_todos should return a copy, not the internal list."""
        _todo_store.add_todo("test-session", "Task")
        todos1 = _todo_store.get_todos("test-session")
        todos1.append({"id": 999, "content": "fake"})
        todos2 = _todo_store.get_todos("test-session")
        assert len(todos2) == 1  # Internal list should not be modified

    # -- Update --

    def test_update_todo_content(self):
        """update_todo should update content field."""
        _todo_store.add_todo("test-session", "Old content")
        updated = _todo_store.update_todo("test-session", 1, content="New content")
        assert updated is not None
        assert updated["content"] == "New content"

    def test_update_todo_done(self):
        """update_todo with done=True should set done and sync status."""
        _todo_store.add_todo("test-session", "Task")
        updated = _todo_store.update_todo("test-session", 1, done=True)
        assert updated is not None
        assert updated["done"] is True
        assert updated["status"] == "completed"

    def test_update_todo_status(self):
        """update_todo with status should set status and sync done."""
        _todo_store.add_todo("test-session", "Task")

        # Set in_progress
        updated = _todo_store.update_todo("test-session", 1, status="in_progress")
        assert updated is not None
        assert updated["status"] == "in_progress"
        assert updated["done"] is False

        # Set completed
        updated = _todo_store.update_todo("test-session", 1, status="completed")
        assert updated is not None
        assert updated["status"] == "completed"
        assert updated["done"] is True

        # Set cancelled
        updated = _todo_store.update_todo("test-session", 1, status="cancelled")
        assert updated is not None
        assert updated["status"] == "cancelled"
        assert updated["done"] is False

    def test_update_todo_not_found(self):
        """update_todo should return None for non-existent ID."""
        result = _todo_store.update_todo("test-session", 999, content="Nope")
        assert result is None

    # -- Delete --

    def test_delete_todo_returns_true(self):
        """delete_todo should return True when successful."""
        _todo_store.add_todo("test-session", "ToDelete")
        assert _todo_store.delete_todo("test-session", 1) is True

    def test_delete_todo_not_found(self):
        """delete_todo should return False for non-existent ID."""
        assert _todo_store.delete_todo("test-session", 999) is False

    def test_delete_does_not_renumber(self):
        """After deletion, remaining IDs should stay unchanged."""
        _todo_store.add_todo("test-session", "A")
        _todo_store.add_todo("test-session", "B")
        _todo_store.add_todo("test-session", "C")

        _todo_store.delete_todo("test-session", 2)
        todos = _todo_store.get_todos("test-session")
        ids = [t["id"] for t in todos]
        assert ids == [1, 3]  # Not [1, 2]

    # -- Clear --

    def test_clear_todos_returns_count(self):
        """clear_todos should return the number of cleared todos."""
        _todo_store.add_todo("test-session", "A")
        _todo_store.add_todo("test-session", "B")
        count = _todo_store.clear_todos("test-session")
        assert count == 2

    def test_clear_todos_resets_counter(self):
        """After clear, next add should start from ID 1."""
        _todo_store.add_todo("test-session", "A")
        _todo_store.add_todo("test-session", "B")
        _todo_store.clear_todos("test-session")
        new_todo = _todo_store.add_todo("test-session", "C")
        assert new_todo["id"] == 1

    # -- Session Isolation --

    def test_session_isolation(self):
        """Todos should be isolated per session."""
        _todo_store.add_todo("session-a", "Task A")
        _todo_store.add_todo("session-b", "Task B")

        todos_a = _todo_store.get_todos("session-a")
        todos_b = _todo_store.get_todos("session-b")

        assert len(todos_a) == 1
        assert todos_a[0]["content"] == "Task A"
        assert len(todos_b) == 1
        assert todos_b[0]["content"] == "Task B"

    def test_session_counters_are_independent(self):
        """Each session should have its own ID counter."""
        _todo_store.add_todo("session-a", "A1")
        _todo_store.add_todo("session-b", "B1")
        _todo_store.add_todo("session-a", "A2")
        _todo_store.add_todo("session-b", "B2")

        todos_a = _todo_store.get_todos("session-a")
        todos_b = _todo_store.get_todos("session-b")

        assert [t["id"] for t in todos_a] == [1, 2]
        assert [t["id"] for t in todos_b] == [1, 2]


# ---------------------------------------------------------------------------
# TodoTool Tests
# ---------------------------------------------------------------------------


class TestTodoTool:
    """Test TodoTool operations through the tool interface."""

    def setup_method(self):
        """Clear todos before each test."""
        _todo_store.clear_todos("tool-test")

    def _run(self, args):
        # type: (dict) -> object
        tool = TodoTool()
        ctx = _make_ctx("tool-test")
        return tool.execute(args, ctx)

    # -- List --

    def test_list_empty(self):
        """list on empty store should return 'No todos' message."""
        result = self._run({"operation": "list"})
        assert result.title == "Todo List"
        assert "No todos" in result.output

    def test_list_with_items(self):
        """list should return formatted todo list."""
        _todo_store.add_todo("tool-test", "Task A")
        _todo_store.add_todo("tool-test", "Task B")
        result = self._run({"operation": "list"})
        assert "1  [pending]  | Task A" in result.output
        assert "2  [pending]  | Task B" in result.output
        assert "Total: 2" in result.output

    # -- Add --

    def test_add_success(self):
        """add should create todo and return concise summary."""
        result = self._run({"operation": "add", "content": "New task"})
        assert result.title == "Todo Added"
        assert "Added #1: New task" in result.output
        assert "1 pending todos remaining" in result.output

    def test_add_empty_content(self):
        """add with empty content should return error."""
        result = self._run({"operation": "add", "content": ""})
        assert result.title == "Todo Error"
        assert "Content is required" in result.output

    def test_add_multiple_shows_all(self):
        """add should return summary with correct pending count."""
        self._run({"operation": "add", "content": "Task 1"})
        result = self._run({"operation": "add", "content": "Task 2"})
        assert "Added #2: Task 2" in result.output
        assert "2 pending todos remaining" in result.output

    # -- Update --

    def test_update_status(self):
        """update with status should change todo status."""
        self._run({"operation": "add", "content": "Task"})
        result = self._run(
            {
                "operation": "update",
                "todo_id": 1,
                "status": "in_progress",
            }
        )
        assert result.title == "Todo Updated"
        assert "Updated #1: [in_progress] Task" in result.output

    def test_update_done_legacy(self):
        """update with done=True should still work (backward compat)."""
        self._run({"operation": "add", "content": "Task"})
        result = self._run(
            {
                "operation": "update",
                "todo_id": 1,
                "done": True,
            }
        )
        assert result.title == "Todo Updated"
        assert "Updated #1: [completed] Task" in result.output
        assert "0 pending todos remaining" in result.output

    def test_update_missing_id(self):
        """update without todo_id should return error."""
        result = self._run({"operation": "update"})
        assert result.title == "Todo Error"
        assert "todo_id is required" in result.output

    def test_update_not_found(self):
        """update with non-existent ID should return error with valid IDs."""
        self._run({"operation": "add", "content": "Task"})
        result = self._run(
            {
                "operation": "update",
                "todo_id": 999,
                "status": "completed",
            }
        )
        assert result.title == "Todo Error"
        assert "Todo #999 not found" in result.output
        assert "Valid IDs: 1" in result.output

    # -- Delete --

    def test_delete_success_returns_list(self):
        """delete should return concise summary with remaining count."""
        self._run({"operation": "add", "content": "A"})
        self._run({"operation": "add", "content": "B"})
        result = self._run({"operation": "delete", "todo_id": 1})
        assert result.title == "Todo Deleted"
        assert "Deleted #1" in result.output
        assert "1 pending todos remaining" in result.output

    def test_delete_not_found(self):
        """delete with non-existent ID should return error."""
        result = self._run({"operation": "delete", "todo_id": 999})
        assert result.title == "Todo Error"
        assert "Todo #999 not found" in result.output

    def test_delete_missing_id(self):
        """delete without todo_id should return error."""
        result = self._run({"operation": "delete"})
        assert result.title == "Todo Error"
        assert "todo_id is required" in result.output

    # -- Clear --

    def test_clear(self):
        """clear should remove all todos."""
        self._run({"operation": "add", "content": "A"})
        self._run({"operation": "add", "content": "B"})
        result = self._run({"operation": "clear"})
        assert result.title == "Todos Cleared"
        assert "Cleared 2 todos" in result.output

        # Verify empty
        list_result = self._run({"operation": "list"})
        assert "No todos" in list_result.output

    # -- Unknown Operation --

    def test_unknown_operation(self):
        """Unknown operation should return error."""
        result = self._run({"operation": "foobar"})
        assert result.title == "Todo Error"
        assert "Unknown operation" in result.output


# ---------------------------------------------------------------------------
# Output Format Tests
# ---------------------------------------------------------------------------


class TestOutputFormat:
    """Test the compact output format for LLM parsing."""

    def setup_method(self):
        _todo_store.clear_todos("format-test")

    def _run(self, args):
        # type: (dict) -> object
        tool = TodoTool()
        ctx = _make_ctx("format-test")
        return tool.execute(args, ctx)

    def test_format_shows_status_brackets(self):
        """Output should show status in brackets."""
        self._run({"operation": "add", "content": "Task"})
        result = self._run(
            {
                "operation": "update",
                "todo_id": 1,
                "status": "in_progress",
            }
        )
        assert "[in_progress]" in result.output

    def test_format_pipe_separator(self):
        """Output should use pipe separator between ID/status and content."""
        self._run({"operation": "add", "content": "My task"})
        result = self._run({"operation": "list"})
        assert "1  [pending]  | My task" in result.output

    def test_format_summary_line(self):
        """Output should include Total/Done/Pending summary."""
        self._run({"operation": "add", "content": "A"})
        self._run({"operation": "add", "content": "B"})
        self._run({"operation": "update", "todo_id": 1, "done": True})
        result = self._run({"operation": "list"})
        assert "Total: 2" in result.output
        assert "Done: 1" in result.output
        assert "Pending: 1" in result.output
