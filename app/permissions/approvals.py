"""Approvals: bound, expiring user consent for side-effecting actions.

An approval is never just `approved = true` (ArchitectureV2 §27). It is
bound to the exact tool name and arguments recorded when the model asked.
Approving executes ONLY those bound arguments — never new input.
"""

import json
import uuid
from typing import Any

from app.persistence.database import Database

APPROVAL_TTL_SECONDS = 600  # 10 minutes


class ApprovalError(Exception):
    pass


class ApprovalManager:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_pending(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        resource: str | None = None,
    ) -> str:
        approval_id = f"ap_{uuid.uuid4().hex[:10]}"

        await self.database.execute(
            """
            INSERT INTO approvals (approval_id, tool_name, arguments_json, resource, expires_at)
            VALUES (?, ?, ?, ?, datetime('now', ?))
            """,
            (
                approval_id,
                tool_name,
                json.dumps(arguments),
                resource,
                f"+{APPROVAL_TTL_SECONDS} seconds",
            ),
        )
        await self.database.commit()

        return approval_id

    async def get(self, approval_id: str) -> dict[str, Any] | None:
        cursor = await self.database.execute(
            """
            SELECT approval_id, tool_name, arguments_json, resource, status, expires_at
            FROM approvals
            WHERE approval_id = ?
            """,
            (approval_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return {
            "approval_id": row[0],
            "tool_name": row[1],
            "arguments": json.loads(row[2]),
            "resource": row[3],
            "status": row[4],
            "expires_at": row[5],
        }

    async def approve(self, approval_id: str) -> dict[str, Any]:
        """Mark approved. Returns the bound approval record.

        The caller must execute exactly the returned tool_name/arguments.
        """

        approval = await self.get(approval_id)

        if approval is None:
            raise ApprovalError(f"Unknown approval: {approval_id}")

        if approval["status"] != "pending":
            raise ApprovalError(
                f"Approval {approval_id} is {approval['status']}, not pending"
            )

        expired = await self._is_expired(approval_id)
        if expired:
            await self._set_status(approval_id, "expired")
            raise ApprovalError(f"Approval {approval_id} has expired")

        await self._set_status(approval_id, "approved")
        approval["status"] = "approved"
        return approval

    async def deny(self, approval_id: str, reason: str = "") -> None:
        approval = await self.get(approval_id)

        if approval is None:
            raise ApprovalError(f"Unknown approval: {approval_id}")

        if approval["status"] != "pending":
            raise ApprovalError(
                f"Approval {approval_id} is {approval['status']}, not pending"
            )

        await self._set_status(approval_id, "denied")

    async def _is_expired(self, approval_id: str) -> bool:
        cursor = await self.database.execute(
            "SELECT datetime('now') > expires_at FROM approvals WHERE approval_id = ?",
            (approval_id,),
        )
        row = await cursor.fetchone()
        return bool(row[0])

    async def _set_status(self, approval_id: str, status: str) -> None:
        await self.database.execute(
            "UPDATE approvals SET status = ? WHERE approval_id = ?",
            (status, approval_id),
        )
        await self.database.commit()

    async def list_pending(self) -> list[dict[str, Any]]:
        """List all pending approvals."""
        cursor = await self.database.execute(
            """
            SELECT approval_id, tool_name, arguments_json, resource, status, expires_at
            FROM approvals
            WHERE status = 'pending'
            ORDER BY expires_at ASC
            """
        )
        rows = await cursor.fetchall()

        result = []
        for row in rows:
            result.append({
                "approval_id": row[0],
                "tool_name": row[1],
                "arguments": json.loads(row[2]),
                "resource": row[3],
                "status": row[4],
                "expires_at": row[5],
            })
        return result

    async def approve_all(self) -> int:
        """Approve all non-expired pending approvals. Returns count of approvals approved."""
        pending = await self.list_pending()
        count = 0
        for p in pending:
            if await self._is_expired(p["approval_id"]):
                continue
            await self.approve(p["approval_id"])
            count += 1
        return count

    async def deny_all(self, reason: str = "Batch denied") -> int:
        """Deny all non-expired pending approvals. Returns count of approvals denied."""
        pending = await self.list_pending()
        count = 0
        for p in pending:
            if await self._is_expired(p["approval_id"]):
                continue
            await self.deny(p["approval_id"], reason)
            count += 1
        return count
