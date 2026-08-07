"""
Todo tool for berserker.

Provides todo CRUD operations (list, add, update, delete, clear)
with session-scoped storage for CLI-friendly interaction.

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolResult

# ---------------------------------------------------------------------------
# Todo Storage (session-scoped, thread-safe)
# ---------------------------------------------------------------------------


class TodoStore(object):
    """Thread-safe in-memory todo storage keyed by session_id."""

    # Valid status values
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    def __init__(self):
        # type: () -> None
        self._lock = threading.Lock()
        self._stores = {}  # type: Dict[str, List[Dict[str, Any]]]
        self._counters = {}  # type: Dict[str, int]

    def get_todos(self, session_id):
        # type: (str) -> List[Dict[str, Any]]
        """Get all todos for a session."""
        with self._lock:
            return list(self._stores.get(session_id, []))

    def add_todo(self, session_id, content):
        # type: (str, str) -> Dict[str, Any]
        """Add a new todo. Returns the created todo.

        Uses monotonic counter per session to guarantee stable, non-repeating IDs.
        """
        with self._lock:
            if session_id not in self._stores:
                self._stores[session_id] = []
                self._counters[session_id] = 0

            todos = self._stores[session_id]
            self._counters[session_id] += 1
            todo_id = self._counters[session_id]
            todo = {
                "id": todo_id,
                "content": content,
                "status": self.STATUS_PENDING,
                "done": False,
                "created_at": time.time(),
            }
            todos.append(todo)
            return dict(todo)

    def update_todo(self, session_id, todo_id, content=None, done=None, status=None):
        # type: (str, int, Optional[str], Optional[bool], Optional[str]) -> Optional[Dict[str, Any]]
        """Update a todo. Returns updated todo or None if not found.

        Supports both legacy 'done' boolean and new 'status' string field.
        When 'done' is set, 'status' is synced (done=True -> completed, done=False -> pending).
        When 'status' is set, 'done' is synced (completed -> done=True, others -> done=False).
        """
        with self._lock:
            todos = self._stores.get(session_id, [])
            for todo in todos:
                if todo["id"] == todo_id:
                    if content is not None:
                        todo["content"] = content
                    if status is not None:
                        todo["status"] = status
                        # Sync done field based on status
                        todo["done"] = status == self.STATUS_COMPLETED
                    if done is not None:
                        todo["done"] = done
                        # Sync status field based on done
                        if done:
                            todo["status"] = self.STATUS_COMPLETED
                        elif todo.get("status") == self.STATUS_COMPLETED:
                            todo["status"] = self.STATUS_PENDING
                    return dict(todo)
            return None

    def delete_todo(self, session_id, todo_id):
        # type: (str, int) -> bool
        """Delete a todo. Returns True if deleted, False if not found."""
        with self._lock:
            todos = self._stores.get(session_id, [])
            for i, todo in enumerate(todos):
                if todo["id"] == todo_id:
                    todos.pop(i)
                    return True
            return False

    def clear_todos(self, session_id):
        # type: (str) -> int
        """Clear all todos for a session. Returns count of cleared todos."""
        with self._lock:
            count = len(self._stores.get(session_id, []))
            self._stores[session_id] = []
            self._counters[session_id] = 0
            return count

    def all_completed(self, session_id):
        # type: (str) -> bool
        """Check if all todos for a session are completed. Returns True if all todos are completed or no todos exist."""
        with self._lock:
            todos = self._stores.get(session_id, [])
            if not todos:
                return False
            return all(t.get("status") == self.STATUS_COMPLETED or t.get("done") for t in todos)


# Global todo store instance
_todo_store = TodoStore()


# ---------------------------------------------------------------------------
# TodoTool
# ---------------------------------------------------------------------------


class TodoTool(Tool):
    """Todo management tool for LLM agents.

    Provides CRUD operations for session-scoped todos.
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=10, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(TodoTool, self).__init__(
            id="todo",
            description=(
                "Create and manage a structured task list for your current coding session.\n\n"
                "## When to Use\n"
                "- Complex tasks (3+ steps) or user explicitly requests todos\n"
                "- User provides multiple tasks (numbered or comma-separated)\n"
                "- After receiving new instructions or completing a task\n\n"
                "## When NOT to Use\n"
                "- Single trivial task (<3 steps) - just do it directly\n"
                "- Purely conversational or informational requests\n\n"
                "## States\n"
                "- **pending**: Not started | **in_progress**: Working on it (ONE at a time)\n"
                "- **completed**: Done | **cancelled**: No longer needed\n\n"
                "## CRITICAL ID RULES\n"
                "1. IDs are stable, monotonic, and NEVER reused (even after deletion)\n"
                "2. BEFORE update/delete, use operation list to see valid IDs\n"
                "3. NEVER guess IDs - only use IDs from 'list' output\n"
                "4. On 'not found' error, use operation list immediately to recover\n\n"
                "## EXECUTION DISCIPLINE\n"
                "- After add/update/delete, the tool ALREADY returns a summary. Do NOT call 'list' unless you specifically need to verify IDs or check overall progress.\n"
                "- Process todos SEQUENTIALLY: mark one as in_progress, execute the actual work (e.g. run bash commands, edit files), mark it completed, then move to the next.\n"
                "- 'in_progress' means you are actively working on that task. You MUST execute the required tools (bash, write, edit, etc.) to complete it before moving on.\n"
                "- Do NOT stop after outputting text — the todo item is only done when the actual work is finished and marked completed.\n\n"
                "Operations: list, add, update, delete, clear."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["list", "add", "update", "delete", "clear"],
                        "description": "Todo operation to perform",
                    },
                    "content": {
                        "type": "string",
                        "description": "Todo content (required for add, optional for update)",
                    },
                    "todo_id": {
                        "type": "integer",
                        "description": "Todo ID (required for update, delete)",
                    },
                    "done": {
                        "type": "boolean",
                        "description": "Completion status (for update operation, legacy)",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                        "description": "Task status (for update operation)",
                    },
                },
                "required": ["operation"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute todo operation."""
        operation = args.get("operation", "")
        session_id = ctx.session_id

        try:
            if operation == "list":
                return self._list(session_id)
            elif operation == "add":
                return self._add(session_id, content=args.get("content", ""))
            elif operation == "update":
                return self._update(
                    session_id,
                    todo_id=args.get("todo_id"),
                    content=args.get("content"),
                    done=args.get("done"),
                    status=args.get("status"),
                )
            elif operation == "delete":
                return self._delete(session_id, todo_id=args.get("todo_id"))
            elif operation == "clear":
                return self._clear(session_id)
            else:
                return ToolResult(
                    title="Todo Error",
                    output="Unknown operation: {}. Use: list, add, update, delete, clear".format(
                        operation
                    ),
                )
        except Exception as e:
            return ToolResult(
                title="Todo Error",
                output="Operation failed: {}".format(str(e)),
            )

    def _format_list(self, header=None, subheader=None, todos=None):
        # type: (Optional[str], Optional[str], Optional[List[Dict[str, Any]]]) -> str
        """Format todo list in compact, LLM-parseable format.

        Output format:
            ID  STATUS  | content
        Example:
            1   [pending]     | Fix ID generation bug
            2   [in_progress] | Add status field
        """
        if todos is None:
            todos = []

        lines = []  # type: List[str]
        if header:
            lines.append(header)
        if subheader:
            lines.append(subheader)
        if not todos:
            if not lines:
                return "No todos."
            lines.append("(empty)")
            return "\n".join(lines)

        lines.append("")  # blank separator
        for t in todos:
            status = t.get("status", "completed" if t.get("done") else "pending")
            lines.append("{}  [{}]  | {}".format(t["id"], status, t["content"]))

        done_count = sum(1 for t in todos if t.get("done"))
        lines.append(
            "\nTotal: {}  Done: {}  Pending: {}".format(
                len(todos), done_count, len(todos) - done_count
            )
        )
        return "\n".join(lines)

    def _list(self, session_id):
        # type: (str) -> ToolResult
        """List all todos."""
        todos = _todo_store.get_todos(session_id)

        if not todos:
            return ToolResult(
                title="Todo List",
                output="No todos. Use 'add' operation to create one.",
            )

        return ToolResult(
            title="Todo List",
            output=self._format_list(header="Todo List", todos=todos),
        )

    def _add(self, session_id, content):
        # type: (str, str) -> ToolResult
        """Add a new todo. Returns concise summary to avoid context overload."""
        if not content:
            return ToolResult(
                title="Todo Error",
                output="Content is required for add operation.",
            )

        todo = _todo_store.add_todo(session_id, content)
        todos = _todo_store.get_todos(session_id)
        pending = sum(
            1
            for t in todos
            if t.get("status") == "pending"
            or (not t.get("done") and t.get("status") != "completed")
        )
        return ToolResult(
            title="Todo Added",
            output="Added #{}: {}\n\n{} pending todos remaining.".format(
                todo["id"], todo["content"], pending
            ),
        )

    def _update(self, session_id, todo_id=None, content=None, done=None, status=None):
        # type: (str, Optional[int], Optional[str], Optional[bool], Optional[str]) -> ToolResult
        """Update a todo. Returns full todo list so agent sees remaining work."""
        if todo_id is None:
            # Include current valid IDs to help LLM recover
            todos = _todo_store.get_todos(session_id)
            if todos:
                id_list = ", ".join(str(t["id"]) for t in todos)
                hint = " Valid IDs: {}.".format(id_list)
            else:
                hint = " No todos exist. Use 'add' to create one first."
            return ToolResult(
                title="Todo Error",
                output="todo_id is required for update operation.{}. Call 'list' to see current todos.".format(
                    hint
                ),
            )

        updated = _todo_store.update_todo(
            session_id, todo_id, content=content, done=done, status=status
        )
        if updated is None:
            # Include current valid IDs to help LLM recover
            todos = _todo_store.get_todos(session_id)
            if todos:
                id_list = ", ".join(str(t["id"]) for t in todos)
                hint = " Valid IDs: {}.".format(id_list)
            else:
                hint = " No todos exist. Use 'add' to create one first."
            return ToolResult(
                title="Todo Error",
                output="Todo #{} not found.{}. Call 'list' to see current todos.".format(
                    todo_id, hint
                ),
            )

        changes = []
        if content is not None:
            changes.append("content='{}'".format(content))
        if done is not None:
            changes.append("done={}".format(done))
        if status is not None:
            changes.append("status='{}'".format(status))

        todos = _todo_store.get_todos(session_id)
        pending = sum(
            1
            for t in todos
            if t.get("status") == "pending"
            or (not t.get("done") and t.get("status") != "completed")
        )
        return ToolResult(
            title="Todo Updated",
            output="Updated #{}: [{}] {}\nChanges: {}\n\n{} pending todos remaining.".format(
                updated["id"],
                updated["status"],
                updated["content"],
                ", ".join(changes),
                pending,
            ),
        )

    def _delete(self, session_id, todo_id=None):
        # type: (str, Optional[int]) -> ToolResult
        """Delete a todo. Returns full list so agent sees remaining work."""
        if todo_id is None:
            return ToolResult(
                title="Todo Error",
                output="todo_id is required for delete operation.",
            )

        deleted = _todo_store.delete_todo(session_id, todo_id)
        if not deleted:
            todos = _todo_store.get_todos(session_id)
            if todos:
                id_list = ", ".join(str(t["id"]) for t in todos)
                hint = " Valid IDs: {}.".format(id_list)
            else:
                hint = " No todos exist."
            return ToolResult(
                title="Todo Error",
                output="Todo #{} not found.{}. Call 'list' to see current todos.".format(
                    todo_id, hint
                ),
            )

        todos = _todo_store.get_todos(session_id)
        pending = sum(
            1
            for t in todos
            if t.get("status") == "pending"
            or (not t.get("done") and t.get("status") != "completed")
        )
        return ToolResult(
            title="Todo Deleted",
            output="Deleted #{}\n\n{} pending todos remaining.".format(todo_id, pending),
        )

    def _clear(self, session_id):
        # type: (str) -> ToolResult
        """Clear all todos."""
        count = _todo_store.clear_todos(session_id)
        return ToolResult(
            title="Todos Cleared",
            output="Cleared {} todos.\n\nNo pending tasks.".format(count),
        )
