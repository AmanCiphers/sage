import pytest

from app.tools.builtin import register_builtin_tools
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolInput


def test_describe_returns_tool_catalog():
    registry = ToolRegistry()
    register_builtin_tools(registry)

    tools = registry.describe()

    names = {tool["name"] for tool in tools}

    assert "get_current_time" in names
    assert "get_current_date" in names


class _T(ToolInput):
    pass


def _registry_with_task_status() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="task_status",
            description="d",
            classification=ToolClassification.READ_ONLY,
            input_schema=_T,
            handler=lambda: "ok",
        )
    )
    return registry


def test_get_recovers_unique_prefix_junk_in_name():
    """Provider junk glued after a name (`task_statuscommentary`) resolves
    to the one unambiguous tool; it still gets normal permission checks."""

    registry = _registry_with_task_status()

    assert registry.get("task_statuscommentary").name == "task_status"
    assert registry.get("task_status").name == "task_status"


def test_get_still_rejects_plain_unknown_names():
    registry = _registry_with_task_status()

    with pytest.raises(KeyError, match="Unknown tool"):
        registry.get("definitely_not_a_tool")
