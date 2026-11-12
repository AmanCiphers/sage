"""Permission engine for SAGE v2.

Layered, deterministic decision flow:

1. Hardline deny (catastrophic commands — never bypassable)
2. Explicit command risk (dangerous commands ask; ordinary commands run)
3. Sensitive file writes (protected files outside a trusted workspace ask)
4. Bypass (YOLO, off mode, approving context)
5. Allowlist (session + permanent)
6. Capability policies
7. Smart guardian (refines ambiguous commands only)
8. Human approval gate
"""

from __future__ import annotations

from app.permissions.allowlist import AllowlistManager
from app.permissions.config import get_permission_config
from app.permissions.context import get_current_context
from app.permissions.models import (
    PermissionDecision,
    PermissionRequest,
    PermissionResult,
    SmartVerdict,
)
from app.permissions.risk import (
    RISK_ALLOW,
    RISK_DENY,
    RISK_REVIEW,
    classify_command,
    classify_file_write,
    pattern_key_for,
)
from app.permissions.smart import GuardianAssessment, SmartGuardian, get_smart_guardian
from app.tools.models import ToolCapability, ToolDefinition
from app.tools.workspace import current_employee_key

# Risk order for capabilities (lower = lower risk, processed first)
CAPABILITY_RISK_ORDER = {
    ToolCapability.READ: 10,
    ToolCapability.WRITE: 20,
    ToolCapability.NETWORK: 30,
    ToolCapability.EXECUTE: 40,
    ToolCapability.PROCESS: 40,
    ToolCapability.STORAGE: 45,
    ToolCapability.NETWORK_ADMIN: 45,
    ToolCapability.SYSTEM: 50,
    ToolCapability.DESTRUCTIVE: 100,
}

FILE_WRITE_TOOLS = {"write_file", "patch"}
AGENT_TOOLS = {"run_opencode", "run_hermes"}


def _normalize_capability(cap) -> ToolCapability:
    if isinstance(cap, ToolCapability):
        return cap
    try:
        return ToolCapability(cap)
    except ValueError:
        return cap


