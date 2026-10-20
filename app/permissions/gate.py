"""Human approval gate for SAGE permission system.

Shared approval flow for CLI and gateway (Web UI).
"""

import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

from app.permissions.config import get_permission_config
from app.permissions.context import get_current_context, ApprovalContext
from app.permissions.models import (
    PermissionDecision,
    PermissionResult,
    ApprovalPrompt,
    ApprovalResponse,
)

logger = logging.getLogger(__name__)


# Default prompts
CLI_PROMPT_TEMPLATE = """
⚠️  {description}

Target: {display_target}

Allow this action?
  [y] Yes, once
  [s] Yes, for this session
  [a] Yes, permanently (add to allowlist)
  [n] No, deny
  [q] Quit

Choice: """


GATEWAY_TEMPLATE = """
{description}

Target: {display_target}

Options:
- allow_once
- allow_session
- allow_permanent
- deny

Waiting for gateway response..."""


@dataclass
class GateSpec:
    """Configuration for a specific approval gate."""
    noun: str = "action"
    allow_permanent: bool = True
    allow_session: bool = True
    smart_denied_override: bool = False  # Smart DENY overrides permanent to session-only


GATE_SPECS = {
    "command": GateSpec(noun="command", allow_permanent=True, allow_session=True),
    "code": GateSpec(noun="code", allow_permanent=True, allow_session=True, smart_denied_override=True),
    "file_write": GateSpec(noun="file write", allow_permanent=True, allow_session=True),
    "file_patch": GateSpec(noun="file patch", allow_permanent=True, allow_session=True),
    "action": GateSpec(noun="action", allow_permanent=False, allow_session=True),
    "default": GateSpec(noun="action", allow_permanent=True, allow_session=True),
}


STOP_MESSAGE = (
    " The user has NOT consented to this action. Do NOT retry this action, do "
    "NOT rephrase it, and do NOT attempt the same outcome via a different "
    "path. Stop the current workflow and wait for the user to respond before "
    "taking any further destructive or irreversible action."
)


