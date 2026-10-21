import json

import pytest

from app.context.builder import ContextBuilder
from app.context.compressor import ContextCompressor
from app.conversation.loop import ConversationLoop
from app.conversation.models import UserMessage
from app.execution.engine import ExecutionEngine
from app.inference.fake import FakeInferenceProvider
from app.inference.manager import InferenceManager
from app.inference.models import InferenceResponse, ModelCapabilities
from app.inference.registry import ModelRegistry
from app.operations.manager import OperationManager
from app.permissions.approvals import ApprovalError, ApprovalManager
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import MessageStore, SessionStateStore
from app.tools.file_tools import register_file_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


def tool_call_response(name: str, arguments: str = "{}") -> InferenceResponse:
    return InferenceResponse(
        model="fake",
        tool_calls=[
            {
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    )


def final_response(content: str) -> InferenceResponse:
    return InferenceResponse(model="fake", content=content)


# --- ApprovalManager ---


async def test_create_get_approve(database):
    approvals = ApprovalManager(database)

    approval_id = await approvals.create_pending(
        "write_file",
        {"path": "/tmp/x", "content": "hi"},
        resource="/tmp/x",
    )

    approval = await approvals.get(approval_id)
    assert approval["status"] == "pending"
    assert approval["tool_name"] == "write_file"
    assert approval["arguments"]["path"] == "/tmp/x"

    approved = await approvals.approve(approval_id)
    assert approved["status"] == "approved"
    assert approved["arguments"] == {"path": "/tmp/x", "content": "hi"}


async def test_approve_twice_fails(database):
    approvals = ApprovalManager(database)
    approval_id = await approvals.create_pending("write_file", {"path": "/x"})

    await approvals.approve(approval_id)

    with pytest.raises(ApprovalError):
        await approvals.approve(approval_id)


async def test_deny_then_approve_fails(database):
    approvals = ApprovalManager(database)
    approval_id = await approvals.create_pending("write_file", {"path": "/x"})

    await approvals.deny(approval_id)

    with pytest.raises(ApprovalError):
        await approvals.approve(approval_id)


async def test_expired_approval_cannot_be_approved(database):
    approvals = ApprovalManager(database)
    approval_id = await approvals.create_pending("write_file", {"path": "/x"})

    await database.execute(
        "UPDATE approvals SET expires_at = datetime('now', '-1 second') "
        "WHERE approval_id = ?",
        (approval_id,),
    )
    await database.commit()

    with pytest.raises(ApprovalError):
        await approvals.approve(approval_id)

    approval = await approvals.get(approval_id)
    assert approval["status"] == "expired"


# --- write_file tool ---


def test_write_file_creates_and_overwrites(tmp_path):
    from app.tools.file_tools import write_file

    new_file = tmp_path / "notes.md"
    result = json.loads(write_file(str(new_file), "# hello"))
    assert result["path"] == str(new_file)
    assert new_file.read_text() == "# hello"

    result = json.loads(write_file(str(new_file), "# updated"))
    assert new_file.read_text() == "# updated"


def test_write_file_creates_missing_parents_and_rejects_dir(tmp_path):
    from app.tools.file_tools import write_file

    nested = tmp_path / "a" / "b" / "notes.md"
    result = json.loads(write_file(str(nested), "hi"))
    assert "path" in result
    assert nested.read_text() == "hi"

    result = json.loads(write_file(str(tmp_path), "hi"))
    assert "error" in result


def test_relative_paths_anchor_to_workspace(tmp_path, monkeypatch):
    from app.config import settings
    from app.tools.file_tools import (
        _write_needs_approval,
        resolve_path,
        write_file,
    )

    monkeypatch.setattr(settings, "workspace_path", tmp_path)

    json.loads(write_file("notes.md", "relative"))
    assert (tmp_path / "notes.md").read_text() == "relative"
    assert resolve_path("notes.md") == (tmp_path / "notes.md").resolve()
    assert resolve_path("sub/notes.md") == (tmp_path / "sub" / "notes.md").resolve()

    # Inside the workspace, editing an existing file needs no approval.
    assert _write_needs_approval({"path": "notes.md"}) is False
    assert _write_needs_approval({"path": "fresh.md"}) is False


def test_write_file_never_needs_approval(tmp_path):
    from app.tools.file_tools import _write_needs_approval

    # A5: file edits never require approval — even outside the workspace.
    existing = tmp_path / "a.txt"
    existing.write_text("old")

    assert _write_needs_approval({"path": str(existing)}) is False
    assert _write_needs_approval({"path": str(tmp_path / "b.txt")}) is False
    assert _write_needs_approval({"path": "notes.md"}) is False
    assert _write_needs_approval({}) is False


# --- OperationManager ---


async def test_operation_execute_success(database, tmp_path):
    tool_registry = ToolRegistry()
    register_file_tools(tool_registry)
    execution = ExecutionEngine(tool_registry)
    operations = OperationManager(database, execution, tool_registry)

    target = tmp_path / "op.md"
    operation = await operations.execute(
        "write_file", {"path": str(target), "content": "done"}
    )

    assert operation["status"] == "SUCCEEDED"
    assert target.read_text() == "done"


async def test_operation_execute_failure(database):
    tool_registry = ToolRegistry()
    register_file_tools(tool_registry)
    execution = ExecutionEngine(tool_registry)
    operations = OperationManager(database, execution, tool_registry)

    operation = await operations.execute("no_such_tool", {})

    assert operation["status"] == "FAILED"
    assert operation["error"]


async def test_operation_recover_marks_executing_unknown(database):
    tool_registry = ToolRegistry()
    execution = ExecutionEngine(tool_registry)
    operations = OperationManager(database, execution, tool_registry)

    await database.execute(
        "INSERT INTO operations (operation_id, tool_name, arguments_json, status) "
        "VALUES ('op_stuck', 'write_file', '{}', 'EXECUTING')"
    )
    await database.commit()

    recovered = await operations.recover()
    assert recovered == 1

    operation = await operations.get("op_stuck")
    assert operation["status"] == "UNKNOWN"


# --- Loop approval flow ---


def build_loop(database, scripted, tmp_path):
    provider = FakeInferenceProvider(
        scripted=scripted,
        capabilities=ModelCapabilities(
            tool_calling=True, streaming=True, reasoning=True
        ),
    )
    model_registry = ModelRegistry()
    model_registry.register(provider)
    inference_manager = InferenceManager(model_registry)

    tool_registry = ToolRegistry()
    permission_engine = PermissionEngine()
    execution_engine = ExecutionEngine(tool_registry)
    approval_manager = ApprovalManager(database)
    message_store = MessageStore(database)
    session_state = SessionStateStore(database)

    compressor = ContextCompressor(
        inference_manager=inference_manager,
        session_state=session_state,
        context_window=4096,
    )
    builder = ContextBuilder(
        message_store=message_store,
        session_state=session_state,
        compressor=compressor,
        system_prompt="You are SAGE.",
    )

    register_file_tools(tool_registry)

    loop = ConversationLoop(
        inference_manager=inference_manager,
        tool_registry=tool_registry,
        permission_engine=permission_engine,
        execution_engine=execution_engine,
        approval_manager=approval_manager,
        message_store=message_store,
        context_builder=builder,
        compressor=compressor,
        max_iterations=3,
    )

    return loop, approval_manager, execution_engine


async def test_loop_overwrite_runs_without_approval(database, tmp_path):
    """A5: overwriting an existing file no longer needs approval — the
    write happens in the same turn and no pending approval is created."""

    target = tmp_path / "existing.md"
    target.write_text("ORIGINAL")

    arguments = json.dumps({"path": str(target), "content": "NEW"})
    loop, _, _ = build_loop(
        database,
        scripted=[
            tool_call_response("write_file", arguments),
            final_response("Overwrote the file."),
        ],
        tmp_path=tmp_path,
    )

    response = await loop.handle(UserMessage(content="Overwrite existing.md"))

    assert response.pending_approval is None
    assert target.read_text() == "NEW"


async def test_loop_create_new_file_runs_immediately(database, tmp_path):
    target = tmp_path / "new.md"
    arguments = json.dumps({"path": str(target), "content": "created"})

    loop, _, _ = build_loop(
        database,
        scripted=[
            tool_call_response("write_file", arguments),
            final_response("File created."),
        ],
        tmp_path=tmp_path,
    )

    response = await loop.handle(UserMessage(content="Create new.md"))

    assert response.pending_approval is None
    assert target.read_text() == "created"


async def test_approved_operation_executes_bound_arguments(database, tmp_path):
    """Approving runs the exact recorded tool_name/arguments as an
    operation — the binding (ArchitectureV2 §27). Uses an explicit
    pending row because file edits no longer prompt (A5)."""

    target = tmp_path / "bound.md"
    target.write_text("OLD")

    _, approvals, execution = build_loop(
        database,
        scripted=[final_response("Nothing to do inline.")],
        tmp_path=tmp_path,
    )
    assert approvals is not None

    approval_id = await approvals.create_pending(
        "write_file",
        {"path": str(target), "content": "APPROVED"},
        resource=str(target),
    )
    approved = await approvals.approve(approval_id)
    assert approved["arguments"]["content"] == "APPROVED"

    tool_registry = ToolRegistry()
    register_file_tools(tool_registry)
    operations = OperationManager(database, execution, tool_registry)
    operation = await operations.execute(
        approved["tool_name"], approved["arguments"], approval_id=approval_id
    )

    assert operation["status"] == "SUCCEEDED"
    assert target.read_text() == "APPROVED"
