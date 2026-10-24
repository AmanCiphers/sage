"""An Employee: a named, lazy agent that works for SAGE (plan Phase 2).

An employee is **not** a diminished SAGE and **not** the old process worker
(which had no mind). It is a small autonomous agent:

- its own `ConversationLoop` and inference,
- its own filtered `ToolRegistry`,
- its own workspace + memory (`data/employees/<key>/`),
- a slim role card (mandate), not a full personality,
- hard budgets (iterations / tokens / wall-clock).

Employees run as **lazy asyncio tasks inside SAGE's process** (N3): the
loop only exists while an assignment is running, then the employee idles.
They report to SAGE and never address the user directly.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.conversation.models import LoopEvent, UserMessage
from app.employees.budgets import Budget, BudgetExceeded, BudgetTracker
from app.employees.roster import EmployeeSpec

if TYPE_CHECKING:
    from app.conversation.loop import ConversationLoop

logger = logging.getLogger(__name__)


@dataclass
class EmployeeResult:
    """Outcome of one assignment.

    `verification` is the employee's own evidence level, not a guarantee:
    - "self-verified": a side-effecting tool completed successfully, so the
      employee observed a real change (file written, command run, ...).
    - "unverified": the employee only read/reasoned — its claim is its word.
    SAGE reports this honestly; it is never upgraded on SAGE's behalf.
    """

    success: bool
    output: str | None = None
    error: str | None = None
    unknown: bool = False
    iterations: int = 0
    tokens: int = 0
    elapsed_seconds: float = 0.0
    verification: str = "unverified"
    verification_detail: str | None = None


@dataclass
class Employee:
    """A roster member with its own isolated runtime."""

    spec: EmployeeSpec
    workspace: Path
    loop: ConversationLoop = field(repr=False)
    budget: Budget = field(default_factory=Budget)

    async def work(
        self,
        objective: str,
        on_progress: Callable[[LoopEvent], Awaitable[None]] | None = None,
    ) -> EmployeeResult:
        """Run one objective to completion under the assignment budget."""

        # Use per-employee logger if available, fall back to module logger
        emp_logger = getattr(self, "_logger", logger)

        emp_logger.info("employee_work_started employee=%s objective=%s", self.spec.key, objective)

        tracker = BudgetTracker(budget=self.budget)

        async def _bridge(event: LoopEvent) -> None:
            if on_progress is not None:
                try:
                    await on_progress(event)
                except Exception:
                    emp_logger.debug("employee_progress_callback_failed", exc_info=True)

        # Count iterations as they start (the loop emits llm_start per turn).
        self.loop.on_event = _bridge

        # Route file/shell tools to this employee's own workspace for the
        # duration of the turn (context var — safe under asyncio task copy).
        from app.tools.workspace import reset_workspace, use_workspace

        workspace_token = use_workspace(self.workspace)

        try:
            # Iteration/token ceilings are enforced by wrapping the event
            # stream: each llm_start is one iteration. We also watch for
            # successful side-effecting tools so we can report honest
            # verification instead of a blanket "unverified".
            side_effects: list[str] = []

            async def _guarded(event: LoopEvent) -> None:
                if event.type == "llm_start":
                    tracker.start_iteration()
                    emp_logger.debug("llm_iteration_start iteration=%s", tracker.iterations)
                elif event.type == "tool_start" and event.tool_name:
                    args = getattr(event, "arguments", None)
                    emp_logger.info("tool_start tool=%s args=%s", event.tool_name, args)
                elif event.type == "tool_end" and event.tool_name:
                    err = getattr(event, "error", None)
                    emp_logger.info(
                        "tool_end tool=%s success=%s error=%s",
                        event.tool_name,
                        event.success,
                        err,
                    )
                    if (
                        event.success
                        and self._is_side_effecting(event.tool_name)
                        and event.tool_name not in side_effects
                    ):
                        side_effects.append(event.tool_name)
                await _bridge(event)

            self.loop.on_event = _guarded

            response = await self.loop.handle(UserMessage(content=objective))

            if side_effects:
                verification = "self-verified"
                detail = "employee ran side-effecting tool(s): " + ", ".join(side_effects)
            else:
                verification = "unverified"
                detail = "employee result (no side-effecting tool observed)"

            emp_logger.info(
                "employee_work_completed employee=%s success=%s verification=%s "
                "iterations=%s tokens=%s elapsed=%.2f",
                self.spec.key,
                True,
                verification,
                tracker.iterations,
                tracker.tokens,
                tracker.elapsed,
            )

            return EmployeeResult(
                success=True,
                output=response.content or "",
                iterations=tracker.iterations,
                tokens=tracker.tokens,
                elapsed_seconds=tracker.elapsed,
                verification=verification,
                verification_detail=detail,
            )

        except BudgetExceeded as exc:
            emp_logger.warning(
                "employee_budget_exceeded employee=%s kind=%s detail=%s",
                self.spec.key,
                exc.kind,
                exc.detail,
            )
            return EmployeeResult(
                success=False,
                error=str(exc),
                iterations=tracker.iterations,
                tokens=tracker.tokens,
                elapsed_seconds=tracker.elapsed,
            )
        except Exception as exc:
            emp_logger.exception("employee_failed employee=%s", self.spec.key)
            return EmployeeResult(
                success=False,
                error=str(exc),
                unknown=False,
                iterations=tracker.iterations,
                tokens=tracker.tokens,
                elapsed_seconds=tracker.elapsed,
            )
        finally:
            self.loop.on_event = None
            reset_workspace(workspace_token)

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name

    def _is_side_effecting(self, tool_name: str) -> bool:
        """True when the tool mutates state (so its success is evidence)."""

        from app.tools.models import ToolClassification

        try:
            tool = self.loop.tool_registry.get(tool_name)
        except KeyError:
            return False

        return tool.classification in (
            ToolClassification.LOCAL_MUTATION,
            ToolClassification.EXTERNAL_SIDE_EFFECT,
        )
