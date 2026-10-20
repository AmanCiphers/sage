"""Routes tool execution: inline (fast) or as a background worker.

`start_task` runs a tool in the background and returns a task_id
immediately, so the conversation loop never blocks on substantial work.
The conversation stays free for new questions while the task runs.

SAGE-side, always (the worker is not trusted — §60–62):
- static + dynamic permission checks before anything is dispatched,
- deterministic verification of the raw result,
- honest statuses: `failed` (definitely failed) vs `unknown` (outcome
  cannot be determined, §12 — never blindly retried).
"""

import asyncio
import logging

from app.execution.executor import ProcessExecutor, RunResult, TaskExecutor
from app.execution.verification import verify, has_verifier
from app.permissions.engine import PermissionEngine
from app.persistence.store import TaskStore
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class TaskDispatcher:
    def __init__(
        self,
        task_store: TaskStore,
        permission_engine: PermissionEngine,
        registry: ToolRegistry,
        executor: TaskExecutor | None = None,
        max_result_chars: int = 8000,
    ) -> None:
        self.task_store = task_store
        self.permission_engine = permission_engine
        self.registry = registry
        self.executor = executor or ProcessExecutor()
        self.max_result_chars = max_result_chars
        self._background: dict[str, asyncio.Task] = {}

    async def start(
        self,
        objective: str,
        tool_name: str,
        arguments: dict,
    ) -> str:
        """Create a durable task and start it in the background.

        All SAGE-side gates run HERE, before any background work starts:
        the tool must be worker-eligible, and its dynamic approval hook
        must pass — backgrounding never bypasses consent (D23/D24).
        """

        tool = self.registry.get(tool_name)

        if not tool.worker_eligible:
            raise PermissionError(
                f"'{tool_name}' cannot run in the background — run it inline"
            )

        if tool.requires_approval and tool.requires_approval(arguments):
            raise PermissionError(
                f"'{tool_name}' needs user approval for these arguments — "
                "run it inline first"
            )

        task_id = await self.task_store.create(
            objective=objective,
            tool_name=tool_name,
            arguments=arguments,
        )

        self._background[task_id] = asyncio.create_task(
            self._run(task_id, tool_name, arguments)
        )

        logger.info("task_started task_id=%s tool=%s", task_id, tool_name)

        return task_id

    async def _run(self, task_id: str, tool_name: str, arguments: dict) -> None:
        await self.task_store.set_status(task_id, "running")

        try:
            tool = self.registry.get(tool_name)
            decision = self.permission_engine.check(tool)

            if decision.value != "allowed":
                raise PermissionError(
                    f"Permission {decision.value} for tool '{tool_name}'"
                )

            # Defense in depth: the dynamic hook again (D23).
            if tool.requires_approval and tool.requires_approval(arguments):
                raise PermissionError(
                    f"'{tool_name}' requires user approval for these arguments"
                )

            result: RunResult = await self.executor.run(
                tool_name, arguments, task_id
            )

        except Exception as exc:
            logger.exception("task_failed task_id=%s", task_id)
            await self.task_store.set_status(task_id, "failed", error=str(exc))
            return

        if result.unknown:
            # The tool may or may not have run — do not pretend (§12).
            logger.warning("task_unknown task_id=%s error=%s", task_id, result.error)
            await self.task_store.set_status(
                task_id, "unknown", error=result.error
            )
            return

        if not result.success:
            await self.task_store.set_status(
                task_id, "failed", error=result.error
            )
            return

        output = (result.output or "")[: self.max_result_chars]

        # Only verify tools with registered verifiers (side-effecting tools).
        # Read-only tools like read_file, list_files, analyze skip verification.
        if has_verifier(tool_name):
            # Never trust the executor's claim — re-check real state (§60–61).
            verification = verify(tool_name, arguments, result.output)

            if verification.status == "failed":
                logger.warning(
                    "task_verification_failed task_id=%s detail=%s",
                    task_id,
                    verification.detail,
                )
                await self.task_store.set_status(
                    task_id,
                    "failed",
                    result=output,
                    error=f"verification failed: {verification.detail}",
                    verification=verification.status,
                    verification_detail=verification.detail,
                )
                return

            await self.task_store.set_status(
                task_id,
                "completed",
                result=output,
                verification=verification.status,
                verification_detail=verification.detail,
            )
        else:
            # No verifier: mark completed without verification noise.
            await self.task_store.set_status(
                task_id,
                "completed",
                result=output,
                verification="unverified",
                verification_detail="no verifier for this tool",
            )

    async def recover(self) -> int:
        """Startup: tasks left `queued`/`running` by a previous SAGE
        process are `unknown` — their workers are gone (§47)."""

        recovered = await self.task_store.recover_stuck()
        if recovered:
            logger.warning("stale_tasks_recovered count=%s", recovered)
        return recovered

    async def get_status(self, task_id: str) -> dict | None:
        """Non-blocking status lookup; reads durable state."""

        return await self.task_store.get(task_id)
