"""Core interfaces for SAGE v2 subsystems.

All interfaces use typing.Protocol for structural subtyping.
Implementations should match the method signatures exactly.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.conversation.models import AssistantResponse, LoopEvent, UserMessage
from app.events.models import Event
from app.tools.models import ToolDefinition, ToolClassification
from app.tools.registry import ToolRegistry
from app.inference.models import InferenceRequest, InferenceResponse, ModelRequirements
from app.execution.models import ExecutionResult, ToolCall
from app.employees.roster import EmployeeSpec
from app.employees.budgets import Budget
from app.employees.employee import EmployeeResult
from app.scheduler.models import ScheduledJob, ProactiveRule
from app.persistence.store import MessageStore, SessionStateStore, TaskStore


@runtime_checkable
class IConversationEngine(Protocol):
    """Main conversation engine interface."""
    
    @abstractmethod
    async def handle(self, message: UserMessage) -> AssistantResponse:
        """Process a user message (non-streaming)."""
        ...
    
    @abstractmethod
    async def handle_stream(
        self,
        message: UserMessage,
        on_delta: Callable[[str], Awaitable[None]],
    ) -> AssistantResponse:
        """Process a user message with streaming deltas."""
        ...
    
    @abstractmethod
    async def cancel(self) -> None:
        """Cancel the current turn mid-generation."""
        ...


@runtime_checkable
class IContextBuilder(Protocol):
    """Builds API message history for each turn."""
    
    @abstractmethod
    async def build(self) -> list[dict]:
        """Build the message history: system + summary + RAG + tail."""
        ...


@runtime_checkable
class IToolRegistry(Protocol):
    """Tool registry interface."""
    
    @abstractmethod
    def register(self, tool: ToolDefinition) -> None:
        """Register a tool definition."""
        ...
    
    @abstractmethod
    def get(self, name: str) -> ToolDefinition:
        """Get a tool by name (with prefix recovery)."""
        ...
    
    @abstractmethod
    def list(self) -> list[ToolDefinition]:
        """List all registered tools."""
        ...
    
    @abstractmethod
    def openai_definitions(self) -> list[dict]:
        """Get tool definitions in OpenAI function-calling format."""
        ...


@runtime_checkable
class IPermissionEngine(Protocol):
    """Deterministic permission engine interface."""
    
    @abstractmethod
    def check(self, tool: ToolDefinition, arguments: dict) -> "PermissionDecision":
        """Synchronous permission check."""
        ...
    
    @abstractmethod
    async def check_async(self, tool: ToolDefinition, arguments: dict) -> "PermissionDecision":
        """Async permission check (with smart guardian)."""
        ...
    
    @abstractmethod
    def allow_session(self, pattern_key: str) -> None:
        """Allow pattern for current session."""
        ...
    
    @abstractmethod
    def allow_permanent(self, pattern_key: str) -> None:
        """Allow pattern permanently."""
        ...
    
    @abstractmethod
    def revoke(self, pattern_key: str) -> bool:
        """Revoke an allowed pattern."""
        ...
    
    @abstractmethod
    def list_allowed(self) -> list[str]:
        """List all allowed patterns."""
        ...


@runtime_checkable
class IWorkerPool(Protocol):
    """Interface for background worker/employee pool."""
    
    @abstractmethod
    async def assign(self, worker: str, objective: str) -> str:
        """Assign an objective to a named worker; returns task_id."""
        ...
    
    @abstractmethod
    async def assign_auto(self, objective: str) -> str:
        """Auto-select best worker and assign objective."""
        ...
    
    @abstractmethod
    async def get_status(self, task_id: str) -> dict | None:
        """Get status of a task."""
        ...
    
    @abstractmethod
    async def await_task(
        self,
        task_id: str,
        timeout_seconds: float = 60.0,
        poll_interval: float = 1.0,
    ) -> dict:
        """Wait for task to reach terminal state."""
        ...
    
    @abstractmethod
    async def cancel_task(self, task_id: str, reason: str = "Cancelled by SAGE") -> None:
        """Cancel a running task."""
        ...
    
    @abstractmethod
    def roster_status(self) -> list[dict]:
        """Get roster snapshot with worker statuses."""
        ...
    
    @abstractmethod
    async def activity(self, worker: str, limit: int = 20) -> list[dict]:
        """Get recent activity for a worker."""
        ...
    
    @abstractmethod
    async def recover(self) -> int:
        """Startup recovery: mark stale tasks as unknown."""
        ...
    
    @abstractmethod
    async def shutdown(self) -> None:
        """Graceful shutdown of all workers."""
        ...


@runtime_checkable
class IAgentBackend(Protocol):
    """External agent backend interface (OpenCode, Hermes)."""
    
    name: str
    
    @abstractmethod
    async def start(self) -> None:
        """Initialize the backend."""
        ...
    
    @abstractmethod
    async def run_task(self, task: "AgentTask") -> "AgentResult":
        """Execute a task and return result."""
        ...
    
    @abstractmethod
    async def cancel(self) -> None:
        """Cancel any running task."""
        ...
    
    @abstractmethod
    async def get_session_id(self) -> str | None:
        """Get current external session ID."""
        ...
    
    @abstractmethod
    async def stream_events(self, task: "AgentTask"):
        """Stream events during execution (async iterator of AgentEvent)."""
        ...


@dataclass
class AgentTask:
    """Task for agent backend execution."""
    objective: str
    workspace: str
    context_files: list[str] = None
    context_summary: str = ""
    model: str | None = None
    timeout_seconds: int = 0
    session_id: str | None = None


@dataclass
class AgentResult:
    """Result from agent backend execution."""
    success: bool
    output: str
    error: str | None = None
    cost_usd: float | None = None
    duration_seconds: float = 0.0
    session_id: str | None = None


@dataclass
class AgentEvent:
    """Streaming event from agent backend."""
    type: str  # "start", "progress", "tool", "complete", "error"
    message: str
    data: dict = None


@runtime_checkable
class IMemoryStore(Protocol):
    """Long-term memory store interface."""
    
    @abstractmethod
    def load(self) -> str:
        """Load entire memory as markdown."""
        ...
    
    @abstractmethod
    def entries(self) -> list[str]:
        """Get list of memory entries."""
        ...
    
    @abstractmethod
    def add(self, text: str) -> str:
        """Add a fact. Returns status message."""
        ...
    
    @abstractmethod
    def remove(self, text: str) -> str:
        """Remove matching facts. Returns status message."""
        ...
    
    @abstractmethod
    def clear(self) -> str:
        """Clear all memory. Returns status message."""
        ...
    
    @abstractmethod
    def status(self) -> dict:
        """Get memory status: used, max, free, %, entries with sizes."""
        ...


@runtime_checkable
class IScheduler(Protocol):
    """Scheduler for cron-like and one-time jobs."""
    
    @abstractmethod
    async def start(self) -> None:
        """Start the scheduler loop."""
        ...
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the scheduler loop."""
        ...
    
    @abstractmethod
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
        """Create and schedule a new job. Returns job_id."""
        ...
    
    @abstractmethod
    async def remove_job(self, job_id: str) -> bool:
        """Remove a job."""
        ...
    
    @abstractmethod
    async def enable_job(self, job_id: str, enabled: bool = True) -> bool:
        """Enable or disable a job."""
        ...
    
    @abstractmethod
    async def list_jobs(self) -> list[ScheduledJob]:
        """List all jobs."""
        ...
    
    @abstractmethod
    async def get_job(self, job_id: str) -> ScheduledJob | None:
        """Get a job by ID."""
        ...


