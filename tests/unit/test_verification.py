import json

import pytest

from app.config import settings
from app.execution.engine import ExecutionEngine
from app.execution.verification import verify
from app.operations.manager import OperationManager
from app.permissions.approvals import ApprovalManager
from app.persistence.database import Database
from app.persistence.schema import SCHEMA_SQL, initialize_schema
from app.persistence.store import TaskStore
from app.tools.file_tools import register_file_tools
from app.tools.registry import ToolRegistry


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


# --- deterministic verifiers (ArchitectureV2 §60-61) ---


def test_verify_write_file_pass(tmp_path):
    (tmp_path / "a.md").write_text("hello")

    v = verify("write_file", {"path": "a.md", "content": "hello"}, "{}")

    assert v.status == "passed"
    assert all(c["ok"] for c in v.checks)


def test_verify_write_file_content_mismatch(tmp_path):
    (tmp_path / "a.md").write_text("WRONG")

    v = verify("write_file", {"path": "a.md", "content": "hello"}, "{}")

    assert v.status == "failed"
    assert "content_matches" in v.detail


def test_verify_write_file_missing_file(tmp_path):
    v = verify("write_file", {"path": "ghost.md", "content": "x"}, "{}")

    assert v.status == "failed"
    assert "file_exists" in v.detail


def test_verify_patch_pass(tmp_path):
    (tmp_path / "a.md").write_text("version 2")

    v = verify(
        "patch",
        {"path": "a.md", "old_text": "version 1", "new_text": "version 2"},
        "{}",
    )

    assert v.status == "passed"


def test_verify_patch_stale_text_still_present(tmp_path):
    (tmp_path / "a.md").write_text("version 1")

    v = verify(
        "patch",
        {"path": "a.md", "old_text": "version 1", "new_text": "version 2"},
        "{}",
    )

    assert v.status == "failed"


def test_verify_run_command_exit_zero():
    v = verify(
        "run_command",
        {"command": "echo hi"},
        json.dumps({"exit_code": 0, "stdout": "hi\n", "stderr": ""}),
    )

    assert v.status == "passed"


def test_verify_run_command_nonzero_exit():
    v = verify(
        "run_command",
        {"command": "exit 3"},
        json.dumps({"exit_code": 3, "stdout": "", "stderr": ""}),
    )

    assert v.status == "failed"
    assert "exit_code=3" in v.detail


def test_verify_run_command_timeout_is_failed():
    v = verify(
        "run_command",
        {"command": "sleep 5"},
        json.dumps({"error": "timed out after 1s"}),
    )

    assert v.status == "failed"


def test_verify_unknown_tool_is_unverified_not_passed():
    v = verify("get_current_time", {}, "{}")

    assert v.status == "unverified"
    assert v.checks == []


# --- schema upgrade for pre-existing databases ---


async def test_initialize_schema_adds_verification_columns(tmp_path):
    db = Database(tmp_path / "legacy.db")
    await db.connect()

    # A database created before verification existed.
    legacy = SCHEMA_SQL.replace(
        "    verification      TEXT,\n    verification_detail TEXT,\n", ""
    )
    await db.connection.executescript(legacy)
    await db.connection.execute(
        "INSERT INTO tasks (task_id, objective, tool_name, arguments_json)"
        " VALUES ('t1', 'o', 'ls', '{}')"
    )
    await db.connection.commit()

    await initialize_schema(db)

    task = await TaskStore(db).get("t1")
    assert task is not None
    assert task["verification"] is None
    await db.close()


# --- approved operations are verified too ---


async def test_operation_with_failed_verification_marks_failed(
    database, monkeypatch
):
    registry = ToolRegistry()
    register_file_tools(registry)
    engine = ExecutionEngine(registry)
    manager = OperationManager(database, engine, registry)
    approvals = ApprovalManager(database)

    approval_id = await approvals.create_pending(
        tool_name="patch",
        arguments={
            "path": "gone.md",
            "old_text": "never there",
            "new_text": "new",
        },
    )
    await approvals.approve(approval_id)

    # File does not exist → patch reports an error payload, but the
    # tool call "succeeds"; verification must reject the outcome.
    status = await manager.execute(
        "patch",
        {"path": "gone.md", "old_text": "never there", "new_text": "new"},
        approval_id=approval_id,
    )

    assert status["status"] == "FAILED"
    assert "verification failed" in status["error"]
