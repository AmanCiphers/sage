"""Additive migration framework for SAGE SQLite database.

Migrations are one-way (additive only) and tracked in schema_version table.
Each migration has a version number and runs exactly once per database.
"""

from __future__ import annotations

from typing import Callable, Awaitable

from app.persistence.database import Database


MIGRATIONS: list[tuple[int, str, Callable[[Database], Awaitable[None]]]] = []


def migration(version: int, description: str):
    """Decorator to register a migration."""
    def wrapper(func: Callable[[Database], Awaitable[None]]) -> Callable[[Database], Awaitable[None]]:
        MIGRATIONS.append((version, description, func))
        return func
    return wrapper


async def run_migrations(database: Database) -> int:
    """Run all pending migrations. Returns number of migrations applied."""
    # Ensure schema_version table exists
    await database.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await database.commit()

    # Get current version
    cursor = await database.execute("SELECT MAX(version) FROM schema_version")
    row = await cursor.fetchone()
    current_version = row[0] if row and row[0] is not None else 0

    # Sort migrations by version
    sorted_migrations = sorted(MIGRATIONS, key=lambda x: x[0])

    applied = 0
    for version, description, func in sorted_migrations:
        if version <= current_version:
            continue

        # Check if already applied (idempotent)
        cursor = await database.execute(
            "SELECT 1 FROM schema_version WHERE version = ?", (version,)
        )
        if await cursor.fetchone():
            continue

        # Run migration
        await func(database)

        # Record version
        await database.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description)
        )
        await database.commit()
        applied += 1

    return applied


async def get_schema_version(database: Database) -> int:
    """Get current schema version."""
    cursor = await database.execute("SELECT MAX(version) FROM schema_version")
    row = await cursor.fetchone()
    return row[0] if row and row[0] is not None else 0


# Migration 1: Add token_count and model_id to messages table; add verification columns to tasks
@migration(1, "Add token_count and model_id columns to messages; add verification columns to tasks")
async def _migration_1_add_message_columns(database: Database) -> None:
    cursor = await database.execute("PRAGMA table_info(messages)")
    existing = {row[1] for row in await cursor.fetchall()}
    
    if "token_count" not in existing:
        await database.execute("ALTER TABLE messages ADD COLUMN token_count INTEGER")
    if "model_id" not in existing:
        await database.execute("ALTER TABLE messages ADD COLUMN model_id TEXT")
    
    # Add verification columns to tasks table
    cursor = await database.execute("PRAGMA table_info(tasks)")
    existing = {row[1] for row in await cursor.fetchall()}
    
    if "verification" not in existing:
        await database.execute("ALTER TABLE tasks ADD COLUMN verification TEXT")
    if "verification_detail" not in existing:
        await database.execute("ALTER TABLE tasks ADD COLUMN verification_detail TEXT")


# Migration 2: Create skills and skill_versions tables
@migration(2, "Create skills and skill_versions tables")
async def _migration_2_create_skills_tables(database: Database) -> None:
    await database.connection.executescript("""
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
    """)
    await database.commit()


# Migration 3: Add embedding_model to message_embeddings (already exists, but ensure)
@migration(3, "Ensure message_embeddings has model and dim columns")
async def _migration_3_embedding_columns(database: Database) -> None:
    # These columns should already exist, but ensure they do
    cursor = await database.execute("PRAGMA table_info(message_embeddings)")
    existing = {row[1] for row in await cursor.fetchall()}
    
    if "model" not in existing:
        await database.execute("ALTER TABLE message_embeddings ADD COLUMN model TEXT")
    if "dim" not in existing:
        await database.execute("ALTER TABLE message_embeddings ADD COLUMN dim INTEGER")


# Migration 4: Add status column to agent_sessions if missing
@migration(4, "Add status column to agent_sessions")
async def _migration_4_agent_sessions_status(database: Database) -> None:
    cursor = await database.execute("PRAGMA table_info(agent_sessions)")
    existing = {row[1] for row in await cursor.fetchall()}
    
    if "status" not in existing:
        await database.execute("ALTER TABLE agent_sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")


async def run_all_migrations(database: Database) -> int:
    """Run all migrations. Called at startup."""
    return await run_migrations(database)