"""SQLite schema for SAGE.

One database holds everything (ArchitectureV2 §49). Tables are created
idempotently at startup; there is no migration framework yet.
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    role           TEXT NOT NULL,
    content        TEXT,
    tool_calls_json TEXT,
    tool_call_id   TEXT,
    api_content    TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
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

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(content, role UNINDEXED);
"""


async def initialize_schema(database) -> None:
    await database.connection.executescript(SCHEMA_SQL)
    await database.connection.execute(
        "INSERT OR IGNORE INTO session_state (id) VALUES (1)"
    )
    await _ensure_columns(database)
    await _backfill_fts(database)
    await database.commit()


async def _ensure_columns(database) -> None:
    """Additive column upgrades for databases created before a field
    existed (there is no migration framework yet — D23)."""

    wanted = {
        "tasks": {
            "verification": "TEXT", 
            "verification_detail": "TEXT",
            "started_at": "TEXT",
            "completed_at": "TEXT"
        }
    }

    for table, columns in wanted.items():
        cursor = await database.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        for name, decl in columns.items():
            if name not in existing:
                await database.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {decl}"
                )


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
