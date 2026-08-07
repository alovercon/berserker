"""
berserker.storage.schema — SQLite table creation SQL.

Contains the DDL statements for all 11 tables and their indexes.
Mirrors the reference Drizzle schema (session.sql.ts, account.sql.ts, etc.)
translated to raw SQLite DDL.
"""

# ---------------------------------------------------------------------------
# Table DDL
# ---------------------------------------------------------------------------

CREATE_ACCOUNT = """\
CREATE TABLE IF NOT EXISTS account (
    id          TEXT PRIMARY KEY,
    email       TEXT,
    name        TEXT,
    avatar      TEXT,
    provider    TEXT,
    created_at  INTEGER,
    updated_at  INTEGER
);
"""

CREATE_ACCOUNT_STATE = """\
CREATE TABLE IF NOT EXISTS account_state (
    account_id  TEXT PRIMARY KEY,
    state       TEXT,  -- JSON
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE
);
"""

CREATE_PROJECT = """\
CREATE TABLE IF NOT EXISTS project (
    id          TEXT PRIMARY KEY,
    directory   TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER
);
"""

CREATE_SESSION = """\
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
    summary_diffs     TEXT,  -- JSON
    revert            TEXT,  -- JSON
    permission        TEXT,  -- JSON
    created_at        INTEGER,
    updated_at        INTEGER,
    time_compacting   INTEGER,
    time_archived     INTEGER,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);
"""

CREATE_MESSAGE = """\
CREATE TABLE IF NOT EXISTS message (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    data        TEXT NOT NULL,  -- JSON
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);
"""

CREATE_PART = """\
CREATE TABLE IF NOT EXISTS part (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    data        TEXT NOT NULL,  -- JSON
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (message_id) REFERENCES message(id) ON DELETE CASCADE
);
"""

CREATE_TODO = """\
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
"""

CREATE_PERMISSION = """\
CREATE TABLE IF NOT EXISTS permission (
    project_id  TEXT PRIMARY KEY,
    data        TEXT NOT NULL,  -- JSON
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (project_id) REFERENCES project(id) ON DELETE CASCADE
);
"""

CREATE_SESSION_SHARE = """\
CREATE TABLE IF NOT EXISTS session_share (
    url         TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);
"""

CREATE_WORKSPACE = """\
CREATE TABLE IF NOT EXISTS workspace (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    directory   TEXT NOT NULL,
    created_at  INTEGER,
    updated_at  INTEGER
);
"""

CREATE_MIGRATION = """\
CREATE TABLE IF NOT EXISTS migration (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  INTEGER NOT NULL
);
"""

CREATE_SESSION_SNAPSHOTS = """\
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
"""

CREATE_PRUNING_STATE = """\
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
"""

CREATE_SESSION_AGENT_CONFIG = """\
CREATE TABLE IF NOT EXISTS session_agent_config (
    session_id  TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    model       TEXT,  -- optional model override
    created_at  INTEGER,
    updated_at  INTEGER,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# Index DDL
# ---------------------------------------------------------------------------

CREATE_INDEXES = """\
CREATE INDEX IF NOT EXISTS session_project_idx ON session(project_id);
CREATE INDEX IF NOT EXISTS message_session_idx ON message(session_id, created_at, id);
CREATE INDEX IF NOT EXISTS part_message_idx   ON part(message_id, id);
CREATE INDEX IF NOT EXISTS part_session_idx   ON part(session_id);
CREATE INDEX IF NOT EXISTS todo_session_idx   ON todo(session_id);
CREATE INDEX IF NOT EXISTS snapshot_session_idx ON session_snapshots(session_id);
CREATE INDEX IF NOT EXISTS pruning_session_idx ON pruning_state(session_id);
CREATE INDEX IF NOT EXISTS pruning_message_idx ON pruning_state(message_id);
CREATE INDEX IF NOT EXISTS idx_session_workspace ON session(workspace_id);
CREATE INDEX IF NOT EXISTS idx_session_parent ON session(parent_id);
"""

# ---------------------------------------------------------------------------
# Convenience: full schema (all tables + indexes)
# ---------------------------------------------------------------------------

ALL_TABLES = [
    CREATE_ACCOUNT,
    CREATE_ACCOUNT_STATE,
    CREATE_PROJECT,
    CREATE_SESSION,
    CREATE_MESSAGE,
    CREATE_PART,
    CREATE_TODO,
    CREATE_PERMISSION,
    CREATE_SESSION_SHARE,
    CREATE_WORKSPACE,
    CREATE_MIGRATION,
    CREATE_SESSION_SNAPSHOTS,
    CREATE_PRUNING_STATE,
    CREATE_SESSION_AGENT_CONFIG,
]

ALL_INDEXES = [CREATE_INDEXES]

FULL_SCHEMA = "\n".join(ALL_TABLES + ALL_INDEXES)