@runtime_checkable
class IProactiveEngine(Protocol):
    """Proactive behavior engine."""
    
    @abstractmethod
    async def initialize(self) -> None:
        """Load rules into cache."""
        ...
    
    @abstractmethod
    async def handle_event(self, event: Event) -> None:
        """Process an incoming event against proactive rules."""
        ...
    
    @abstractmethod
    async def add_rule(
        self,
        name: str,
        description: str,
        trigger_event_type: str,
        condition: str,
        action_event_type: str,
        action_payload_template: dict[str, Any] | None = None,
        cooldown_seconds: int = 300,
    ) -> str:
        """Create a new proactive rule. Returns rule_id."""
        ...
    
    @abstractmethod
    async def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule."""
        ...
    
    @abstractmethod
    async def enable_rule(self, rule_id: str, enabled: bool = True) -> bool:
        """Enable or disable a rule."""
        ...
    
    @abstractmethod
    async def list_rules(self) -> list[ProactiveRule]:
        """List all rules."""
        ...
    
    @abstractmethod
    async def get_rule(self, rule_id: str) -> ProactiveRule | None:
        """Get a rule by ID."""
        ...


@runtime_checkable
class IEventBus(Protocol):
    """Event bus for pub/sub with persistence."""
    
    @abstractmethod
    def subscribe(self, event_type: str, handler: Callable[[Event], Awaitable[None]]) -> None:
        """Subscribe to a specific event type."""
        ...
    
    @abstractmethod
    def subscribe_all(self, handler: Callable[[Event], Awaitable[None]]) -> None:
        """Subscribe to all events (wildcard)."""
        ...
    
    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers with deduplication."""
        ...
    
    @abstractmethod
    async def publish_and_wait(self, event: Event, timeout: float = 30.0) -> list[Any]:
        """Publish and wait for all handlers to complete."""
        ...
    
    @abstractmethod
    def next_sequence(self, source: str) -> int:
        """Get next sequence number for a source."""
        ...
    
    @abstractmethod
    def create_event(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> Event:
        """Create an event with auto-assigned sequence number."""
        ...


@runtime_checkable
class IInferenceManager(Protocol):
    """Model inference manager with routing and fallback."""
    
    @abstractmethod
    async def generate(
        self,
        request: InferenceRequest,
        requirements: ModelRequirements | None = None,
    ) -> InferenceResponse:
        """Generate a non-streaming response."""
        ...
    
    @abstractmethod
    async def stream(
        self,
        request: InferenceRequest,
        requirements: ModelRequirements | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> InferenceResponse:
        """Stream a response with delta callbacks."""
        ...


@runtime_checkable
class IVerification(Protocol):
    """Outcome verification interface."""
    
    @abstractmethod
    async def verify(
        self,
        tool_name: str,
        arguments: dict,
        result: ExecutionResult,
    ) -> tuple[str, str | None]:
        """Verify a tool execution result.
        
        Returns (verification_status, verification_detail).
        Status: "passed", "failed", "unverified"
        """
        ...