"""Supervisor: SAGE's interface to the employee roster (plan Phase 3).

Replaces the old `TaskDispatcher`. SAGE calls `assign(worker, objective)`
and gets a `task_id` back immediately; the employee does the work as a
lazy asyncio task while SAGE stays free.

Frozen contract (docs/WORKER_EMPLOYEE_PLAN.md §2) — SAGE depends only on:

    assign(worker, objective) -> task_id
    get_status(task_id)       -> dict   (same keys as before, additive only)
    recover()                 -> int

Honesty rules carry over: outcome failures are `failed`; an indeterminate
outcome (budget breach mid-action, cancel) is `unknown` — never silently
retried or reported as failed.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.employees.budgets import Budget
from app.employees.employee import Employee, EmployeeResult
from app.employees.factory import build_employee, close_employee
from app.employees.roster import ROSTER, EmployeeSpec
from app.employees.roster import get as get_spec
from app.events.bus import EventBus
from app.events.models import Event, EventTypes
from app.inference.manager import InferenceManager
from app.permissions.engine import PermissionEngine
from app.persistence.store import TaskStore
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class Supervisor:
    def __init__(
        self,
        task_store: TaskStore,
        permission_engine: PermissionEngine,
        registry: ToolRegistry,
        inference_manager: InferenceManager,
        model_registry,
        event_bus: EventBus | None = None,
        default_budget: Budget | None = None,
        max_result_chars: int = 8000,
    ) -> None:
        self.task_store = task_store
        self.permission_engine = permission_engine
        self.registry = registry
        self.inference_manager = inference_manager
        self.model_registry = model_registry
        self.event_bus = event_bus
        self.default_budget = default_budget or Budget()
        self.max_result_chars = max_result_chars

        # Lazily-built employees, keyed by lowercase worker key.
        self._employees: dict[str, Employee] = {}
        # In-flight assignments, keyed by task_id.
        self._background: dict[str, asyncio.Task] = {}
        # Worker key for each in-flight task_id (for roster_status).
        self._in_flight: dict[str, str] = {}
        # Live progress per in-flight task_id (stage, last tool, ...).
        self._progress: dict[str, dict] = {}

    async def _employee_for(self, spec: EmployeeSpec) -> Employee:
        employee = self._employees.get(spec.key)
        if employee is None:
            employee = await build_employee(
                spec,
                inference_manager=self.inference_manager,
                model_registry=self.model_registry,
                source_registry=self.registry,
                permission_engine=self.permission_engine,
                budget=self.default_budget,
            )
            self._employees[spec.key] = employee
        return employee

    async def _publish(self, event_type: str, payload: dict) -> None:
        if self.event_bus is None:
            return
        try:
            await self.event_bus.publish(
                Event(
                    event_type=event_type,
                    source="employee",
                    payload=payload,
                )
            )
        except Exception:
            logger.debug("event publish failed", exc_info=True)

    async def assign(self, worker: str, objective: str) -> str:
        """Create a durable task and run it on the named employee."""

        spec = get_spec(worker)  # raises KeyError for unknown worker

        task_id = await self.task_store.create(
            objective=objective,
            tool_name=f"employee:{spec.key}",
            arguments={},
            worker=spec.key,
        )

        await self._publish(
            EventTypes.EMPLOYEE_ASSIGNED,
            {
                "task_id": task_id,
                "worker": spec.key,
                "name": spec.name,
                "objective": objective,
            },
        )

        self._background[task_id] = asyncio.create_task(
            self._run(task_id, spec, objective)
        )
        self._in_flight[task_id] = spec.key

        logger.info(
            "assignment_created task_id=%s worker=%s", task_id, spec.key
        )
        return task_id

    async def _run(
        self, task_id: str, spec: EmployeeSpec, objective: str
    ) -> None:
        await self.task_store.set_status(task_id, "running")
        await self._publish(
            EventTypes.TASK_STARTED,
            {"task_id": task_id, "worker": spec.key, "name": spec.name},
        )

        try:
            employee = await self._employee_for(spec)
            result: EmployeeResult = await employee.work(
                objective,
                on_progress=self._make_progress(task_id, spec),
            )
        except Exception as exc:
            logger.exception("assignment_failed task_id=%s", task_id)
            await self.task_store.set_status(task_id, "failed", error=str(exc))
            await self._publish(
                EventTypes.TASK_FAILED,
                {"task_id": task_id, "worker": spec.key, "error": str(exc)},
            )
            self._background.pop(task_id, None)
            self._in_flight.pop(task_id, None)
            self._progress.pop(task_id, None)
            return

        if result.unknown:
            await self.task_store.set_status(
                task_id, "unknown", error=result.error
            )
        elif not result.success:
            await self.task_store.set_status(
                task_id, "failed", error=result.error
            )
            await self._publish(
                EventTypes.TASK_FAILED,
                {
                    "task_id": task_id,
                    "worker": spec.key,
                    "error": result.error,
                },
            )
        else:
            output = (result.output or "")[: self.max_result_chars]
            await self.task_store.set_status(
                task_id,
                "completed",
                result=output,
                verification=result.verification,
                verification_detail=result.verification_detail,
            )
            await self._publish(
                EventTypes.EMPLOYEE_FINISHED,
                {
                    "task_id": task_id,
                    "worker": spec.key,
                    "name": spec.name,
                    "summary": f"{spec.name} finished the assigned work",
                },
            )

        self._background.pop(task_id, None)
        self._in_flight.pop(task_id, None)
        self._progress.pop(task_id, None)

    def _make_progress(self, task_id: str, spec: EmployeeSpec):
        async def _on_progress(event) -> None:
            # Surface meaningful employee loop events on the bus, and keep a
            # live snapshot so task_status can tell progress from a stall.
            if event.type in ("llm_start", "tool_start", "tool_end"):
                snapshot = self._progress.setdefault(task_id, {})
                snapshot["stage"] = event.type
                snapshot["updated_at"] = time.time()
                if event.type == "llm_start":
                    snapshot["iterations"] = snapshot.get("iterations", 0) + 1
                tool_name = getattr(event, "tool_name", None)
                if tool_name:
                    snapshot["last_tool"] = tool_name
                await self._publish(
                    EventTypes.EMPLOYEE_ACTIVITY,
                    {
                        "task_id": task_id,
                        "worker": spec.key,
                        "name": spec.name,
                        "stage": event.type,
                        "tool": tool_name,
                    },
                )

        return _on_progress

    async def recover(self) -> int:
        """Startup: assignments left in-flight by a previous SAGE process
        become `unknown` — their employees are gone."""

        recovered = await self.task_store.recover_stuck()
        if recovered:
            logger.warning("stale_assignments_recovered count=%s", recovered)
        return recovered

    async def get_status(self, task_id: str) -> dict | None:
        task = await self.task_store.get(task_id)
        if task is None:
            return None

        # Additive progress fields: let callers tell "running productively"
        # from "stuck" instead of guessing from the status word alone.
        progress = self._progress.get(task_id, {})
        task["stage"] = progress.get("stage")
        task["last_tool"] = progress.get("last_tool")
        task["iterations"] = progress.get("iterations")
        updated_at = progress.get("updated_at")
        task["last_activity_at"] = updated_at
        task["seconds_since_activity"] = (
            round(time.time() - updated_at, 1) if updated_at else None
        )
        return task

    def roster_status(self) -> list[dict]:
        """Snapshot of the roster for the API/UI (Phase 6).

        Additive shape: every roster member, plus whether it has an
        in-memory loop built and which assignments are running now.
        """

        running_by_worker: dict[str, list[str]] = {}
        for task_id, worker in self._in_flight.items():
            running_by_worker.setdefault(worker, []).append(task_id)

        return [
            {
                "worker": spec.key,
                "id": spec.id,
                "name": spec.name,
                "status": (
                    "active"
                    if spec.key in running_by_worker
                    else "standby"
                ),
                "running_tasks": running_by_worker.get(spec.key, []),
            }
            for spec in ROSTER
        ]

    async def activity(self, worker: str, limit: int = 20) -> list[dict]:
        """Recent assignments for one employee (validates the name)."""

        spec = get_spec(worker)  # raises KeyError for unknown worker
        return await self.task_store.list_for_worker(spec.key, limit=limit)

    async def shutdown(self) -> None:
        """Await outstanding assignments; cancel if they overrun."""

        tasks = list(self._background.values())
        if tasks:
            logger.info("supervisor_shutdown waiting_on=%s", len(tasks))
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background.clear()
        self._in_flight.clear()

        employees = list(self._employees.values())
        self._employees.clear()
        for employee in employees:
            try:
                await close_employee(employee)
            except Exception:
                logger.debug("employee close failed", exc_info=True)
