"""Agent session tracking for OpenCode/Hermes sessions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.persistence.database import Database


@dataclass
class AgentSession:
    """Record of an agent backend session."""
    session_id: str
    employee_key: str
    backend: str  # "opencode" | "hermes"
    workspace_path: str
    objective: str
    external_session_id: str | None = None
    status: str = "active"  # "active" | "completed" | "failed"
    created_at: str = ""
    last_used_at: str = ""
    completed_at: str | None = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.last_used_at:
            self.last_used_at = datetime.now().isoformat()


class AgentSessionManager:
    """Manages agent backend sessions per employee."""

    def __init__(self, database: Database) -> None:
        self.db = database

    async def get_or_create_session(
        self,
        employee_key: str,
        backend: str,
        workspace_path: str,
        objective: str,
        external_session_id: str | None = None,
    ) -> AgentSession:
        """Get existing active session or create new one."""
        # Look for existing active session for this employee+backend+workspace
        existing = await self._find_active_session(employee_key, backend, workspace_path)
        if existing:
            # Update last_used_at
            await self._update_last_used(existing.session_id)
            if external_session_id and not existing.external_session_id:
                await self._set_external_session_id(existing.session_id, external_session_id)
            return existing

        # Create new session
        session_id = f"as_{uuid.uuid4().hex[:12]}"
        session = AgentSession(
            session_id=session_id,
            employee_key=employee_key,
            backend=backend,
            workspace_path=workspace_path,
            objective=objective,
            external_session_id=external_session_id,
        )
        await self._insert(session)
        return session

    async def _find_active_session(
        self,
        employee_key: str,
        backend: str,
        workspace_path: str,
    ) -> AgentSession | None:
        """Find active session for employee+backend+workspace."""
        cursor = await self.db.execute(
            """
            SELECT session_id, employee_key, backend, workspace_path, objective,
                   external_session_id, status, created_at, last_used_at, completed_at
            FROM agent_sessions
            WHERE employee_key = ? AND backend = ? AND workspace_path = ? AND status = 'active'
            ORDER BY last_used_at DESC
            LIMIT 1
            """,
            (employee_key, backend, workspace_path),
        )
        row = await cursor.fetchone()
        if row:
            return AgentSession(
                session_id=row[0],
                employee_key=row[1],
                backend=row[2],
                workspace_path=row[3],
                objective=row[4],
                external_session_id=row[5],
                status=row[6],
                created_at=row[7],
                last_used_at=row[8],
                completed_at=row[9],
            )
        return None

    async def _insert(self, session: AgentSession) -> None:
        await self.db.execute(
            """
            INSERT INTO agent_sessions
            (session_id, employee_key, backend, workspace_path, objective,
             external_session_id, status, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.employee_key,
                session.backend,
                session.workspace_path,
                session.objective,
                session.external_session_id,
                session.status,
                session.created_at,
                session.last_used_at,
            ),
        )
        await self.db.commit()

    async def _update_last_used(self, session_id: str) -> None:
        await self.db.execute(
            "UPDATE agent_sessions SET last_used_at = ? WHERE session_id = ?",
            (datetime.now().isoformat(), session_id),
        )
        await self.db.commit()

    async def _set_external_session_id(self, session_id: str, external_id: str) -> None:
        await self.db.execute(
            "UPDATE agent_sessions SET external_session_id = ? WHERE session_id = ?",
            (external_id, session_id),
        )
        await self.db.commit()

    async def complete_session(
        self,
        session_id: str,
        status: str = "completed",
        external_session_id: str | None = None,
    ) -> None:
        """Mark session as completed/failed."""
        updates = ["status = ?", "completed_at = ?"]
        params = [status, datetime.now().isoformat()]
        if external_session_id:
            updates.append("external_session_id = ?")
            params.append(external_session_id)
        params.append(session_id)

        await self.db.execute(
            f"UPDATE agent_sessions SET {', '.join(updates)} WHERE session_id = ?",
            params,
        )
        await self.db.commit()

    async def get_session_history(self, employee_key: str, limit: int = 20) -> list[AgentSession]:
        """Get session history for an employee."""
        cursor = await self.db.execute(
            """
            SELECT session_id, employee_key, backend, workspace_path, objective,
                   external_session_id, status, created_at, last_used_at, completed_at
            FROM agent_sessions
            WHERE employee_key = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (employee_key, limit),
        )
        rows = await cursor.fetchall()
        return [
            AgentSession(
                session_id=row[0],
                employee_key=row[1],
                backend=row[2],
                workspace_path=row[3],
                objective=row[4],
                external_session_id=row[5],
                status=row[6],
                created_at=row[7],
                last_used_at=row[8],
                completed_at=row[9],
            )
            for row in rows
        ]

    async def get_session(self, session_id: str) -> AgentSession | None:
        """Get a specific session by ID."""
        cursor = await self.db.execute(
            """
            SELECT session_id, employee_key, backend, workspace_path, objective,
                   external_session_id, status, created_at, last_used_at, completed_at
            FROM agent_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        if row:
            return AgentSession(
                session_id=row[0],
                employee_key=row[1],
                backend=row[2],
                workspace_path=row[3],
                objective=row[4],
                external_session_id=row[5],
                status=row[6],
                created_at=row[7],
                last_used_at=row[8],
                completed_at=row[9],
            )
        return None
