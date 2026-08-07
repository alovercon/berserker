-- 001_initial.sql
-- Create all 11 tables and indexes for the berserker storage layer.
-- Mirrors the Drizzle schema translated to raw SQLite DDL.

-- account
CREATE TABLE IF NOT EXISTS account (
    id          TEXT PRIMARY KEY,
    email       TEXT,
    name        TEXT,
    avatar      TEXT,
    provider    TEXT,
    created_at  INTEGER,
    updated_at  INTEGER
);

-- account_state
CREATE TABLE IF NOT EXISTS account_state (
    account_id  TEXT PRIMARY KEY,
    state       TEXT,
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE
);

-- project
CREATE TABLE IF NOT EXISTS project (
    id          TEXT PRIMARY KEY,
    directory   TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER
);

-- session
CREATE TABLE IF NOT EXISTS session (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    workspace_id      TEXT,
    parent_id         TEXT,
    slug              TEXT NOT NULL,
    directory         TEXT NOT NULL,
    title             TEXT NOT NULL,
    version           TEXT NOT NULL,
    share_url         TEXT,
    summary_additions INTEGER,
    summary_deletions INTEGER,
    summary_files     INTEGER,
    summary_diffs     TEXT,
    revert            TEXT,
    permission        TEXT,
    created_at        INTEGER,
    updated_at        INTEGER,
    time_compacting   INTEGER,
    time_archived     INTEGER,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

-- message
CREATE TABLE IF NOT EXISTS message (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    data        TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

-- part
CREATE TABLE IF NOT EXISTS part (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    data        TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (message_id) REFERENCES message(id) ON DELETE CASCADE
);

-- todo
CREATE TABLE IF NOT EXISTS todo (
    session_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL,
    priority    TEXT NOT NULL,
    position    INTEGER NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER,
    PRIMARY KEY (session_id, position),
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

-- permission
CREATE TABLE IF NOT EXISTS permission (
    project_id  TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);

-- session_share
CREATE TABLE IF NOT EXISTS session_share (
    url         TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);

-- workspace
CREATE TABLE IF NOT EXISTS workspace (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    directory   TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER
);

-- migration
CREATE TABLE IF NOT EXISTS migration (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  INTEGER NOT NULL
);

-- indexes
CREATE INDEX IF NOT EXISTS session_project_idx ON session(project_id);
CREATE INDEX IF NOT EXISTS message_session_idx ON message(session_id, created_at, id);
CREATE INDEX IF NOT EXISTS part_message_idx   ON part(message_id, id);
CREATE INDEX IF NOT EXISTS part_session_idx   ON part(session_id);
CREATE INDEX IF NOT EXISTS todo_session_idx   ON todo(session_id);
