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

from app.employees.budgets import Budget
from app.employees.employee import Employee, EmployeeResult
from app.employees.factory import build_employee
from app.employees.roster import EmployeeSpec, display_name, get as get_spec
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
        except Exception:  # noqa: BLE001 — telemetry is best-effort
            logger.debug("event publish failed", exc_info=True)

    async def assign(self, worker: str, objective: str) -> str:
        """Create a durable task and run it on the named employee."""

        spec = get_spec(worker)  # raises KeyError for unknown worker

        task_id = await self.task_store.create(
            objective=objective,
            tool_name=f"employee:{spec.key}",
            arguments={},
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
        except Exception as exc:  # noqa: BLE001 — report honestly
            logger.exception("assignment_failed task_id=%s", task_id)
            await self.task_store.set_status(task_id, "failed", error=str(exc))
            await self._publish(
                EventTypes.TASK_FAILED,
                {"task_id": task_id, "worker": spec.key, "error": str(exc)},
            )
            self._background.pop(task_id, None)
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
                verification="unverified",
                verification_detail="employee result (no deterministic verifier)",
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

    def _make_progress(self, task_id: str, spec: EmployeeSpec):
        async def _on_progress(event) -> None:
            # Surface meaningful employee loop events on the bus.
            if event.type in ("llm_start", "tool_start", "tool_end"):
                await self._publish(
                    EventTypes.EMPLOYEE_ACTIVITY,
                    {
                        "task_id": task_id,
                        "worker": spec.key,
                        "name": spec.name,
                        "stage": event.type,
                        "tool": getattr(event, "tool_name", None),
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
        return await self.task_store.get(task_id)

    async def shutdown(self) -> None:
        """Await outstanding assignments; cancel if they overrun."""

        tasks = list(self._background.values())
        if tasks:
            logger.info("supervisor_shutdown waiting_on=%s", len(tasks))
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background.clear()
