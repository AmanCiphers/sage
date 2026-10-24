"""Per-assignment budgets for an Employee (plan N8).

An employee is an autonomous LLM loop that can run away, so its work is
bounded. Historically all three ceilings (iterations, tokens, wall-clock)
were tight; by default **iterations and tokens are now unlimited** — tool
calls are cheap and an employee should be free to pursue an objective until
it is done. The **wall-clock ceiling stays** as the sole runaway guard, and
any ceiling set to `0`/`None` means "unlimited".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """An employee crossed a hard budget ceiling."""

    def __init__(self, kind: str, detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"budget exceeded ({kind}): {detail}")


@dataclass
class Budget:
    """Ceilings for one assignment. 0/None means 'unlimited'."""

    max_iterations: int = 0
    max_tokens: int = 0
    max_wall_seconds: float = 600.0


@dataclass
class BudgetTracker:
    """Tracks consumption of a `Budget` for a single assignment."""

    budget: Budget
    iterations: int = 0
    tokens: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def start_iteration(self, estimated_tokens: int = 0) -> None:
        """Call at the top of each loop turn."""

        self.iterations += 1
        if self.budget.max_iterations and self.iterations > self.budget.max_iterations:
            raise BudgetExceeded(
                "iterations",
                f"{self.iterations} > {self.budget.max_iterations}",
            )

        self.tokens += max(0, estimated_tokens)
        if self.budget.max_tokens and self.tokens > self.budget.max_tokens:
            raise BudgetExceeded(
                "tokens", f"{self.tokens} > {self.budget.max_tokens}"
            )

        self.check_time()

    def add_tokens(self, count: int) -> None:
        self.tokens += max(0, count)
        if self.budget.max_tokens and self.tokens > self.budget.max_tokens:
            raise BudgetExceeded(
                "tokens", f"{self.tokens} > {self.budget.max_tokens}"
            )

    def check_time(self) -> None:
        elapsed = time.monotonic() - self.started_at
        # Treat 0 or None as unlimited
        if self.budget.max_wall_seconds and elapsed > self.budget.max_wall_seconds:
            raise BudgetExceeded(
                "wall_clock",
                f"{elapsed:.1f}s > {self.budget.max_wall_seconds:.0f}s",
            )

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at
