"""Scheduler engine for cron-like job execution."""

import asyncio
from datetime import datetime
from typing import Any

import croniter

from app.events.bus import EventBus
from app.events.models import EventTypes
from app.observability.logging import get_logger
from app.scheduler.models import ScheduledJob
from app.scheduler.store import SchedulerStore

logger = get_logger(__name__)


class SchedulerEngine:
    """Cron-based scheduler that publishes events on schedule (ArchitectureV2 §36)."""

    def __init__(
        self,
        store: SchedulerStore,
        event_bus: EventBus,
        check_interval_seconds: int = 30,
    ):
        self.store = store
        self.event_bus = event_bus
        self.check_interval = check_interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("scheduler_started", interval=self.check_interval)

    async def stop(self) -> None:
        """Stop the scheduler loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:  # noqa: SIM105
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("scheduler_stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._check_and_trigger()
            except Exception:
                logger.exception("scheduler_loop_error")
            await asyncio.sleep(self.check_interval)

    async def _check_and_trigger(self) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        due_jobs = await self.store.get_due_jobs(now)

        for job in due_jobs:
            await self._trigger_job(job, now)

    async def _trigger_job(self, job: ScheduledJob, now: str) -> None:
        """Trigger a scheduled job by publishing its event."""
        logger.info("scheduled_job_triggered", job_id=job.job_id, name=job.name)

        # Create and publish the event
        event = self.event_bus.create_event(
            event_type=job.event_type,
            source="scheduler",
            payload={
                "job_id": job.job_id,
                "job_name": job.name,
                "triggered_at": now,
                **job.payload_template,
            },
            correlation_id=job.job_id,
        )

        await self.event_bus.publish(event)

        # Update job state
        job.last_run_at = now
        job.run_count += 1

        # Calculate next run
        try:
            cron = croniter.croniter(job.cron_expression, datetime.utcnow())
            next_run = cron.get_next(datetime)
            job.next_run_at = next_run.isoformat() + "Z"
        except Exception as exc:
            logger.warning("cron_parse_failed", job_id=job.job_id, error=str(exc))
            job.enabled = False
            job.next_run_at = None

        # Check max runs
        if job.max_runs and job.run_count >= job.max_runs:
            job.enabled = False
            job.next_run_at = None
            logger.info("job_max_runs_reached", job_id=job.job_id)

        await self.store.update_job(job)

        # Publish completion event
        completion_event = self.event_bus.create_event(
            event_type=EventTypes.SCHEDULED_JOB_COMPLETED,
            source="scheduler",
            payload={
                "job_id": job.job_id,
                "job_name": job.name,
                "completed_at": now,
                "run_count": job.run_count,
            },
            correlation_id=job.job_id,
        )
        await self.event_bus.publish(completion_event)

    async def add_job(
        self,
        name: str,
        cron_expression: str,
        event_type: str,
        payload_template: dict[str, Any] | None = None,
        max_runs: int | None = None,
        timezone: str = "UTC",
    ) -> str:
        """Create and schedule a new job."""
        # Validate cron expression
        try:
            cron = croniter.croniter(cron_expression, datetime.utcnow())
            next_run = cron.get_next(datetime)
        except Exception as exc:
            raise ValueError(f"Invalid cron expression: {exc}")  # noqa: B904

        job = ScheduledJob(
            name=name,
            cron_expression=cron_expression,
            event_type=event_type,
            payload_template=payload_template or {},
            next_run_at=next_run.isoformat() + "Z",
            max_runs=max_runs,
            timezone=timezone,
        )

        await self.store.create_job(job)
        logger.info("job_created", job_id=job.job_id, name=name, next_run=job.next_run_at)
        return job.job_id

    async def remove_job(self, job_id: str) -> bool:
        return await self.store.delete_job(job_id)

    async def enable_job(self, job_id: str, enabled: bool = True) -> bool:
        job = await self.store.get_job(job_id)
        if not job:
            return False
        job.enabled = enabled
        if enabled and not job.next_run_at:
            # Recalculate next run
            try:
                cron = croniter.croniter(job.cron_expression, datetime.utcnow())
                next_run = cron.get_next(datetime)
                job.next_run_at = next_run.isoformat() + "Z"
            except Exception:
                return False
        await self.store.update_job(job)
        return True

    async def list_jobs(self) -> list[ScheduledJob]:
        return await self.store.list_jobs(enabled_only=False)

    async def get_job(self, job_id: str) -> ScheduledJob | None:
        return await self.store.get_job(job_id)