class PermissionEngine:
    """Deterministic permission engine with layered defense."""

    def __init__(
        self,
        allowlist: AllowlistManager | None = None,
        smart_guardian: SmartGuardian | None = None,
        gateway_callback=None,
    ) -> None:
        self.config = get_permission_config()
        self.allowlist = allowlist or AllowlistManager()
        self.smart_guardian = smart_guardian or get_smart_guardian()
        self.gateway_callback = gateway_callback

    def _get_capability_policy(self, cap: ToolCapability) -> str:
        """Get the approval policy for a capability.

        Deterministic defaults — never dependent on optional config fields.
        """
        if cap == ToolCapability.READ:
            return "auto_allow"
        if cap == ToolCapability.WRITE:
            return "auto_allow"
        if cap == ToolCapability.DESTRUCTIVE:
            return "explicit"
        if cap in (
            ToolCapability.EXECUTE,
            ToolCapability.NETWORK,
            ToolCapability.NETWORK_ADMIN,
            ToolCapability.SYSTEM,
            ToolCapability.STORAGE,
            ToolCapability.PROCESS,
        ):
            return "session_allow"
        return "explicit"

    def check(self, tool: ToolDefinition, arguments: dict | None = None) -> PermissionDecision:
        """Synchronous check - ambiguous commands stay at approval."""
        return self._check_internal(tool, arguments or {}).decision

    async def check_async(
        self, tool: ToolDefinition, arguments: dict | None = None
    ) -> PermissionDecision:
        """Async check - may consult the smart guardian for ambiguous commands."""
        result = await self._check_async_internal(tool, arguments or {})
        return result.decision

    def _check_internal(self, tool: ToolDefinition, arguments: dict) -> PermissionResult:
        request = PermissionRequest(
            tool_name=tool.name,
            classification=tool.classification,
            destructive=tool.destructive,
            arguments=arguments,
        )

        # Agent tools invoked by an employee run in an isolated, budgeted
        # workspace — no approval required.
        if tool.name in AGENT_TOOLS and current_employee_key() is not None:
            return PermissionResult(decision=PermissionDecision.ALLOWED)

        # 1. Dangerous / catastrophic shell commands come first so no
        #    bypass mode can ever auto-approve them.
        if tool.name == "run_command":
            return self._check_run_command(request, arguments)

        # 2. File writes: ordinary files run silently, protected files
        #    outside a trusted workspace require approval.
        if tool.name in FILE_WRITE_TOOLS:
            return self._check_file_write(request, arguments)

        # 3. Bypass checks (YOLO, off mode, approving context, deny context)
        bypass_result = self._check_bypass(request)
        if bypass_result.decision != PermissionDecision.APPROVAL_REQUIRED:
            return bypass_result

        # 4. Allowlist
        key = pattern_key_for(tool.name, arguments)
        if self.allowlist.is_allowed(key):
            return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=key)

        # 5. Capability policies, lowest risk first
        invocation_caps = (
            tool.resolve_capabilities(arguments)
            if tool.resolve_capabilities
            else tool.capabilities
        )
        capabilities = [_normalize_capability(cap) for cap in invocation_caps]
        capabilities.sort(key=lambda c: CAPABILITY_RISK_ORDER.get(c, 999))

        for cap in capabilities:
            policy = self._get_capability_policy(cap)
            if policy == "auto_allow":
                return PermissionResult(
                    decision=PermissionDecision.ALLOWED, pattern_key=key
                )
            if policy == "session_allow":
                return self._ask_or_allow(key, request, f"{cap.value} requires approval")
            if policy == "explicit":
                return self._create_approval_required(
                    f"{cap.value} requires explicit approval", request
                )
            if policy == "deny":
                return PermissionResult(
                    decision=PermissionDecision.DENIED,
                    pattern_key=key,
                    message=f"{cap.value} is not permitted",
                )

        # 6. Legacy classification fallback (tools without capabilities)
        classification = self._get_classification_value(request.classification)
        if classification == "read_only":
            return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=key)
        if request.destructive:
            return self._ask_or_allow(key, request, f"destructive tool: {request.tool_name}")
        if classification == "external_side_effect":
            return self._ask_or_allow(
                key, request, f"external side effect: {request.tool_name}"
            )
        if classification in {"read_only", "local_mutation", "external_side_effect"}:
            return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=key)

        return PermissionResult(
            decision=PermissionDecision.DENIED,
            pattern_key=key,
            message=f"Unknown tool classification: {request.classification}",
        )

    def _check_run_command(
        self, request: PermissionRequest, arguments: dict
    ) -> PermissionResult:
        command = str(arguments.get("command", ""))
        risk, reason = classify_command(command)
        key = pattern_key_for("run_command", arguments)

        if risk == RISK_DENY:
            return PermissionResult(
                decision=PermissionDecision.DENIED,
                pattern_key=key,
                message=f"Hard-blocked command: {reason}",
            )

        if risk == RISK_ALLOW:
            return PermissionResult(
                decision=PermissionDecision.ALLOWED, pattern_key=key
            )

        if self.allowlist.is_allowed(key):
            return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=key)

        result = self._create_approval_required(reason, request)
        result.pattern_key = key
        # Only ambiguous commands may be auto-resolved by the smart guardian.
        result.requires_smart_check = risk == RISK_REVIEW
        return result

    def _check_file_write(
        self, request: PermissionRequest, arguments: dict
    ) -> PermissionResult:
        path = str(arguments.get("path") or arguments.get("file") or "")
        risk, reason = classify_file_write(path)

        if risk == RISK_ALLOW:
            return PermissionResult(decision=PermissionDecision.ALLOWED)

        key = pattern_key_for(request.tool_name, arguments)
        if self.allowlist.is_allowed(key):
            return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=key)

        result = self._create_approval_required(reason, request)
        result.pattern_key = key
        return result

    async def _check_async_internal(
        self, tool: ToolDefinition, arguments: dict
    ) -> PermissionResult:
        """Deterministic check, then smart guardian for ambiguous commands."""
        result = self._check_internal(tool, arguments)

        if (
            result.decision == PermissionDecision.APPROVAL_REQUIRED
            and result.requires_smart_check
            and self.smart_guardian
        ):
            verdict = await self._run_smart_guardian(arguments, result)
            if verdict.verdict == SmartVerdict.APPROVE.value:
                return PermissionResult(
                    decision=PermissionDecision.ALLOWED,
                    pattern_key=result.pattern_key,
                    smart_verdict=SmartVerdict.APPROVE.value,
                )
            if verdict.verdict == SmartVerdict.DENY.value:
                return PermissionResult(
                    decision=PermissionDecision.DENIED,
                    pattern_key=result.pattern_key,
                    description=result.description,
                    message=f"Smart guardian denied: {verdict.reasoning}",
                    smart_verdict=SmartVerdict.DENY.value,
                )

        return result

    def _check_bypass(self, request: PermissionRequest) -> PermissionResult:
        """Check for bypass conditions."""
        config = get_permission_config()

        # Global YOLO: allow everything EXCEPT genuinely destructive tools,
        # which still require explicit approval.
        if config.yolo_active:
            if not request.destructive:
                return PermissionResult(decision=PermissionDecision.ALLOWED)
            return PermissionResult(decision=PermissionDecision.APPROVAL_REQUIRED)

        # Off mode
        if config.is_off_mode:
            return PermissionResult(decision=PermissionDecision.ALLOWED)

        # Context-based auto-approve
        context = get_current_context()
        if context.mode == "approve":
            return PermissionResult(decision=PermissionDecision.ALLOWED)

        # Context-based deny
        if context.mode == "deny":
            return PermissionResult(
                decision=PermissionDecision.DENIED,
                message=f"Blocked by {context.name} context (mode: deny)",
            )

        return PermissionResult(decision=PermissionDecision.APPROVAL_REQUIRED)

    def _ask_or_allow(
        self,
        key: str,
        request: PermissionRequest,
        description: str,
    ) -> PermissionResult:
        if self.allowlist.is_allowed(key):
            return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=key)
        result = self._create_approval_required(description, request)
        result.pattern_key = key
        return result

    def _get_classification_value(self, classification) -> str:
        """Get classification value handling both enum and string."""
        if hasattr(classification, "value"):
            return classification.value
        return str(classification)

    def _create_approval_required(
        self, description: str, request: PermissionRequest
    ) -> PermissionResult:
        return PermissionResult(
            decision=PermissionDecision.APPROVAL_REQUIRED,
            description=description,
            requires_smart_check=False,
        )

    async def _run_smart_guardian(
        self, arguments: dict, result: PermissionResult
    ) -> GuardianAssessment:
        """Run the smart guardian LLM check for an ambiguous command."""
        if not self.smart_guardian:
            return GuardianAssessment(verdict=SmartVerdict.ESCALATE.value)

        try:
            return await self.smart_guardian.assess(
                command=arguments.get("command", "") if arguments else "",
                description=result.description or "",
                pattern_keys=[result.pattern_key] if result.pattern_key else [],
            )
        except Exception:
            # Guardian failure = escalate to human
            return GuardianAssessment(
                verdict=SmartVerdict.ESCALATE.value,
                reasoning="Smart guardian error",
            )

    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is triggered (delegates to smart guardian)."""
        if self.smart_guardian:
            return self.smart_guardian._is_circuit_breaker_tripped()
        return False

    def reset_circuit_breaker(self) -> None:
        """Reset circuit breaker (call on successful approval)."""
        if self.smart_guardian:
            self.smart_guardian.reset_circuit_breaker()

    # Public API for allowlist management
    def allow_session(self, pattern_key: str) -> None:
        self.allowlist.allow_session(pattern_key)

    def allow_permanent(self, pattern_key: str) -> None:
        self.allowlist.allow_permanent(pattern_key)
        self.reset_circuit_breaker()

    def revoke(self, pattern_key: str) -> bool:
        return self.allowlist.revoke(pattern_key)

    def list_allowed(self) -> list[str]:
        return self.allowlist.list_all()
