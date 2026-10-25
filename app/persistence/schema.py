"""SQLite schema for SAGE.

One database holds everything (ArchitectureV2 §49). Tables are created
idempotently at startup; migrations run additive upgrades.
"""

from app.persistence.migrations import run_all_migrations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    role           TEXT NOT NULL,
    content        TEXT,
    tool_calls_json TEXT,
    tool_call_id   TEXT,
    api_content    TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    token_count    INTEGER,
    model_id       TEXT
);

CREATE TABLE IF NOT EXISTS session_state (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    summary            TEXT,
    ineffective_strikes INTEGER NOT NULL DEFAULT 0,
    compaction_count   INTEGER NOT NULL DEFAULT 0,
    last_compacted_at  TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id       TEXT PRIMARY KEY,
    objective     TEXT NOT NULL,
    tool_name     TEXT NOT NULL,
    worker        TEXT,
    arguments_json TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    result        TEXT,
    error         TEXT,
    verification      TEXT,
    verification_detail TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    started_at    TEXT,
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id   TEXT PRIMARY KEY,
    tool_name     TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    resource      TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    operation_id  TEXT PRIMARY KEY,
    approval_id   TEXT,
    tool_name     TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'EXECUTING',
    result        TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id       TEXT PRIMARY KEY,
    employee_key     TEXT NOT NULL,
    backend          TEXT NOT NULL,
    workspace_path   TEXT NOT NULL,
    objective        TEXT NOT NULL,
    external_session_id TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at     TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at     TEXT
);

CREATE TABLE IF NOT EXISTS message_embeddings (
    message_id   TEXT PRIMARY KEY,
    embedding    BLOB NOT NULL,
    model        TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_message_embeddings_model
ON message_embeddings(model);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(content, role UNINDEXED);

-- Skills tables (created by migration 2)
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    author TEXT,
    tags_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);

CREATE TABLE IF NOT EXISTS skill_versions (
    skill_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by TEXT,
    PRIMARY KEY (skill_id, version)
);

CREATE INDEX IF NOT EXISTS idx_skill_versions_skill ON skill_versions(skill_id);
"""


async def initialize_schema(database) -> None:
    await database.connection.executescript(SCHEMA_SQL)
    await database.connection.execute(
        "INSERT OR IGNORE INTO session_state (id) VALUES (1)"
    )
    await _backfill_fts(database)
    await database.commit()
    
    # Run additive migrations
    from app.persistence.migrations import run_all_migrations
    await run_all_migrations(database)


async def _backfill_fts(database) -> None:
    """Index any existing raw messages exactly once."""

    cursor = await database.execute("SELECT count(*) FROM messages_fts")
    if (await cursor.fetchone())[0] > 0:
        return

    await database.execute(
        """
        INSERT INTO messages_fts (rowid, content, role)
        SELECT id, coalesce(content, ''), role FROM messages
        """
    )
