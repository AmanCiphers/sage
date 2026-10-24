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
from app.permissions.approvals import ApprovalManager
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import MessageStore, SessionStateStore
from app.tools.builtin import register_builtin_tools
from app.tools.models import ToolClassification, ToolDefinition
from app.tools.registry import ToolRegistry


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


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


def build_loop(database, scripted, extra_tools=None):
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

    if extra_tools:
        for tool in extra_tools:
            tool_registry.register(tool)

    register_builtin_tools(tool_registry)

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

    return loop, message_store, provider


async def test_loop_executes_tool_then_returns_final_answer(database):
    loop, message_store, _ = build_loop(
        database,
        scripted=[
            tool_call_response("get_current_time"),
            final_response("The current time is 12:00."),
        ],
    )

    response = await loop.handle(UserMessage(content="What time is it?"))

    assert response.content == "The current time is 12:00."

    messages = await message_store.recent(50)

    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert messages[1]["tool_calls"][0]["function"]["name"] == "get_current_time"
    assert json.loads(messages[2]["content"])["success"] is True


async def test_loop_stops_at_iteration_budget(database):
    loop, _, provider = build_loop(
        database,
        scripted=[
            tool_call_response("get_current_time")
            for _ in range(5)
        ],
    )

    response = await loop.handle(UserMessage(content="Loop forever"))

    assert "iteration budget" in response.content
    assert len(provider.requests) == 3


async def test_loop_destructive_tool_requests_approval(database):
    ran = []

    dangerous_tool = ToolDefinition(
        name="delete_everything",
        description="Delete everything.",
        classification=ToolClassification.LOCAL_MUTATION,
        input_schema=None,
        destructive=True,
        employee_eligible=False,
        handler=lambda: ran.append("executed"),
    )

    loop, message_store, _ = build_loop(
        database,
        scripted=[
            tool_call_response("delete_everything"),
        ],
        extra_tools=[dangerous_tool],
    )

    response = await loop.handle(UserMessage(content="Delete everything"))

    # Turn ends immediately when approval is needed - no LLM response generated
    assert response.content == ""
    assert ran == []
    assert response.pending_approval is not None
    assert response.pending_approval["tool_name"] == "delete_everything"

    # Tool was never executed (no result appended to history since turn stopped)
    messages = await message_store.recent(50)
    # Should have: user msg, assistant msg with tool_calls
    assert len(messages) == 2


async def test_loop_reports_unknown_tool(database):
    loop, message_store, _ = build_loop(
        database,
        scripted=[
            tool_call_response("no_such_tool"),
            final_response("I don't have that tool."),
        ],
    )

    await loop.handle(UserMessage(content="Do the thing"))

    messages = await message_store.recent(50)
    tool_result = json.loads(messages[2]["content"])

    assert tool_result["success"] is False
    assert "Unknown tool" in tool_result["error"]
