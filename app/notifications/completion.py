"""Completion notices for background assignments.

When an employee finishes (or fails), SAGE should tell the user — but it
must never interrupt a response it is already composing. So we do not push
into the middle of a turn. Instead we enqueue a ready-to-render line, and
the front end drains the queue at a safe boundary (when SAGE is idle,
between turns).

The queue is the delivery gate: `drain()` is only ever called by the turn
runner while nothing else is streaming.
"""

from __future__ import annotations

import asyncio
import logging

from app.events.models import Event, EventTypes

logger = logging.getLogger(__name__)

# Event types that warrant telling the user, and how to phrase them.
_INTERESTING = {
    EventTypes.EMPLOYEE_FINISHED,
    EventTypes.TASK_FAILED,
}


class CompletionNotifier:
    """Collects employee-finished events into a FIFO of user-facing lines."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def handle_event(self, event: Event) -> None:
        if event.event_type not in _INTERESTING:
            return

        payload = event.payload or {}
        name = payload.get("name") or payload.get("worker") or "An employee"
        task_id = payload.get("task_id", "")

        if event.event_type == EventTypes.TASK_FAILED:
            error = payload.get("error") or "unknown error"
            line = f"{name} failed {task_id}: {error}"
        else:
            summary = payload.get("summary") or "finished the assigned work"
            line = f"{name} finished {task_id} — {summary}"

        await self._queue.put(line)
        logger.debug("completion_notice_queued task_id=%s", task_id)

    def drain(self) -> list[str]:
        """Return all pending notices without blocking."""

        notices: list[str] = []
        while True:
            try:
                notices.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return notices

    @property
    def pending(self) -> int:
        return self._queue.qsize()
