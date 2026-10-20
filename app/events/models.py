"""Event models for the SAGE event system."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid


@dataclass
class Event:
    """Structured event (ArchitectureV2 §37)."""
    event_type: str
    source: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    sequence_number: int = 0
    correlation_id: str | None = None  # For tracing related events

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "timestamp": self.timestamp,
            "sequence_number": self.sequence_number,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            source=data["source"],
            payload=data["payload"],
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
            sequence_number=data.get("sequence_number", 0),
            correlation_id=data.get("correlation_id"),
        )


# Standard event types (ArchitectureV2 §37)
class EventTypes:
    # System events
    SYSTEM_STARTED = "system_started"
    SYSTEM_SHUTDOWN = "system_shutdown"
    
    # Worker events
    WORKER_STARTED = "worker_started"
    WORKER_COMPLETED = "worker_completed"
    WORKER_FAILED = "worker_failed"

    # Employee (agent-worker) events
    EMPLOYEE_ASSIGNED = "employee_assigned"
    EMPLOYEE_ACTIVITY = "employee_activity"
    EMPLOYEE_FINISHED = "employee_finished"
    
    # Task events
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    
    # Schedule events
    SCHEDULED_JOB_TRIGGERED = "scheduled_job_triggered"
    SCHEDULED_JOB_COMPLETED = "scheduled_job_completed"
    SCHEDULED_JOB_FAILED = "scheduled_job_failed"
    
    # Proactive events
    PROACTIVE_CHECK_TRIGGERED = "proactive_check_triggered"
    PROACTIVE_ACTION_TAKEN = "proactive_action_taken"
    
    # External events
    EMAIL_RECEIVED = "email_received"
    FILE_CHANGED = "file_changed"
    NETWORK_RESTORED = "network_restored"
    SCHEDULED_TIME_REACHED = "scheduled_time_reached"
    
    # User interaction
    USER_MESSAGE = "user_message"
    USER_COMMAND = "user_command"
    
    # Approval/Operation events
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    OPERATION_EXECUTED = "operation_executed"