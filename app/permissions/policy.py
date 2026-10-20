from app.tools.models import ToolClassification

from .models import PermissionDecision, PermissionRequest


class PermissionPolicy:
    """
    Deterministic policy for deciding whether a tool may execute.

    The LLM never participates in this decision.
    """

    def evaluate(
        self,
        request: PermissionRequest,
    ) -> PermissionDecision:

        classification = request.classification

        # Read-only operations are safe by default.
        if classification == ToolClassification.READ_ONLY.value:
            return PermissionDecision.ALLOWED

        # Destructive local mutations require user approval.
        if classification == ToolClassification.LOCAL_MUTATION.value:
            if request.destructive:
                return PermissionDecision.APPROVAL_REQUIRED

            return PermissionDecision.ALLOWED

        # External side effects always require approval for now.
        if classification == ToolClassification.EXTERNAL_SIDE_EFFECT.value:
            return PermissionDecision.APPROVAL_REQUIRED

        # Unknown classifications fail closed.
        return PermissionDecision.DENIED
