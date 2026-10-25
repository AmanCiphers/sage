"""Operation Manager: tracks real-world side effects (ArchitectureV2 §15-17).

Every side-effecting execution gets an operation ID and a durable status:

    EXECUTING -> SUCCEEDED | FAILED
    EXECUTING -> UNKNOWN   (crash recovery: outcome cannot be determined)

Operations are recorded BEFORE execution so a crash never loses track of a
side effect. Approved actions and background tasks run through here.
"""

import json
import logging
import uuid
from typing import Any

from app.execution.engine import ExecutionEngine
from app.execution.models import ToolCall
from app.execution.verification import verify
from app.persistence.database import Database
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

RESULT_CAP_CHARS = 8000


class OperationManager:
    def __init__(
        self,
        database: Database,
        execution_engine: ExecutionEngine,
        registry: ToolRegistry,
    ) -> None:
        self.database = database
        self.execution_engine = execution_engine
        self.registry = registry

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a tool as a tracked operation."""

        operation_id = f"op_{uuid.uuid4().hex[:10]}"

        await self.database.execute(
            """
            INSERT INTO operations (operation_id, approval_id, tool_name, arguments_json, status)
            VALUES (?, ?, ?, ?, 'EXECUTING')
            """,
            (operation_id, approval_id, tool_name, json.dumps(arguments)),
        )
        await self.database.commit()

        try:
            result = await self.execution_engine.execute(
                ToolCall(tool_name=tool_name, arguments=arguments)
            )
        except Exception as exc:
            logger.exception("operation_crashed operation_id=%s", operation_id)
            await self._finish(operation_id, "FAILED", error=str(exc))
            return await self.get(operation_id)

        if result.success:
            output = str(result.output)[:RESULT_CAP_CHARS]
            # Outcomes are re-checked against real state before the
            # operation counts as successful (ArchitectureV2 §60).
            verification = await verify(tool_name, arguments, result.output)
            if verification.status == "failed":
                logger.warning(
                    "operation_verification_failed operation_id=%s detail=%s",
                    operation_id,
                    verification.detail,
                )
                await self._finish(
                    operation_id,
                    "FAILED",
                    result=output,
                    error=f"verification failed: {verification.detail}",
                )
            else:
                await self._finish(operation_id, "SUCCEEDED", result=output)
        else:
            await self._finish(operation_id, "FAILED", error=result.error)

        return await self.get(operation_id)

    async def recover(self) -> int:
        """On startup: any operation still EXECUTING is UNKNOWN — its
        outcome cannot be safely determined (ArchitectureV2 §12)."""

        cursor = await self.database.execute(
            "SELECT count(*) FROM operations WHERE status = 'EXECUTING'"
        )
        stuck = (await cursor.fetchone())[0]

        if stuck:
            await self.database.execute(
                """
                UPDATE operations
                SET status = 'UNKNOWN', updated_at = datetime('now')
                WHERE status = 'EXECUTING'
                """
            )
            await self.database.commit()
            logger.warning("operations_recovered_unknown count=%s", stuck)

        return stuck

    async def get(self, operation_id: str) -> dict[str, Any] | None:
        cursor = await self.database.execute(
            """
            SELECT operation_id, approval_id, tool_name, arguments_json, status, result, error
            FROM operations
            WHERE operation_id = ?
            """,
            (operation_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return {
            "operation_id": row[0],
            "approval_id": row[1],
            "tool_name": row[2],
            "arguments": json.loads(row[3]),
            "status": row[4],
            "result": row[5],
            "error": row[6],
        }

    async def _finish(
        self,
        operation_id: str,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        await self.database.execute(
            """
            UPDATE operations
            SET status = ?, result = ?, error = ?, updated_at = datetime('now')
            WHERE operation_id = ?
            """,
            (status, result, error, operation_id),
        )
        await self.database.commit()
