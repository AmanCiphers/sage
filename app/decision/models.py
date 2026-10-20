from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DecisionAction(str, Enum):
    RESPOND = "respond"
    USE_TOOL = "use_tool"


class Decision(BaseModel):
    action: DecisionAction
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    response: str | None = None
