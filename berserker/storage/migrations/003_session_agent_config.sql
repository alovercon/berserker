-- 003_session_agent_config.sql
-- Add table for per-session agent configuration persistence.
-- Enables restoring the active agent when switching sessions or after compaction.

CREATE TABLE IF NOT EXISTS session_agent_config (
    session_id  TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    model       TEXT,  -- optional model override
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);
