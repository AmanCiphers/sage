import asyncio

import pytest

from app.execution.dispatcher import TaskDispatcher
from app.execution.engine import ExecutionEngine
from app.execution.executor import InlineExecutor
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import TaskStore
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolInput


class SlowInput(ToolInput):
    seconds: float = 0.01
    label: str = "ok"


class ExplodeInput(ToolInput):
    pass


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


def build_dispatcher(database):
    registry = ToolRegistry()

    def slow_tool(seconds: float, label: str) -> str:
        return f"done:{label}"

    def explode_tool() -> str:
        raise RuntimeError("boom")

    registry.register(
        ToolDefinition(
            name="slow_tool",
            description="A slow tool.",
            classification=ToolClassification.READ_ONLY,
            input_schema=SlowInput,
            handler=slow_tool,
        )
    )
    registry.register(
        ToolDefinition(
            name="explode_tool",
            description="A failing tool.",
            classification=ToolClassification.READ_ONLY,
            input_schema=ExplodeInput,
            handler=explode_tool,
        )
    )

    return TaskDispatcher(
        task_store=TaskStore(database),
        permission_engine=PermissionEngine(),
        registry=registry,
        executor=InlineExecutor(ExecutionEngine(registry)),
    )


async def test_start_task_returns_immediately_and_completes(database):
    dispatcher = build_dispatcher(database)

    task_id = await dispatcher.start(
        objective="Run the slow tool",
        tool_name="slow_tool",
        arguments={"seconds": 0.01, "label": "hello"},
    )

    assert task_id.startswith("task_")

    # Status is durable and visible immediately (queued/running).
    task = await dispatcher.get_status(task_id)
    assert task["status"] in {"queued", "running", "completed"}

    await asyncio.gather(*dispatcher._background.values())

    task = await dispatcher.get_status(task_id)
    assert task["status"] == "completed"
    assert task["result"] == "done:hello"


async def test_failing_task_is_recorded_as_failed(database):
    dispatcher = build_dispatcher(database)

    task_id = await dispatcher.start(
        objective="Explode",
        tool_name="explode_tool",
        arguments={},
    )

    await asyncio.gather(*dispatcher._background.values())

    task = await dispatcher.get_status(task_id)
    assert task["status"] == "failed"
    assert "boom" in task["error"]


async def test_unknown_task_id_returns_none(database):
    dispatcher = build_dispatcher(database)

    assert await dispatcher.get_status("task_missing") is None
