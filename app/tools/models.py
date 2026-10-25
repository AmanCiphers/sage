from collections.abc import Callable
from enum import Enum
from typing import Any, List

from pydantic import BaseModel, ConfigDict


class ToolCapability(str, Enum):  # noqa: UP042
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    SYSTEM = "system"
    DESTRUCTIVE = "destructive"
    NETWORK_ADMIN = "network_admin"
    STORAGE = "storage"
    PROCESS = "process"


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

    # New capability system
    capabilities: List[ToolCapability] = []
    # Per-invocation capability resolution (optional)
    resolve_capabilities: Callable[..., List[ToolCapability]] | None = None

    # Optional dynamic check: given the parsed arguments, return True when
    # THIS particular invocation needs user approval (e.g. overwriting an
    # existing file). Deterministic — SAGE decides, never the LLM.
    requires_approval: Callable[..., bool] | None = None

    handler: Callable[..., Any] | None = None


class ToolResult(BaseModel):
    success: bool
    output: Any = None
    error: str | None = None
