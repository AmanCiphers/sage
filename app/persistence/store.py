"""Repositories over the SAGE SQLite database.

Messages are append-only raw history (ArchitectureV2 §30): compaction only
changes what the LLM sees, never what is stored.
"""

import asyncio
import json
import re
import uuid
from typing import TYPE_CHECKING, Any

from app.persistence.database import Database

if TYPE_CHECKING:
    from app.embeddings import EmbeddingProvider, EmbeddingStore

# FTS5 special characters that need escaping
_FTS5_SPECIAL = re.compile(r'([\+\-\*\(\)~:<>@/\\"\'])')


def _escape_fts5(query: str) -> str:
    """Escape FTS5 special characters to avoid syntax errors."""
    # Escape all FTS5 special chars, then wrap in quotes for phrase search
    # This handles edge cases like backslashes in paths
    escaped = _FTS5_SPECIAL.sub(r'\\\1', query)
    # Also escape any remaining unescaped backslashes
    escaped = escaped.replace('\\', '\\\\')
    return '"' + escaped + '"'


class MessageStore:
    def __init__(
        self,
        database: Database,
        embedding_provider: "EmbeddingProvider | None" = None,
        embedding_store: "EmbeddingStore | None" = None,
    ) -> None:
        self.database = database
        self.embedding_provider = embedding_provider
        self.embedding_store = embedding_store

    async def append_user(self, content: str, api_content: str | None = None) -> int:
        return await self._append("user", content, api_content=api_content)

    async def append_assistant(
        self,
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> int:
        tool_calls_json = (
            json.dumps(tool_calls) if tool_calls else None
        )
        return await self._append(
            "assistant",
            content,
            tool_calls_json=tool_calls_json,
        )

    async def append_tool_result(self, tool_call_id: str, content: str) -> int:
        return await self._append(
            "tool",
            content,
            tool_call_id=tool_call_id,
        )

    async def recent(self, limit: int = 400) -> list[dict[str, Any]]:
        cursor = await self.database.execute(
            """
            SELECT id, role, content, tool_calls_json, tool_call_id, api_content
            FROM messages
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        rows.reverse()

        return [
            {
                "id": row[0],
                "role": row[1],
                "content": row[2],
                "tool_calls": json.loads(row[3]) if row[3] else None,
                "tool_call_id": row[4],
                "api_content": row[5],
            }
            for row in rows
        ]

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """FTS5 search over the permanent raw history."""
        # Escape FTS5 special characters to avoid syntax errors
        escaped_query = _escape_fts5(query)

        try:
            cursor = await self.database.execute(
                """
                SELECT m.id, m.role, m.content
                FROM messages_fts f
                JOIN messages m ON m.id = f.rowid
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (escaped_query, limit),
            )
            rows = await cursor.fetchall()
        except Exception:
            # Fallback: try a simpler query without phrase matching
            try:
                simple_query = query.replace('"', '').replace('\\', '')[:100]
                cursor = await self.database.execute(
                    """
                    SELECT m.id, m.role, m.content
                    FROM messages_fts f
                    JOIN messages m ON m.id = f.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (simple_query, limit),
                )
                rows = await cursor.fetchall()
            except Exception:
                # Give up on FTS5, return empty
                return []

        return [
            {
                "id": row[0],
                "role": row[1],
                "content": (row[2] or "")[:300],
            }
            for row in rows
        ]

    async def _append(
        self,
        role: str,
        content: str | None,
        tool_calls_json: str | None = None,
        tool_call_id: str | None = None,
        api_content: str | None = None,
    ) -> int:
        cursor = await self.database.execute(
            """
            INSERT INTO messages (role, content, tool_calls_json, tool_call_id, api_content)
            VALUES (?, ?, ?, ?, ?)
            """,
            (role, content, tool_calls_json, tool_call_id, api_content),
        )

        msg_id = cursor.lastrowid

        await self.database.execute(
            "INSERT INTO messages_fts (rowid, content, role) VALUES (?, ?, ?)",
            (msg_id, content or "", role),
        )

        await self.database.commit()

        if not hasattr(self, "_embedding_tasks"):
            self._embedding_tasks = set()

        if content and content.strip() and self.embedding_provider and self.embedding_store:
            task = asyncio.create_task(self._generate_embedding(msg_id, content))
            self._embedding_tasks.add(task)
            task.add_done_callback(self._embedding_tasks.discard)

        return msg_id

    async def _generate_embedding(self, msg_id: int, content: str) -> None:
        """Generate and store embedding for a message (fire-and-forget)."""
        try:
            # Use passage type for documents
            embedding = await self.embedding_provider.embed_passage(content)
            await self.embedding_store.save(str(msg_id), embedding)
        except Exception as e:
            # Log but don't fail the message append
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("embedding_generation_failed msg_id=%s error=%s", msg_id, e)


class SessionStateStore:
    """Durable compaction guards (anti-thrash strikes, summary text)."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self) -> dict[str, Any]:
        cursor = await self.database.execute(
            """
            SELECT summary, ineffective_strikes, compaction_count, last_compacted_at
            FROM session_state
            WHERE id = 1
            """
        )
        row = await cursor.fetchone()

        return {
            "summary": row[0],
            "ineffective_strikes": row[1],
            "compaction_count": row[2],
            "last_compacted_at": row[3],
        }

    async def save_summary(self, summary: str) -> None:
        await self.database.execute(
            """
            UPDATE session_state
            SET summary = ?,
                compaction_count = compaction_count + 1,
                last_compacted_at = datetime('now')
            WHERE id = 1
            """,
            (summary,),
        )
        await self.database.commit()

    async def set_strikes(self, strikes: int) -> None:
        await self.database.execute(
            "UPDATE session_state SET ineffective_strikes = ? WHERE id = 1",
            (strikes,),
        )
        await self.database.commit()


class TaskStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create(
        self,
        objective: str,
        tool_name: str,
        arguments: dict[str, Any],
        worker: str | None = None,
    ) -> str:
        task_id = f"task_{uuid.uuid4().hex[:10]}"

        await self.database.execute(
            """
            INSERT INTO tasks (task_id, objective, tool_name, worker, arguments_json, status)
            VALUES (?, ?, ?, ?, ?, 'queued')
            """,
            (task_id, objective, tool_name, worker, json.dumps(arguments)),
        )
        await self.database.commit()

        return task_id

    async def set_status(
        self,
        task_id: str,
        status: str,
        result: str | None = None,
        error: str | None = None,
        verification: str | None = None,
        verification_detail: str | None = None,
    ) -> None:
        # Track timing: started_at when moving to running, completed_at when terminal
        now = "datetime('now')"
        updates = ["status = ?", "updated_at = " + now]
        params = [status]

        if status == "running":
            updates.append("started_at = " + now)
        elif status in ("completed", "failed", "unknown"):
            updates.append("completed_at = " + now)

        if result is not None:
            updates.append("result = ?")
            params.append(result)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if verification is not None:
            updates.append("verification = ?")
            params.append(verification)
        if verification_detail is not None:
            updates.append("verification_detail = ?")
            params.append(verification_detail)

        params.append(task_id)

        await self.database.execute(
            f"""
            UPDATE tasks
            SET {", ".join(updates)}
            WHERE task_id = ?
            """,
            tuple(params),
        )
        await self.database.commit()

    async def recover_stuck(self) -> int:
        """Mark in-flight tasks from a previous process as `unknown`
        (their workers are gone; the outcome cannot be determined)."""

        cursor = await self.database.execute(
            """
            UPDATE tasks
            SET status = 'unknown',
                error = 'SAGE restarted while the task was in flight; outcome unknown',
                updated_at = datetime('now')
            WHERE status IN ('queued', 'running')
            """
        )
        await self.database.commit()
        return cursor.rowcount

    async def get(self, task_id: str) -> dict[str, Any] | None:
        cursor = await self.database.execute(
            """
            SELECT task_id, objective, tool_name, worker, arguments_json, status,
                   result, error, verification, verification_detail,
                   created_at, started_at, completed_at
            FROM tasks
            WHERE task_id = ?
            """,
            (task_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        started = row[11]
        completed = row[12]
        duration_sec = None
        if started and completed:
            try:
                from datetime import datetime
                s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                c = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                duration_sec = (c - s).total_seconds()
            except (ValueError, TypeError):
                pass

        return {
            "task_id": row[0],
            "objective": row[1],
            "tool_name": row[2],
            "worker": row[3],
            "arguments": json.loads(row[4]),
            "status": row[5],
            "result": row[6],
            "error": row[7],
            "verification": row[8],
            "verification_detail": row[9],
            "created_at": row[10],
            "started_at": row[11],
            "completed_at": row[12],
            "duration_seconds": duration_sec,
        }

    async def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent tasks, newest first (for the web worker panel)."""
        return await self._list(limit)

    async def list_for_worker(self, worker: str, limit: int = 20) -> list[dict[str, Any]]:
        """Recent assignments for one employee (lowercase key)."""
        return await self._list(limit, worker=worker)

    async def _list(
        self,
        limit: int,
        worker: str | None = None,
    ) -> list[dict[str, Any]]:
        where = "WHERE worker = ?" if worker is not None else ""
        params: tuple[Any, ...] = (worker, limit) if worker is not None else (limit,)

        cursor = await self.database.execute(
            f"""
            SELECT task_id, objective, tool_name, worker, arguments_json, status,
                   result, error, verification, verification_detail,
                   created_at, started_at, completed_at
            FROM tasks
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()

        tasks: list[dict[str, Any]] = []
        for row in rows:
            tasks.append(
                {
                    "task_id": row[0],
                    "objective": row[1],
                    "tool_name": row[2],
                    "worker": row[3],
                    "arguments": json.loads(row[4]),
                    "status": row[5],
                    "result": row[6],
                    "error": row[7],
                    "verification": row[8],
                    "verification_detail": row[9],
                    "created_at": row[10],
                    "started_at": row[11],
                    "completed_at": row[12],
                }
            )
        return tasks
