"""Permission engine for SAGE v2.

Three-layer defense:
1. Bypass checks (YOLO, off mode)
2. Allowlist (session + permanent)
3. Danger detection + smart guardian + human approval
"""


from app.permissions.allowlist import AllowlistManager
from app.permissions.config import get_permission_config
from app.permissions.context import get_current_context
from app.permissions.detection import detect_dangerous, is_read_only_command
from app.permissions.models import (
    PermissionDecision,
    PermissionRequest,
    PermissionResult,
    SmartVerdict,
)
from app.permissions.smart import SmartGuardian, get_smart_guardian
from app.tools.models import ToolDefinition
from app.tools.workspace import current_employee_key


class PermissionEngine:
    """
    Deterministic permission engine with layered defense.
    
    Layers:
    1. Bypass (YOLO, off mode, context auto-approve)
    2. Allowlist (session + permanent)
    3. Static danger detection
    4. Smart guardian (optional, async)
    5. Human approval gate
    """  # noqa: W293

    def __init__(
        self,
        allowlist: AllowlistManager | None = None,
        smart_guardian: SmartGuardian | None = None,
        gateway_callback=None,  # For Web UI approval
    ) -> None:
        self.config = get_permission_config()
        self.allowlist = allowlist or AllowlistManager()
        self.smart_guardian = smart_guardian or get_smart_guardian()
        self.gateway_callback = gateway_callback

        # Circuit breaker for smart guardian denials (delegated to smart guardian)

    def check(self, tool: ToolDefinition, arguments: dict | None = None) -> PermissionDecision:
        """Synchronous check - returns decision or APPROVAL_REQUIRED."""
        result = self._check_internal(tool, arguments or {})
        return result.decision

    async def check_async(self, tool: ToolDefinition, arguments: dict | None = None) -> PermissionDecision:  # noqa: E501
        """Async check - can run smart guardian."""
        result = await self._check_async_internal(tool, arguments or {})
        return result.decision

    def _check_internal(self, tool: ToolDefinition, arguments: dict) -> PermissionResult:
        """Internal synchronous check."""
        # 1. Build request
        request = PermissionRequest(
            tool_name=tool.name,
            classification=tool.classification,
            destructive=tool.destructive,
            arguments=arguments,
        )

        # 2. Bypass checks
        bypass_result = self._check_bypass(request)
        if bypass_result.decision == PermissionDecision.ALLOWED:
            return bypass_result

        # 3. Allowlist check
        pattern_key = self._get_pattern_key(request)
        if self.allowlist.is_allowed(pattern_key):
            return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=pattern_key)

        # 4. Read-only fast path
        if self._is_read_only(request):
            return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=pattern_key)

        # 5. Danger detection
        danger_result = self._check_danger(request)
        if danger_result:
            return self._create_approval_required(danger_result, request)

        # 6. Destructive tool
        if request.destructive:
            return self._create_approval_required(
                (True, request.tool_name, f"destructive tool: {request.tool_name}"),
                request
            )

        # 7. External side effect
        if self._get_classification_value(request.classification) == "external_side_effect":
            return self._create_approval_required(
                (True, request.tool_name, f"external side effect: {request.tool_name}"),
                request
            )

        # Unknown classification - fail closed
        known_classifications = {"read_only", "local_mutation", "external_side_effect"}
        if self._get_classification_value(request.classification) not in known_classifications:
            return PermissionResult(
                decision=PermissionDecision.DENIED,
                pattern_key=pattern_key,
                message=f"Unknown tool classification: {request.classification}"
            )

        # Default allow
        return PermissionResult(decision=PermissionDecision.ALLOWED, pattern_key=pattern_key)

    async def _check_async_internal(self, tool: ToolDefinition, arguments: dict) -> PermissionResult:  # noqa: E501
        """Internal async check with smart guardian."""
        result = self._check_internal(tool, arguments)

        # If approval required and smart guardian available, run it
        if result.decision == PermissionDecision.APPROVAL_REQUIRED and self.smart_guardian:
            smart_result = await self._run_smart_guardian(result, arguments)
            if smart_result.verdict == SmartVerdict.APPROVE:
                return PermissionResult(
                    decision=PermissionDecision.ALLOWED,
                    pattern_key=result.pattern_key,
                    smart_verdict=SmartVerdict.APPROVE.value
                )
            elif smart_result.verdict == SmartVerdict.DENY:
                return PermissionResult(
                    decision=PermissionDecision.DENIED,
                    pattern_key=result.pattern_key,
                    description=result.description,
                    message=f"Smart guardian denied: {smart_result.reasoning}",
                    smart_verdict=SmartVerdict.DENY.value
                )
            elif smart_result.verdict == SmartVerdict.DENY and self._check_circuit_breaker():
                # Circuit breaker triggered - hard stop
                return PermissionResult(
                    decision=PermissionDecision.DENIED,
                    pattern_key=result.pattern_key,
                    description=result.description,
                    message=f"Circuit breaker: {self.config.denial_breaker_threshold} consecutive smart denials",  # noqa: E501
                    smart_verdict=SmartVerdict.DENY.value
                )
            # ESCALATE falls through to human approval

        return result

    def _check_bypass(self, request: PermissionRequest) -> PermissionResult:
        """Check for bypass conditions."""
        config = get_permission_config()

        # Global YOLO
        if config.yolo_active:
            return PermissionResult(decision=PermissionDecision.ALLOWED)

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
                message=f"Blocked by {context.name} context (mode: deny)"
            )

        return PermissionResult(decision=PermissionDecision.APPROVAL_REQUIRED)

    def _get_pattern_key(self, request: PermissionRequest) -> str:
        """Generate pattern key for allowlist matching."""
        if request.tool_name == "run_command" and request.arguments:
            cmd = request.arguments.get("command", "")
            # Use first word of command as base pattern
            first_word = cmd.strip().split()[0] if cmd.strip() else request.tool_name
            return f"{request.tool_name}:{first_word}"
        return request.tool_name

    def _get_classification_value(self, classification) -> str:
        """Get classification value handling both enum and string."""
        if hasattr(classification, 'value'):
            return classification.value
        return str(classification)

    def _is_read_only(self, request: PermissionRequest) -> bool:
        """Check if tool/args are read-only."""
        if self._get_classification_value(request.classification) == "read_only":
            return True

        # run_command with read-only command
        if request.tool_name == "run_command" and request.arguments:
            cmd = request.arguments.get("command", "")
            if is_read_only_command(cmd):
                return True

        # Agent tools (run_opencode, run_hermes) when invoked by an employee
        # are budgeted, verified, and run in isolated workspace — no approval needed
        is_agent_tool = request.tool_name in ("run_opencode", "run_hermes")
        return bool(is_agent_tool and current_employee_key() is not None)

    def _check_danger(self, request: PermissionRequest) -> tuple[bool, str, str] | None:
        """Check for dangerous patterns."""
        if request.tool_name == "run_command" and request.arguments:
            cmd = request.arguments.get("command", "")
            if cmd:
                return detect_dangerous(cmd)
        return None

    def _create_approval_required(self, danger_result: tuple, request: PermissionRequest) -> PermissionResult:  # noqa: E501
        _dangerous, pattern_key, description = danger_result
        return PermissionResult(
            decision=PermissionDecision.APPROVAL_REQUIRED,
            pattern_key=pattern_key,
            description=description,
            requires_smart_check=True,
        )

    async def _run_smart_guardian(self, result: PermissionResult, arguments: dict) -> SmartVerdict:
        """Run smart guardian LLM check."""
        if not self.smart_guardian:
            return SmartVerdict(verdict=SmartVerdict.ESCALATE)

        try:
            verdict = await self.smart_guardian.assess(
                command=arguments.get("command", "") if arguments else "",
                description=result.description or "",
                pattern_keys=[result.pattern_key] if result.pattern_key else [],
            )

            # Track denials for circuit breaker
            if verdict.verdict == SmartVerdict.DENY:
                self.smart_guardian._record_denial()

            return verdict
        except Exception:
            # Smart guardian failure = escalate to human
            return SmartVerdict(verdict=SmartVerdict.ESCALATE, reasoning="Smart guardian error")

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
