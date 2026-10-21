import pytest

from app.execution.engine import ExecutionEngine
from app.execution.models import ToolCall
from app.tools.builtin import register_builtin_tools
from app.tools.registry import ToolRegistry


def create_execution_engine() -> ExecutionEngine:
    registry = ToolRegistry()
    register_builtin_tools(registry)

    return ExecutionEngine(registry)


@pytest.mark.asyncio
async def test_execute_current_time() -> None:
    engine = create_execution_engine()

    result = await engine.execute(
        ToolCall(
            tool_name="get_current_time",
            arguments={},
        )
    )

    assert result.success is True
    assert result.tool_name == "get_current_time"
    assert result.output is not None


@pytest.mark.asyncio
async def test_invalid_tool_arguments_are_rejected() -> None:
    engine = create_execution_engine()

    result = await engine.execute(
        ToolCall(
            tool_name="get_current_time",
            arguments={
                "unexpected_argument": "bad",
            },
        )
    )

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_execute_current_date() -> None:
    engine = create_execution_engine()

    result = await engine.execute(
        ToolCall(
            tool_name="get_current_date",
            arguments={},
        )
    )

    assert result.success is True
    assert result.tool_name == "get_current_date"
    assert result.output is not None