class ApprovalGate:
    """Human approval gate - shared by CLI and gateway."""
    
    def __init__(
        self,
        gateway_callback: Callable[[ApprovalPrompt], "Awaitable[ApprovalResponse]"] | None = None,
        cli_callback: Callable[[ApprovalPrompt], "Awaitable[ApprovalResponse]"] | None = None,
    ):
        self.config = get_permission_config()
        self.gateway_callback = gateway_callback
        self.cli_callback = cli_callback
    
    async def run_gate(
        self,
        pattern_key: str,
        description: str,
        arguments: dict,
        tool_name: str,
        spec: "GateSpec | None" = None,
        display_target: str = "",
        smart_denied: bool = False,
    ) -> PermissionResult:
        """Run the approval gate for a flagged action."""
        
        # Build prompt data
        prompt = ApprovalPrompt(
            pattern_key=pattern_key,
            description=description,
            arguments=arguments,
            tool_name=tool_name,
            display_target=display_target or self._get_display_target(arguments),
            allow_permanent=spec.allow_permanent if spec else True,
            allow_session=spec.allow_session if spec else True,
            smart_denied=smart_denied,
        )
        
        # If smart denied, override permanent to session-only
        if smart_denied and spec and spec.smart_denied_override:
            prompt.allow_permanent = False
        
        context = get_current_context()
        
        # Try gateway first (Web UI)
        if self.gateway_callback and context.has_human:
            return await self._run_gateway(prompt, spec, context)
        
        # Try CLI
        if self.cli_callback and context.has_human:
            return await self._run_cli(prompt, spec)
        
        # No human available - use context config
        return await self._run_unattended(prompt, spec, context)
    
    async def _run_gateway(
        self,
        prompt: "ApprovalPrompt",
        spec: "GateSpec | None",
        context: ApprovalContext,
    ) -> PermissionResult:
        """Run approval via gateway callback (Web UI)."""
        try:
            if not self.gateway_callback:
                return self._pending_result(prompt, spec)
            
            response = await self.gateway_callback(prompt)
            return self._process_response(response, prompt, spec)
        except Exception as e:
            logger.error(f"Gateway callback failed: {e}")
            return self._pending_result(prompt, spec)
    
    async def _run_cli(
        self,
        prompt: "ApprovalPrompt",
        spec: "GateSpec | None",
    ) -> PermissionResult:
        """Run approval via CLI callback."""
        try:
            if not self.cli_callback:
                return self._pending_result(prompt, spec)
            
            response = await self.cli_callback(prompt)
            return self._process_response(response, prompt, spec)
        except Exception as e:
            logger.error(f"CLI callback failed: {e}")
            return self._pending_result(prompt, spec)
    
    async def _run_unattended(
        self,
        prompt: "ApprovalPrompt",
        spec: "GateSpec | None",
        context: ApprovalContext,
    ) -> PermissionResult:
        """Handle unattended contexts (cron, single-query, etc.)."""
        config = get_permission_config()
        
        if context.mode == "approve":
            # Context configured to auto-approve
            return PermissionResult(
                decision=PermissionDecision.ALLOWED,
                pattern_key=prompt.pattern_key,
                smart_verdict="auto_approve" if prompt.smart_denied else None,
            )
        
        # Deny or fallback to pending
        if context.mode == "deny":
            return PermissionResult(
                decision=PermissionDecision.DENIED,
                pattern_key=prompt.pattern_key,
                description=prompt.description,
                message=f"Blocked by {context.name} context (mode: deny): {prompt.description}",
            )
        
        # Fallback: create pending approval (for manual /approve later)
        return self._pending_result(prompt, spec)
    
    def _process_response(
        self,
        response: "ApprovalResponse",
        prompt: "ApprovalPrompt",
        spec: "GateSpec | None",
    ) -> PermissionResult:
        """Process user/gateway response into PermissionResult."""
        
        if response.choice == "allow":
            return PermissionResult(
                decision=PermissionDecision.ALLOWED,
                pattern_key=prompt.pattern_key,
                smart_verdict="user_approved",
            )
        
        if response.choice == "session":
            return PermissionResult(
                decision=PermissionDecision.ALLOWED,
                pattern_key=prompt.pattern_key,
                message="Approved for this session",
            )
        
        if response.choice == "permanent" and prompt.allow_permanent:
            return PermissionResult(
                decision=PermissionDecision.ALLOWED,
                pattern_key=prompt.pattern_key,
                message="Approved permanently (added to allowlist)",
            )
        
        # Deny or timeout
        reason = response.reason or "denied by user"
        if response.choice == "timeout":
            return PermissionResult(
                decision=PermissionDecision.DENIED,
                pattern_key=prompt.pattern_key,
                description=prompt.description,
                message=f"Approval timed out. {STOP_MESSAGE}",
            )
        
        if response.choice == "cancelled":
            return PermissionResult(
                decision=PermissionDecision.DENIED,
                pattern_key=prompt.pattern_key,
                description=prompt.description,
                message=f"Approval cancelled: {reason}. {STOP_MESSAGE}",
            )
        
        # Deny
        return PermissionResult(
            decision=PermissionDecision.DENIED,
            pattern_key=prompt.pattern_key,
            description=prompt.description,
            message=f"BLOCKED: User denied {prompt.noun} ({reason}).{STOP_MESSAGE}",
        )
    
    def _pending_result(self, prompt: "ApprovalPrompt", spec: "GateSpec | None") -> PermissionResult:
        """Create a pending approval result."""
        body = f"**{prompt.description}**\n\n**Target:** `{prompt.display_target}`"
        
        return PermissionResult(
            decision=PermissionDecision.APPROVAL_REQUIRED,
            pattern_key=prompt.pattern_key,
            description=prompt.description,
            message=(
                f"⚠️ {prompt.description}. Asking for approval.\n\n"
                f"{body}\n\n"
                f"STOP: do NOT re-run, rephrase, or re-issue this {prompt.noun} — "
                f"each variant sends another approval request. Wait for decision."
            ),
        )
    
    def _get_display_target(self, arguments: dict) -> str:
        """Extract display target from arguments."""
        if not arguments:
            return ""
        
        for key in ["command", "path", "file", "target", "resource"]:
            if key in arguments:
                val = arguments[key]
                if isinstance(val, str):
                    return val[:200]
        return str(arguments)[:200]


def get_gate_spec(tool_name: str) -> "GateSpec":
    """Get gate spec for a tool."""
    return GATE_SPECS.get(tool_name, GATE_SPECS["default"])


STOP_MESSAGE = (
    " The user has NOT consented to this action. Do NOT retry this action, do "
    "NOT rephrase it, and do NOT attempt the same outcome via a different "
    "path. Stop the current workflow and wait for the user to respond before "
    "taking any further destructive or irreversible action."
)


@dataclass
class GateSpec:
    """Configuration for a specific approval gate."""
    noun: str = "action"
    allow_permanent: bool = True
    allow_session: bool = True
    smart_denied_override: bool = False  # Smart DENY overrides permanent to session-only


GATE_SPECS = {
    "command": GateSpec(noun="command", allow_permanent=True, allow_session=True),
    "code": GateSpec(noun="code", allow_permanent=True, allow_session=True, smart_denied_override=True),
    "file_write": GateSpec(noun="file write", allow_permanent=True, allow_session=True),
    "file_patch": GateSpec(noun="file patch", allow_permanent=True, allow_session=True),
    "action": GateSpec(noun="action", allow_permanent=False, allow_session=True),
    "default": GateSpec(noun="action", allow_permanent=True, allow_session=True),
}