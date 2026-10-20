from typing import Any

from pydantic import BaseModel, Field


class ModelCapabilities(BaseModel):
    tool_calling: bool = False
    vision: bool = False
    reasoning: bool = False
    streaming: bool = False


class ModelMetadata(BaseModel):
    provider: str
    model_id: str
    context_window: int
    capabilities: ModelCapabilities = Field(
        default_factory=ModelCapabilities
    )
    latency_class: str = "unknown"
    cost_class: str = "unknown"
    priority: int = 0
    available: bool = True


class InferenceRequest(BaseModel):
    messages: list[dict[str, Any]]
    model: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    tools: list[dict[str, Any]] | None = None


class InferenceResponse(BaseModel):
    content: str = ""
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    tool_calls: list[dict[str, Any]] | None = None


class StreamDelta(BaseModel):
    """One piece of a streamed response.

    `content` is yielded as it arrives (live feedback). `tool_calls` is
    yielded only once, fully accumulated, at the end of the stream.
    `input_tokens`/`output_tokens`/`latency_ms` are present only on the
    final delta (when the provider sends the usage chunk).
    """

    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None


class ModelRequirements(BaseModel):
    tool_calling: bool = False
    vision: bool = False
    reasoning: bool = False
    streaming: bool = False


