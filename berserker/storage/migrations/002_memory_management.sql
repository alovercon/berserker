-- 002_memory_management.sql
-- Add tables for memory management system: session snapshots and pruning state.

-- session_snapshots
CREATE TABLE IF NOT EXISTS session_snapshots (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    snapshot_hash   TEXT NOT NULL,  -- git commit hash or stash reference
    additions       INTEGER DEFAULT 0,
    deletions       INTEGER DEFAULT 0,
    files           INTEGER DEFAULT 0,
    diff_summary    TEXT,  -- JSON string with diff details
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

-- pruning_state
CREATE TABLE IF NOT EXISTS pruning_state (
    id                      TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL,
    message_id              TEXT NOT NULL,
    original_content_hash   TEXT NOT NULL,  -- hash of original content before pruning
    pruned_content_length   INTEGER NOT NULL,  -- length after pruning
    original_content_length INTEGER NOT NULL,  -- length before pruning
    pruned_at               INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

-- indexes
CREATE INDEX IF NOT EXISTS snapshot_session_idx ON session_snapshots(session_id);
CREATE INDEX IF NOT EXISTS pruning_session_idx ON pruning_state(session_id);
CREATE INDEX IF NOT EXISTS pruning_message_idx ON pruning_state(message_id);