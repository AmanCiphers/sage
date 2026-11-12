"""AgentBackend interface for external agent execution (OpenCode, Hermes)."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


async def iter_output_lines(stream: asyncio.StreamReader):
    """Yield decoded, newline-stripped lines from a subprocess stream.

    ``async for line in stream`` (and ``readline``) raise once a single line
    exceeds asyncio's 64 KiB reader limit, and the stream cannot be consumed
    past that point. Reading fixed chunks and splitting on newlines has no
    line-length ceiling, so a huge JSON payload or stack trace streams safely.
    """
    buffer = b""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line.decode("utf-8", errors="replace")
    if buffer:
        yield buffer.decode("utf-8", errors="replace")


@dataclass
class AgentTask:
    """A task to be executed by an agent backend."""
    objective: str
    workspace: str
    context_files: list[str] = field(default_factory=list)
    context_summary: str = ""
    model: str | None = None
    timeout_seconds: int = 0  # 0 = unlimited
    session_id: str | None = None  # Resume existing session


@dataclass
class AgentResult:
    """Result of an agent task execution."""
    success: bool
    output: str
    error: str | None = None
    cost_usd: float | None = None
    duration_seconds: float = 0.0
    session_id: str | None = None  # External session ID for resume


@dataclass
class AgentEvent:
    """Streaming event from agent backend."""
    type: str  # "start", "progress", "tool", "complete", "error"
    message: str
    data: dict = field(default_factory=dict)


class AgentBackend(ABC):
    """Abstract base class for agent backends (OpenCode, Hermes, etc.)."""

    name: str  # "opencode", "hermes"

    @abstractmethod
    async def start(self) -> None:
        """Initialize the backend (verify installation, auth, etc.)."""
        ...

    @abstractmethod
    async def run_task(self, task: AgentTask) -> AgentResult:
        """Execute a task and return the result."""
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel any running task."""
        ...

    @abstractmethod
    async def get_session_id(self) -> str | None:
        """Get the current external session ID if any."""
        ...

    @abstractmethod
    async def stream_events(self, task: AgentTask) -> AsyncIterator[AgentEvent]:
        """Stream events during task execution (optional)."""
        ...

    async def __aenter__(self) -> AgentBackend:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.cancel()
