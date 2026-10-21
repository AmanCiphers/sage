"""Context detection for SAGE permission system.

Determines the current execution context to apply appropriate approval modes.
"""

import os
from dataclasses import dataclass

from app.permissions.config import get_permission_config


@dataclass(frozen=True)
class ApprovalContext:
    """Detected execution context with its approval mode."""
    name: str                    # "cli" | "gateway" | "single_query" | "cron" | "unattended"
    mode: str                    # "approve" | "deny" | "off"
    has_human: bool              # Whether a human can respond to prompts
    description: str             # Human-readable context description


def get_current_context() -> ApprovalContext:
    """Detect the current execution context and its approval mode."""
    config = get_permission_config()

    # Check for global off mode first
    if config.is_off_mode:
        return ApprovalContext(
            name="off",
            mode="approve",
            has_human=False,
            description="approvals.mode=off — all actions auto-approved"
        )

    # Check for global YOLO
    if config.yolo_active:
        return ApprovalContext(
            name="yolo",
            mode="approve",
            has_human=False,
            description="YOLO mode active — all actions auto-approved"
        )

    # Detect specific contexts (order matters - most specific first)

    # Single query mode (-q flag)
    if os.getenv("SAGE_SINGLE_QUERY", "").lower() in ("1", "true", "yes"):
        mode = config.get_context_mode("single_query")
        return ApprovalContext(
            name="single_query",
            mode=mode,
            has_human=False,
            description=f"Single-query mode (SAGE_SINGLE_QUERY) — mode: {mode}"
        )

    # Cron job context
    if os.getenv("SAGE_CRON", "").lower() in ("1", "true", "yes"):
        mode = config.get_context_mode("cron")
        return ApprovalContext(
            name="cron",
            mode=mode,
            has_human=False,
            description=f"Cron job (SAGE_CRON) — mode: {mode}"
        )

    # Gateway context (Web UI, Discord, etc.)
    if os.getenv("SAGE_GATEWAY", "").lower() in ("1", "true", "yes"):
        return ApprovalContext(
            name="gateway",
            mode=config.mode,
            has_human=True,  # Gateway can notify user
            description=f"Gateway context (SAGE_GATEWAY) — mode: {config.mode}"
        )

    # Unattended platform (systemd, docker, etc.)
    if os.getenv("SAGE_UNATTENDED", "").lower() in ("1", "true", "yes"):
        mode = config.get_context_mode("unattended")
        return ApprovalContext(
            name="unattended",
            mode=mode,
            has_human=False,
            description=f"Unattended platform (SAGE_UNATTENDED) — mode: {mode}"
        )

    # Default: interactive CLI
    return ApprovalContext(
        name="cli",
        mode=config.mode,
        has_human=True,
        description=f"Interactive CLI — mode: {config.mode}"
    )


def is_interactive_cli() -> bool:
    """Check if running in interactive CLI mode."""
    ctx = get_current_context()
    return ctx.name == "cli" and ctx.has_human


def is_gateway_context() -> bool:
    """Check if running in gateway context."""
    return get_current_context().name == "gateway"


def is_single_query_context() -> bool:
    return get_current_context().name == "single_query"


def is_cron_context() -> bool:
    return get_current_context().name == "cron"


def is_unattended_context() -> bool:
    ctx = get_current_context()
    return ctx.name in ("cron", "unattended", "single_query") and not ctx.has_human


def get_context_mode(context_name: str) -> str:
    """Get the approval mode for a named context."""
    config = get_permission_config()
    return config.get_context_mode(context_name)
