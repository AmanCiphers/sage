from typing import Any, Literal

from pydantic import BaseModel


class UserMessage(BaseModel):
    content: str


class AssistantResponse(BaseModel):
    content: str

    # Set when the turn ended with a tool awaiting user consent.
    # The caller should prompt the user and call approve/deny.
    pending_approval: dict[str, Any] | None = None

    # Token usage and latency for this turn (accumulated across iterations).
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    model: str | None = None


class LoopEvent(BaseModel):
    """Observable turn activity for the UI layer (CLI/Web)."""

    type: Literal["llm_start", "compaction", "tool_start", "tool_end", "approval"]
    iteration: int | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    duration_ms: float | None = None
    success: bool | None = None
    error: str | None = None
    approval_id: str | None = None
    resource: str | None = None
