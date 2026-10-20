"""Scheduler models for cron-like jobs and recurring tasks."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid


@dataclass
class ScheduledJob:
    """A recurring scheduled job (ArchitectureV2 §36)."""
    job_id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:10]}")
    name: str = ""
    cron_expression: str = ""  # Standard cron format
    event_type: str = ""  # Event to publish when triggered
    payload_template: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    last_run_at: str | None = None
    next_run_at: str | None = None
    run_count: int = 0
    max_runs: int | None = None  # None = unlimited
    timezone: str = "UTC"


@dataclass
class ProactiveRule:
    """A proactive behavior rule (ArchitectureV2 §56)."""
    rule_id: str = field(default_factory=lambda: f"rule_{uuid.uuid4().hex[:10]}")
    name: str = ""
    description: str = ""
    trigger_event_type: str = ""  # Event that triggers evaluation
    condition: str = ""  # Python expression evaluating to bool
    action_event_type: str = ""  # Event to publish if condition true
    action_payload_template: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    cooldown_seconds: int = 300  # Minimum time between firings
    last_fired_at: str | None = None
    fire_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")