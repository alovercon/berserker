"""Shared pytest fixtures for berserker tests."""

import os
import pytest
import tempfile
import threading
import shutil

from berserker.provider.base import ChatResponse
from berserker.tool.base import ToolContext
from berserker.tool.registry import ToolRegistry
from berserker.session.manager import SessionManager

from tests.helpers.mock_provider import MockProvider


@pytest.fixture
def mock_provider():
    # type: () -> MockProvider
    """Create a fresh MockProvider with no pre-configured responses."""
    return MockProvider()


@pytest.fixture
def mock_provider_with_responses():
    # type: () -> MockProvider
    """Create a MockProvider — call set_responses() in tests to configure."""
    return MockProvider()


@pytest.fixture
def temp_workspace():
    # type: () -> str
    """Create a temporary directory for file tool tests. Cleaned up automatically."""
    d = tempfile.mkdtemp(prefix="berserker-test-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def tool_context(temp_workspace):
    # type: (str) -> ToolContext
    """Create a standard ToolContext for tool execution tests."""
    return ToolContext(
        session_id="test-session-001",
        message_id="test-msg-001",
        agent="build",
        abort=threading.Event(),
        call_id="call-001",
        extra={
            "workspace": temp_workspace,
            "context_info": {
                "current_tokens": 50000,
                "context_limit": 128000,
                "compaction_buffer": 20000,
            },
        },
        messages=[],
    )


@pytest.fixture
def tool_context_with_messages(tool_context):
    # type: (ToolContext) -> ToolContext
    """ToolContext pre-populated with conversation messages."""
    tool_context.messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, what files are in this project?"},
        {"role": "assistant", "content": "Let me check the directory."},
    ]
    return tool_context


@pytest.fixture
def tool_registry():
    # type: () -> ToolRegistry
    """Create an empty ToolRegistry with default settings."""
    return ToolRegistry()


@pytest.fixture
def isolated_db(tmp_path):
    # type: (Any) -> None
    """Set BERSERKER_DATA_DIR to isolate the DB per test. Restores after."""
    import berserker.storage.db as db_module
    import berserker.storage as storage

    data_dir = str(tmp_path / "berserker-data")
    os.makedirs(data_dir, exist_ok=True)
    old_env = os.environ.get("BERSERKER_DATA_DIR")
    os.environ["BERSERKER_DATA_DIR"] = data_dir

    # Reset the singleton so it picks up the new path
    with db_module._db_lock:
        db_module._db_instance = None

    # Re-create schema tables in the fresh database.
    # This is needed because modules like session/history.py call
    # ensure_table() at import time against the OLD database.
    from berserker.session.history import command_history

    command_history.ensure_table()

    yield data_dir

    # Cleanup: close connections and reset
    try:
        db = storage.get_db()
        db.close()
    except Exception:
        pass
    with db_module._db_lock:
        db_module._db_instance = None
    if old_env is None:
        os.environ.pop("BERSERKER_DATA_DIR", None)
    else:
        os.environ["BERSERKER_DATA_DIR"] = old_env


@pytest.fixture
def session_manager(isolated_db):
    # type: (str) -> SessionManager
    """Create a SessionManager backed by an isolated temporary DB."""
    return SessionManager(project_id="test-project")


@pytest.fixture
def session_id(session_manager):
    # type: (SessionManager) -> str
    """Create a session and return its ID."""
    return session_manager.create()


@pytest.fixture
def populated_session(session_manager):
    # type: (SessionManager) -> str
    """Create a session with a few messages and return its ID."""
    sid = session_manager.create()
    session_manager.append_message(sid, "system", "You are a test assistant.")
    session_manager.append_message(sid, "user", "Run a test.")
    session_manager.append_message(sid, "assistant", "Running test now.")
    return sid
