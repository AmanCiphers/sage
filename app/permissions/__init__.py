"""SAGE Permission System - Lean v2

Three-layer defense:
1. Bypass (YOLO, off mode, context auto-approve)
2. Allowlist (session + permanent)
3. Danger detection + Smart guardian + Human approval
"""

from .allowlist import AllowlistManager
from .config import PermissionConfig, get_permission_config, reset_permission_config
from .context import (
    ApprovalContext,
    get_context_mode,
    get_current_context,
    is_cron_context,
    is_gateway_context,
    is_interactive_cli,
    is_single_query_context,
    is_unattended_context,
)
from .detection import detect_dangerous, detect_shell_metacharacters, is_read_only_command
from .engine import PermissionEngine
from .gate import ApprovalGate, GateSpec, get_gate_spec
from .models import (
    ApprovalPrompt,
    ApprovalResponse,
    PermissionDecision,
    PermissionRequest,
    PermissionResult,
    SmartVerdict,
    ToolClassification,
)

__all__ = [  # noqa: RUF022
    # Core
    "PermissionEngine",
    "PermissionDecision",
    "PermissionRequest",
    "PermissionResult",
    "SmartVerdict",
    "ToolClassification",
    "ApprovalPrompt",
    "ApprovalResponse",

    # Config
    "PermissionConfig",
    "get_permission_config",
    "reset_permission_config",

    # Allowlist
    "AllowlistManager",

    # Context
    "ApprovalContext",
    "get_current_context",
    "is_interactive_cli",
    "is_gateway_context",
    "is_single_query_context",
    "is_cron_context",
    "is_unattended_context",
    "get_context_mode",

    # Detection
    "detect_dangerous",
    "detect_shell_metacharacters",
    "is_read_only_command",

    # Gate
    "ApprovalGate",
    "GateSpec",
    "get_gate_spec",
]

# Deprecated (for backward compat)
from .policy import PermissionPolicy  # noqa: F401

__all__ = sorted(__all__ + ["PermissionPolicy"])  # noqa: RUF005
