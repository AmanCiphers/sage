from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolClassification(str, Enum):  # noqa: UP042
    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    classification: ToolClassification
    input_schema: type[BaseModel] | None = None
    destructive: bool = False
    employee_eligible: bool = True

    # Optional dynamic check: given the parsed arguments, return True when
    # THIS particular invocation needs user approval (e.g. overwriting an
    # existing file). Deterministic — SAGE decides, never the LLM.
    requires_approval: Callable[..., bool] | None = None

    handler: Callable[..., Any] | None = None


class ToolResult(BaseModel):
    success: bool
    output: Any = None
    error: str | None = None
