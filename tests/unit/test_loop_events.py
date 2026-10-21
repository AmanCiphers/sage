
import pytest

from app.context.builder import ContextBuilder
from app.context.compressor import ContextCompressor
from app.conversation.loop import ConversationLoop
from app.conversation.models import LoopEvent, UserMessage
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


def build_loop(database, scripted, extra_tools=None):
    provider = FakeInferenceProvider(
        scripted=scripted,
        capabilities=ModelCapabilities(
            tool_calling=True, streaming=True, reasoning=True
        ),
    )
    registry = ModelRegistry()
    registry.register(provider)
    inference_manager = InferenceManager(registry)

    tool_registry = ToolRegistry()
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
        permission_engine=PermissionEngine(),
        execution_engine=ExecutionEngine(tool_registry),
        approval_manager=approval_manager,
        message_store=message_store,
        context_builder=builder,
        compressor=compressor,
        max_iterations=3,
    )
    return loop


async def test_loop_emits_tool_round_events(database):
    loop = build_loop(
        database,
        scripted=[
            tool_call_response("get_current_time"),
            final_response("It is 12:00."),
        ],
    )

    events: list[LoopEvent] = []

    async def on_event(event: LoopEvent) -> None:
        events.append(event)

    loop.on_event = on_event
    await loop.handle(UserMessage(content="What time?"))

    sequence = [e.type for e in events]
    assert sequence == ["llm_start", "tool_start", "tool_end", "llm_start"]

    tool_start = events[1]
    assert tool_start.tool_name == "get_current_time"

    tool_end = events[2]
    assert tool_end.success is True
    assert tool_end.duration_ms is not None


async def test_loop_emits_approval_event_not_tool_end(database):
    ran = []

    dangerous = ToolDefinition(
        name="delete_everything",
        description="Delete everything.",
        classification=ToolClassification.LOCAL_MUTATION,
        input_schema=None,
        destructive=True,
        employee_eligible=False,
        handler=lambda: ran.append("x"),
    )

    loop = build_loop(
        database,
        scripted=[
            tool_call_response("delete_everything"),
            final_response("Needs approval."),
        ],
        extra_tools=[dangerous],
    )

    events: list[LoopEvent] = []
    loop.on_event = events.append
    await loop.handle(UserMessage(content="Delete everything"))

    types = [e.type for e in events]
    assert "approval" in types
    assert "tool_end" not in types  # approval replaces the normal end event

    approval = next(e for e in events if e.type == "approval")
    assert approval.tool_name == "delete_everything"
    assert approval.approval_id
    assert ran == []
