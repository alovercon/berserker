"""Tests to verify that required indexes exist after schema initialization."""

import pytest

from berserker.storage import get_db
from berserker.storage.schema import FULL_SCHEMA


@pytest.mark.usefixtures("isolated_db")
def test_session_indexes_exist():
    # type: () -> None
    """Verify idx_session_workspace and idx_session_parent exist after init_db()."""
    db = get_db()

    # Ensure the full schema (including indexes) has been applied.
    # The isolated_db fixture creates tables via ensure_table(), but indexes
    # are only in FULL_SCHEMA. Execute them explicitly.
    db.executescript(FULL_SCHEMA)

    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name IN (?, ?)",
        ("idx_session_workspace", "idx_session_parent"),
    )
    found = {row[0] for row in cursor.fetchall()}

    assert "idx_session_workspace" in found, (
        "Index idx_session_workspace on session(workspace_id) is missing"
    )
    assert "idx_session_parent" in found, (
        "Index idx_session_parent on session(parent_id) is missing"
    )
