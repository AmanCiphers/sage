"""Tests for investigation tools (grep, glob, web_search, browse_url), the
per-role employee allowlists, and the anti-poll circuit breaker."""

import json

import pytest

from app.inference.fake import FakeInferenceProvider
from app.inference.manager import InferenceManager
from app.inference.models import InferenceResponse, ModelCapabilities
from app.inference.registry import ModelRegistry
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import TaskStore
from app.employees.supervisor import Supervisor
from app.tools.builtin import register_builtin_tools
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------- grep/glob


def _has_ripgrep():
    import shutil
    return shutil.which("rg") is not None


@pytest.mark.skipif(not _has_ripgrep(), reason="ripgrep not installed")
async def test_grep_finds_content(tmp_path):
    from app.tools.investigation import grep

    f = tmp_path / "sample.py"
    f.write_text("def hello():\n    return 1\n")

    result = json.loads(await grep("def hello", path=str(tmp_path), include="*.py", limit=10))
    assert result["count"] >= 1
    assert any("sample.py" in m and "hello" in m for m in result["matches"])


@pytest.mark.skipif(not _has_ripgrep(), reason="ripgrep not installed")
async def test_grep_requires_pattern():
    from app.tools.investigation import grep

    result = json.loads(await grep(""))
    assert "error" in result


@pytest.mark.skipif(not _has_ripgrep(), reason="ripgrep not installed")
async def test_glob_finds_files(tmp_path):
    from app.tools.investigation import glob

    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.md").write_text("y")

    result = json.loads(await glob("**/*.py", path=str(tmp_path)))
    assert any("a.py" in f for f in result["files"])


# ---------------------------------------------------------------- web_search


def test_web_search_returns_results(monkeypatch):
    from app.tools.investigation import web_search, _search_bing

    monkeypatch.setattr(
        "app.tools.investigation._web_search_provider", lambda: "bing"
    )
    monkeypatch.setattr(
        "app.tools.investigation._search_bing",
        lambda q, m: [{"title": "t", "url": "https://example.com", "snippet": "s"}],
    )

    result = json.loads(web_search("test query", max_results=3))
    assert result["provider"] == "bing"
    assert result["results"][0]["title"] == "t"


def test_web_search_requires_query():
    from app.tools.investigation import web_search

    result = json.loads(web_search(""))
    assert "error" in result


# ---------------------------------------------------------- per-role allowlist


def test_employee_role_allowlists_are_role_specific():
    from app.employees.factory import _filter_registry, _ROLE_ALLOWLIST
    from app.tools.investigation import register_investigation_tools

    r = ToolRegistry()
    register_builtin_tools(r)
    register_investigation_tools(r)

    charlie = {t.name for t in _filter_registry(r, "charlie").list()}
    alpha = {t.name for t in _filter_registry(r, "alpha").list()}

    # Charlie (coder) has coding tools; Alpha (research) does not.
    assert "run_opencode" in charlie
    assert "run_opencode" not in alpha
    # Both share investigation tools.
    assert "grep" in charlie and "grep" in alpha
    assert "web_search" in alpha


# -------------------------------------------------------- poll circuit breaker


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


def _make_loop_with_task_polls(database):
    """Build a Supervisor + scripted provider that keeps polling task_status."""
    from app.conversation.loop import ConversationLoop
    from app.context.builder import ContextBuilder
    from app.context.compressor import ContextCompressor
    from app.execution.engine import ExecutionEngine
    from app.permissions.approvals import ApprovalManager
    from app.persistence.store import MessageStore, SessionStateStore

    provider = FakeInferenceProvider(
        scripted=[InferenceResponse(model="fake", content="done")],
        capabilities=ModelCapabilities(tool_calling=True, streaming=True, reasoning=True),
    )
    mr = ModelRegistry()
    mr.register(provider)
    im = InferenceManager(mr)
    task_store = TaskStore(database)
    supervisor = Supervisor(
        task_store=task_store,
        permission_engine=PermissionEngine(),
        registry=ToolRegistry(),
        inference_manager=im,
        model_registry=mr,
    )

    msg_store = MessageStore(database)
    session_state = SessionStateStore(database)
    compressor = ContextCompressor(im, session_state, context_window=4096)
    context_builder = ContextBuilder(
        message_store=msg_store,
        session_state=session_state,
        compressor=compressor,
        system_prompt="test",
    )
    loop = ConversationLoop(
        inference_manager=im,
        tool_registry=ToolRegistry(),
        permission_engine=PermissionEngine(),
        execution_engine=ExecutionEngine(ToolRegistry()),
        approval_manager=ApprovalManager(database),
        message_store=msg_store,
        context_builder=context_builder,
        compressor=compressor,
        max_iterations=10,
    )
    return loop


def test_poll_task_id_extracts_from_call():
    from app.conversation.loop import ConversationLoop

    call = {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "task_status",
            "arguments": '{"task_id": "task_123"}',
        },
    }
    assert ConversationLoop._poll_task_id(call) == "task_123"


def test_poll_circuit_breaker_triggers_after_threshold():
    """Repeated task_status on the same running task should inject guidance."""
    from app.conversation.loop import ConversationLoop

    loop = ConversationLoop.__new__(ConversationLoop)
    loop._task_polls = {}
    loop._MAX_REPEATED_POLLS = 2

    call = {
        "id": "c1",
        "type": "function",
        "function": {
            "name": "task_status",
            "arguments": '{"task_id": "task_x"}',
        },
    }
    tid = loop._poll_task_id(call)
    loop._task_polls[tid] = loop._task_polls.get(tid, 0) + 1
    assert loop._task_polls[tid] <= loop._MAX_REPEATED_POLLS  # allowed first time

    loop._task_polls[tid] = 3  # simulate threshold exceeded
    assert loop._task_polls[tid] > loop._MAX_REPEATED_POLLS