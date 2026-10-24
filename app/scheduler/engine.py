"""Scheduler engine for cron-like job execution."""

import asyncio
from datetime import UTC, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import croniter

from app.events.bus import EventBus
from app.events.models import EventTypes
from app.observability.logging import get_logger
from app.scheduler.models import ScheduledJob
from app.scheduler.store import SchedulerStore

logger = get_logger(__name__)

DEFAULT_TIMEZONE = "UTC"


def _tz_for(name: str) -> timezone | ZoneInfo:
    """Return a tzinfo for a zone name, defaulting to UTC on failure."""
    if not name or name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown_timezone_falling_back_to_utc", tz=name)
        return UTC


def _now_utc() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def _iso_utc(dt: datetime) -> str:
    """Format a timezone-aware datetime as UTC ISO-8601 with Z."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _next_run(expr: str, tz_name: str) -> datetime:
    """Compute the next cron run in the job's timezone, returned as UTC-aware."""
    tz = _tz_for(tz_name)
    base = datetime.now(tz)
    cron = croniter.croniter(expr, base)
    nxt = cron.get_next(datetime)
    return nxt.astimezone(UTC)


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
        now = _iso_utc(_now_utc())
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

        # Calculate next run in the job's timezone (stored as UTC).
        try:
            job.next_run_at = _iso_utc(_next_run(job.cron_expression, job.timezone))
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
        cron_expression: str | None = None,
        event_type: str = "",
        payload_template: dict[str, Any] | None = None,
        max_runs: int | None = None,
        timezone: str | None = None,
        run_at: str | None = None,
    ) -> str:
        """Create and schedule a new job.

        Either `cron_expression` (recurring) or `run_at` (one-time, an
        absolute ISO datetime) must be provided. `run_at` allows sub-minute
        precision that cron cannot express.
        """
        if not timezone:
            from app.config import settings

            timezone = settings.timezone

        if run_at:
            # One-time absolute datetime. Parse it as timezone-aware; if naive,
            # assume the job's timezone.
            try:
                dt = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_tz_for(timezone))
                next_run_utc = dt.astimezone(UTC)
                cron_expr = run_at  # stored for reference / list display
            except Exception as exc:
                raise ValueError(f"Invalid run_at datetime: {exc}")  # noqa: B904
        else:
            if not cron_expression:
                raise ValueError("Either cron_expression or run_at is required")
            try:
                next_run_utc = _next_run(cron_expression, timezone)
            except Exception as exc:
                raise ValueError(f"Invalid cron expression: {exc}")  # noqa: B904
            cron_expr = cron_expression

        job = ScheduledJob(
            name=name,
            cron_expression=cron_expr,
            event_type=event_type,
            payload_template=payload_template or {},
            next_run_at=_iso_utc(next_run_utc),
            max_runs=max_runs if max_runs is not None else (1 if run_at else None),
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
            # Recalculate next run in the job's timezone.
            try:
                job.next_run_at = _iso_utc(_next_run(job.cron_expression, job.timezone))
            except Exception:
                return False
        await self.store.update_job(job)
        return True

    async def list_jobs(self) -> list[ScheduledJob]:
        return await self.store.list_jobs(enabled_only=False)

    async def get_job(self, job_id: str) -> ScheduledJob | None:
        return await self.store.get_job(job_id)
