"""Permission models for SAGE v2."""

from dataclasses import dataclass
from enum import Enum


class PermissionDecision(str, Enum):  # noqa: UP042
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


class SmartVerdict(str, Enum):  # noqa: UP042
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"


class ToolClassification(str, Enum):  # noqa: UP042
    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


@dataclass
class PermissionRequest:
    """Describes an attempted tool execution."""
    tool_name: str
    classification: ToolClassification
    destructive: bool = False
    arguments: dict | None = None
    pattern_key: str | None = None


@dataclass
class PermissionResult:
    """Result of a permission check."""
    decision: PermissionDecision
    pattern_key: str | None = None
    description: str | None = None
    message: str | None = None
    requires_smart_check: bool = False
    smart_verdict: str | None = None


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
    reason: str | None = None
