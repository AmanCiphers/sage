from app.permissions import PermissionDecision, PermissionEngine
from app.tools.models import ToolClassification, ToolDefinition


def dummy_handler():
    return "executed"


def make_tool(
    name: str = "test_tool",
    classification: ToolClassification = ToolClassification.READ_ONLY,
    destructive: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Test tool",
        classification=classification,
        destructive=destructive,
        worker_eligible=False,
        handler=dummy_handler,
    )


def test_read_only_tool_is_allowed():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.READ_ONLY,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.ALLOWED


def test_non_destructive_local_mutation_is_allowed():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.LOCAL_MUTATION,
        destructive=False,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.ALLOWED


def test_destructive_local_mutation_requires_approval():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.LOCAL_MUTATION,
        destructive=True,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.APPROVAL_REQUIRED


def test_external_side_effect_requires_approval():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.EXTERNAL_SIDE_EFFECT,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.APPROVAL_REQUIRED


def test_destructive_external_side_effect_requires_approval():
    engine = PermissionEngine()

    tool = make_tool(
        classification=ToolClassification.EXTERNAL_SIDE_EFFECT,
        destructive=True,
    )

    result = engine.check(tool)

    assert result == PermissionDecision.APPROVAL_REQUIRED


def test_permission_engine_fails_closed_for_unknown_classification():
    engine = PermissionEngine()

    tool = make_tool()

    # Simulate an invalid classification at runtime.
    tool.classification = "unknown"

    result = engine.check(tool)

    assert result == PermissionDecision.DENIED
