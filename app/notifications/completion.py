"""Completion & reminder notices for background/async events.

When an employee finishes (or fails), or a scheduled reminder fires, SAGE
should tell the user — but it must never interrupt a response it is already
composing. So we do not push into the middle of a turn. Instead we enqueue a
ready-to-render line, and the front end drains the queue at a safe boundary
(when SAGE is idle, between turns).

The queue is the delivery gate: `drain()` is only ever called by the turn
runner while nothing else is streaming.
"""

from __future__ import annotations

import asyncio
import logging

from app.events.models import Event, EventTypes

logger = logging.getLogger(__name__)

# Event types that warrant telling the user, and how to phrase them.
# `reminder` is the custom event_type SAGE uses when scheduling a reminder;
# the scheduler always publishes `scheduled_job_completed` as well.
_INTERESTING = {
    EventTypes.EMPLOYEE_FINISHED,
    EventTypes.TASK_FAILED,
    EventTypes.SCHEDULED_JOB_TRIGGERED,
    EventTypes.SCHEDULED_JOB_COMPLETED,
    EventTypes.PROACTIVE_ACTION_TAKEN,
    "reminder",
}


class CompletionNotifier:
    """Collects employee-finished events into a FIFO of user-facing lines."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        # Recently-surfaced reminder job_ids, to avoid double-notifying when
        # both the `reminder` event and `scheduled_job_completed` fire for the
        # same job in the same trigger.
        self._recent_reminders: set[str] = set()

    async def handle_event(self, event: Event) -> None:
        if event.event_type not in _INTERESTING:
            return

        payload = event.payload or {}

        if (
            event.event_type == "reminder"
            or event.event_type
            in (EventTypes.SCHEDULED_JOB_TRIGGERED, EventTypes.SCHEDULED_JOB_COMPLETED)
        ):
            name = payload.get("job_name") or payload.get("name") or "Scheduled job"
            job_id = payload.get("job_id", "")
            # Dedupe: only surface each reminder job once per trigger cycle.
            if job_id in self._recent_reminders:
                return
            if job_id:
                self._recent_reminders.add(job_id)
            # The reminder body may be in the payload template (e.g. a `message` key).
            body = (
                payload.get("message")
                or payload.get("reminder")
                or payload.get("body")
                or "reminder"
            )
            line = f"⏰ {name}: {body}"
            if job_id:
                line += f" ({job_id})"
            await self._queue.put(line)
            logger.debug("reminder_notice_queued job_id=%s", job_id)
            return

        if event.event_type == EventTypes.PROACTIVE_ACTION_TAKEN:
            name = payload.get("rule_name") or "Proactive action"
            line = f"⚡ {name}"
            await self._queue.put(line)
            return

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
        # Clear the reminder-dedupe set so a recurring job notifies again next time.
        if self._recent_reminders:
            self._recent_reminders.clear()
        return notices

    @property
    def pending(self) -> int:
        return self._queue.qsize()
