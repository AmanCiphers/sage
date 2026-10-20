"""Permission models for SAGE v2."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class PermissionDecision(str, Enum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


class SmartVerdict(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"


class ToolClassification(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


@dataclass
class PermissionRequest:
    """Describes an attempted tool execution."""
    tool_name: str
    classification: ToolClassification
    destructive: bool = False
    arguments: Optional[dict] = None
    pattern_key: Optional[str] = None


@dataclass
class PermissionResult:
    """Result of a permission check."""
    decision: PermissionDecision
    pattern_key: Optional[str] = None
    description: Optional[str] = None
    message: Optional[str] = None
    requires_smart_check: bool = False
    smart_verdict: Optional[str] = None


@dataclass
class SmartVerdict:
    """Smart guardian LLM verdict."""
    verdict: SmartVerdict
    reasoning: str = ""
    confidence: float = 1.0


@dataclass
class ApprovalPrompt:
    """Data needed to prompt user for approval."""
    pattern_key: str
    description: str
    arguments: dict
    tool_name: str
    display_target: str  # What to show user (command, file path, etc.)
    allow_permanent: bool = True
    allow_session: bool = True
    smart_denied: bool = False


@dataclass
class ApprovalResponse:
    """User's response to an approval prompt."""
    choice: str  # "allow" | "deny" | "session" | "permanent" | "timeout" | "cancelled"
    reason: Optional[str] = None