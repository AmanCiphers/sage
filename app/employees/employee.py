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
    """Outcome of one assignment."""

    success: bool
    output: str | None = None
    error: str | None = None
    unknown: bool = False
    iterations: int = 0
    tokens: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class Employee:
    """A roster member with its own isolated runtime."""

    spec: EmployeeSpec
    workspace: Path
    loop: "ConversationLoop" = field(repr=False)
    budget: Budget = field(default_factory=Budget)

    async def work(
        self,
        objective: str,
        on_progress: Callable[[LoopEvent], Awaitable[None]] | None = None,
    ) -> EmployeeResult:
        """Run one objective to completion under the assignment budget."""

        tracker = BudgetTracker(budget=self.budget)

        async def _bridge(event: LoopEvent) -> None:
            if on_progress is not None:
                try:
                    await on_progress(event)
                except Exception:  # noqa: BLE001 — progress is best-effort
                    logger.debug("employee progress callback failed", exc_info=True)

        # Count iterations as they start (the loop emits llm_start per turn).
        self.loop.on_event = _bridge

        try:
            # Iteration/token ceilings are enforced by wrapping the event
            # stream: each llm_start is one iteration.
            async def _guarded(event: LoopEvent) -> None:
                if event.type == "llm_start":
                    tracker.start_iteration()
                await _bridge(event)

            self.loop.on_event = _guarded

            response = await self.loop.handle(UserMessage(content=objective))

            return EmployeeResult(
                success=True,
                output=response.content or "",
                iterations=tracker.iterations,
                tokens=tracker.tokens,
                elapsed_seconds=tracker.elapsed,
            )

        except BudgetExceeded as exc:
            logger.warning(
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
        except Exception as exc:  # noqa: BLE001 — report honestly
            logger.exception("employee_failed employee=%s", self.spec.key)
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

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name
