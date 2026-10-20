import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from app.config import settings
from app.execution.dispatcher import TaskDispatcher
from app.execution.executor import ProcessExecutor
from app.execution.protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    encode,
    make_message,
    read_message,
    validate,
)
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import TaskStore
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolInput
from app.tools.shell_tools import register_shell_tools


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_path", tmp_path)
    return tmp_path


@pytest.fixture
def sock_dir():
    """Short path — AF_UNIX caps at ~104 chars (macOS)."""

    directory = Path(tempfile.mkdtemp(prefix="sw_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


# --- wire protocol (ArchitectureV2 §11) ---


async def test_protocol_roundtrip():
    message = make_message(
        "RESULT", "task_1", "w_abc", 3, {"success": True, "output": "ok"}
    )

    reader = make_reader(encode(message))
    decoded = await read_message(reader)

    assert decoded == message
    assert decoded["protocol_version"] == 1
    assert decoded["message_type"] == "RESULT"


async def test_protocol_rejects_missing_fields():
    message = make_message("TASK", "task_1", "w_abc", 1, {})
    del message["worker_id"]

    with pytest.raises(ProtocolError, match="missing fields"):
        validate(message)


async def test_protocol_rejects_wrong_version():
    message = make_message("TASK", "task_1", "w_abc", 1, {})
    message["protocol_version"] = 99

    with pytest.raises(ProtocolError, match="protocol_version"):
        validate(message)


async def test_protocol_rejects_wrong_expect_type():
    message = make_message("RESULT", "task_1", "w_abc", 1, {})

    with pytest.raises(ProtocolError, match="expected TASK"):
        validate(message, expect_type="TASK")


async def test_protocol_rejects_invalid_json():
    with pytest.raises(ProtocolError, match="valid JSON"):
        await read_message(make_reader(b"not json\n"))


async def test_protocol_rejects_oversized_message():
    huge = make_message(
        "TASK", "task_1", "w_abc", 1, {"blob": "x" * (MAX_MESSAGE_BYTES + 10)}
    )

    with pytest.raises(ProtocolError, match="too large"):
        encode(huge)


async def test_protocol_rejects_closed_connection():
    with pytest.raises(ProtocolError, match="connection closed"):
        await read_message(make_reader(b""))


# --- process executor: real worker subprocesses ---


async def test_process_executor_runs_real_worker(sock_dir):
    executor = ProcessExecutor(socket_dir=sock_dir)

    result = await executor.run(
        "run_command",
        {"command": "echo worker-ok"},
        "task_ok",
    )

    assert result.success is True
    assert "worker-ok" in (result.output or "")
    assert result.unknown is False
    assert not (sock_dir / "task_ok.sock").exists()


async def test_process_executor_reports_worker_crash_as_unknown(sock_dir):
    crash_script = (
        "import asyncio, sys\n"
        "async def m():\n"
        "    r, w = await asyncio.open_unix_connection(sys.argv[1])\n"
        "    await asyncio.sleep(0.3)\n"
        "asyncio.run(m())\n"
    )
    executor = ProcessExecutor(
        worker_command=[sys.executable, "-c", crash_script],
        socket_dir=sock_dir,
    )

    result = await executor.run(
        "run_command", {"command": "echo hi"}, "task_crash"
    )

    assert result.unknown is True
    assert result.success is False


async def test_process_executor_missing_worker_is_failed_not_unknown(sock_dir):
    executor = ProcessExecutor(
        worker_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
        connect_timeout=0.5,
        socket_dir=sock_dir,
    )

    result = await executor.run(
        "run_command", {"command": "echo hi"}, "task_nospawn"
    )

    assert result.unknown is False
    assert result.success is False
    assert "never connected" in (result.error or "")


# --- dispatcher + real workers end to end ---


def build_dispatcher(database, executor) -> TaskDispatcher:
    registry = ToolRegistry()
    register_shell_tools(registry)
    return TaskDispatcher(
        task_store=TaskStore(database),
        permission_engine=PermissionEngine(),
        registry=registry,
        executor=executor,
    )


async def test_dispatcher_background_task_via_worker_process(database, sock_dir):
    executor = ProcessExecutor(socket_dir=sock_dir)
    dispatcher = build_dispatcher(database, executor)

    task_id = await dispatcher.start(
        objective="echo", tool_name="run_command", arguments={"command": "echo hi"}
    )
    await asyncio.gather(*dispatcher._background.values())

    task = await dispatcher.get_status(task_id)
    assert task["status"] == "completed"
    assert task["verification"] == "passed"
    assert "hi" in task["result"]


async def test_dispatcher_marks_dead_worker_unknown(database, sock_dir):
    crash_script = (
        "import asyncio, sys\n"
        "async def m():\n"
        "    r, w = await asyncio.open_unix_connection(sys.argv[1])\n"
        "    await asyncio.sleep(0.3)\n"
        "asyncio.run(m())\n"
    )
    executor = ProcessExecutor(
        worker_command=[sys.executable, "-c", crash_script],
        socket_dir=sock_dir,
    )
    dispatcher = build_dispatcher(database, executor)

    task_id = await dispatcher.start(
        objective="echo", tool_name="run_command", arguments={"command": "echo hi"}
    )
    await asyncio.gather(*dispatcher._background.values())

    task = await dispatcher.get_status(task_id)
    assert task["status"] == "unknown"
    assert task["error"]


# --- SAGE-side gates ---


class IneligibleInput(ToolInput):
    pass


async def test_start_refuses_non_worker_eligible_tool(database, sock_dir):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="inline_only",
            description="Not for background.",
            classification=ToolClassification.READ_ONLY,
            input_schema=IneligibleInput,
            worker_eligible=False,
            handler=lambda: "ok",
        )
    )
    dispatcher = TaskDispatcher(
        task_store=TaskStore(database),
        permission_engine=PermissionEngine(),
        registry=registry,
        executor=ProcessExecutor(socket_dir=sock_dir),
    )

    with pytest.raises(PermissionError, match="cannot run in the background"):
        await dispatcher.start(
            objective="x", tool_name="inline_only", arguments={}
        )


async def test_recover_marks_stuck_tasks_unknown(database, sock_dir):
    store = TaskStore(database)
    task_id = await store.create(objective="stuck", tool_name="ls", arguments={})
    await store.set_status(task_id, "running")

    dispatcher = TaskDispatcher(
        task_store=store,
        permission_engine=PermissionEngine(),
        registry=ToolRegistry(),
        executor=ProcessExecutor(socket_dir=sock_dir),
    )

    recovered = await dispatcher.recover()

    assert recovered == 1
    task = await store.get(task_id)
    assert task["status"] == "unknown"
