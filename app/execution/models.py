from typing import Any

from pydantic import BaseModel


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}


class ExecutionResult(BaseModel):
    success: bool
    tool_name: str
    output: Any = None
    error: str | None = None
