"""Scheduler persistence layer."""

import json

from app.persistence.database import Database
from app.scheduler.models import ProactiveRule, ScheduledJob


class SchedulerStore:
    """Persisted scheduled jobs and proactive rules."""

    def __init__(self, database: Database):
        self.database = database

    async def initialize(self) -> None:
        """Create scheduler tables."""
        await self.database.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                job_id            TEXT PRIMARY KEY,
                name              TEXT NOT NULL,
                cron_expression   TEXT NOT NULL,
                event_type        TEXT NOT NULL,
                payload_template  TEXT NOT NULL,
                enabled           INTEGER NOT NULL DEFAULT 1,
                created_at        TEXT NOT NULL,
                last_run_at       TEXT,
                next_run_at       TEXT,
                run_count         INTEGER NOT NULL DEFAULT 0,
                max_runs          INTEGER,
                timezone          TEXT NOT NULL DEFAULT 'UTC'
            )
        """)
        await self.database.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_next_run 
            ON scheduled_jobs(next_run_at) WHERE enabled = 1
        """)  # noqa: W291

        await self.database.execute("""
            CREATE TABLE IF NOT EXISTS proactive_rules (
                rule_id                 TEXT PRIMARY KEY,
                name                    TEXT NOT NULL,
                description             TEXT NOT NULL,
                trigger_event_type      TEXT NOT NULL,
                condition               TEXT NOT NULL,
                action_event_type       TEXT NOT NULL,
                action_payload_template TEXT NOT NULL,
                enabled                 INTEGER NOT NULL DEFAULT 1,
                cooldown_seconds        INTEGER NOT NULL DEFAULT 300,
                last_fired_at           TEXT,
                fire_count              INTEGER NOT NULL DEFAULT 0,
                created_at              TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await self.database.execute("""
            CREATE INDEX IF NOT EXISTS idx_rules_trigger 
            ON proactive_rules(trigger_event_type) WHERE enabled = 1
        """)  # noqa: W291
        await self.database.commit()

        # Additive column upgrades for existing databases
        await self._ensure_columns()

    async def _ensure_columns(self) -> None:
        """Additive column upgrades for scheduler tables."""
        # proactive_rules: created_at
        cursor = await self.database.execute("PRAGMA table_info(proactive_rules)")
        existing = {row[1] for row in await cursor.fetchall()}
        if "created_at" not in existing:
            await self.database.execute(
                "ALTER TABLE proactive_rules ADD COLUMN created_at TEXT DEFAULT (datetime('now'))"
            )
            await self.database.commit()

    # --- Scheduled Jobs ---

    async def create_job(self, job: ScheduledJob) -> str:
        await self.database.execute(
            """
            INSERT INTO scheduled_jobs 
            (job_id, name, cron_expression, event_type, payload_template, enabled, created_at, next_run_at, max_runs, timezone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,  # noqa: E501, W291
            (
                job.job_id,
                job.name,
                job.cron_expression,
                job.event_type,
                json.dumps(job.payload_template),
                1 if job.enabled else 0,
                job.created_at,
                job.next_run_at,
                job.max_runs,
                job.timezone,
            ),
        )
        await self.database.commit()
        return job.job_id

    async def get_job(self, job_id: str) -> ScheduledJob | None:
        cursor = await self.database.execute(
            "SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_job(row)

    async def list_jobs(self, enabled_only: bool = False) -> list[ScheduledJob]:
        query = "SELECT * FROM scheduled_jobs"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at DESC"
        cursor = await self.database.execute(query)
        rows = await cursor.fetchall()
        return [self._row_to_job(row) for row in rows]

    async def update_job(self, job: ScheduledJob) -> None:
        await self.database.execute(
            """
            UPDATE scheduled_jobs
            SET name = ?, cron_expression = ?, event_type = ?, payload_template = ?,
                enabled = ?, next_run_at = ?, run_count = ?, last_run_at = ?
            WHERE job_id = ?
            """,
            (
                job.name,
                job.cron_expression,
                job.event_type,
                json.dumps(job.payload_template),
                1 if job.enabled else 0,
                job.next_run_at,
                job.run_count,
                job.last_run_at,
                job.job_id,
            ),
        )
        await self.database.commit()

    async def delete_job(self, job_id: str) -> bool:
        cursor = await self.database.execute(
            "DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        )
        await self.database.commit()
        return cursor.rowcount > 0

    async def get_due_jobs(self, now: str) -> list[ScheduledJob]:
        """Get all enabled jobs whose next_run_at <= now."""
        cursor = await self.database.execute(
            """
            SELECT * FROM scheduled_jobs
            WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (now,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_job(row) for row in rows]

    def _row_to_job(self, row: tuple) -> ScheduledJob:
        return ScheduledJob(
            job_id=row[0],
            name=row[1],
            cron_expression=row[2],
            event_type=row[3],
            payload_template=json.loads(row[4]) if row[4] else {},
            enabled=bool(row[5]),
            created_at=row[6],
            last_run_at=row[7],
            next_run_at=row[8],
            run_count=row[9],
            max_runs=row[10],
            timezone=row[11],
        )

    # --- Proactive Rules ---

    async def create_rule(self, rule: ProactiveRule) -> str:
        await self.database.execute(
            """
            INSERT INTO proactive_rules
            (rule_id, name, description, trigger_event_type, condition, action_event_type,
             action_payload_template, enabled, cooldown_seconds, last_fired_at, fire_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,  # noqa: E501
            (
                rule.rule_id,
                rule.name,
                rule.description,
                rule.trigger_event_type,
                rule.condition,
                rule.action_event_type,
                json.dumps(rule.action_payload_template),
                1 if rule.enabled else 0,
                rule.cooldown_seconds,
                rule.last_fired_at,
                rule.fire_count,
                rule.created_at,
            ),
        )
        await self.database.commit()
        return rule.rule_id

    async def get_rule(self, rule_id: str) -> ProactiveRule | None:
        cursor = await self.database.execute(
            "SELECT * FROM proactive_rules WHERE rule_id = ?", (rule_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_rule(row)

    async def list_rules(self, enabled_only: bool = False, trigger_type: str | None = None) -> list[ProactiveRule]:  # noqa: E501
        query = "SELECT * FROM proactive_rules"
        params = []
        conditions = []
        if enabled_only:
            conditions.append("enabled = 1")
        if trigger_type:
            conditions.append("trigger_event_type = ?")
            params.append(trigger_type)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"
        cursor = await self.database.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_rule(row) for row in rows]

    async def update_rule(self, rule: ProactiveRule) -> None:
        await self.database.execute(
            """
            UPDATE proactive_rules
            SET name = ?, description = ?, trigger_event_type = ?, condition = ?,
                action_event_type = ?, action_payload_template = ?, enabled = ?,
                cooldown_seconds = ?, last_fired_at = ?, fire_count = ?
            WHERE rule_id = ?
            """,
            (
                rule.name,
                rule.description,
                rule.trigger_event_type,
                rule.condition,
                rule.action_event_type,
                json.dumps(rule.action_payload_template),
                1 if rule.enabled else 0,
                rule.cooldown_seconds,
                rule.last_fired_at,
                rule.fire_count,
                rule.rule_id,
            ),
        )
        await self.database.commit()

    async def delete_rule(self, rule_id: str) -> bool:
        cursor = await self.database.execute(
            "DELETE FROM proactive_rules WHERE rule_id = ?", (rule_id,)
        )
        await self.database.commit()
        return cursor.rowcount > 0

    def _row_to_rule(self, row: tuple) -> ProactiveRule:
        return ProactiveRule(
            rule_id=row[0],
            name=row[1],
            description=row[2],
            trigger_event_type=row[3],
            condition=row[4],
            action_event_type=row[5],
            action_payload_template=json.loads(row[6]) if row[6] else {},
            enabled=bool(row[7]),
            cooldown_seconds=row[8],
            last_fired_at=row[9],
            fire_count=row[10],
            created_at=row[11] if len(row) > 11 else None,
        )
