
import httpx
import pytest

from app.context.builder import ContextBuilder
from app.context.compressor import ContextCompressor
from app.conversation.loop import ConversationLoop
from app.conversation.models import UserMessage
from app.execution.engine import ExecutionEngine
from app.inference.fake import FakeInferenceProvider
from app.inference.manager import InferenceManager
from app.inference.models import (
    InferenceRequest,
    InferenceResponse,
    ModelCapabilities,
    ModelMetadata,
)
from app.inference.providers.config import ProviderConfig
from app.inference.providers.openai_compatible import OpenAICompatibleProvider
from app.inference.registry import ModelRegistry
from app.permissions.approvals import ApprovalManager
from app.permissions.engine import PermissionEngine
from app.persistence.database import Database
from app.persistence.schema import initialize_schema
from app.persistence.store import MessageStore, SessionStateStore
from app.tools.registry import ToolRegistry


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await initialize_schema(db)
    yield db
    await db.close()


# --- SSE parsing ---


def _sse_response(handler) -> httpx.Response:
    async def content():
        for line in handler():
            yield line.encode()

    return httpx.Response(200, content=content())


async def test_openai_provider_streams_content_chunks():
    def sse():
        yield 'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        yield 'data: {"choices":[{"delta":{"content":"lo "}}]}\n\n'
        yield 'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
        yield "data: [DONE]\n\n"

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _sse_response(sse))
    )
    provider = OpenAICompatibleProvider(
        config=ProviderConfig(api_key="k", base_url="https://example.com/v1"),
        metadata=ModelMetadata(
            provider="test",
            model_id="m",
            context_window=4096,
            capabilities=ModelCapabilities(streaming=True),
        ),
        client=client,
    )

    deltas = []
    async for delta in provider.stream(
        InferenceRequest(messages=[{"role": "user", "content": "hi"}])
    ):
        deltas.append(delta)

    assert [d.content for d in deltas] == ["Hel", "lo ", "world"]
    assert all(d.tool_calls is None for d in deltas)
    await client.aclose()


async def test_openai_provider_accumulates_tool_call_fragments():
    def sse():
        yield 'data: {"choices":[{"delta":{"content":"checking"}}]}\n\n'
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"id":"call_abc","type":"function","function":'
            '{"name":"get_current_time","arguments":""}}]}}]}\n\n'
        )
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"{}"}}]}}]}\n\n'
        )
        yield "data: [DONE]\n\n"

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _sse_response(sse))
    )
    provider = OpenAICompatibleProvider(
        config=ProviderConfig(api_key="k", base_url="https://example.com/v1"),
        metadata=ModelMetadata(
            provider="test",
            model_id="m",
            context_window=4096,
            capabilities=ModelCapabilities(streaming=True, tool_calling=True),
        ),
        client=client,
    )

    content_parts = []
    tool_calls = None
    async for delta in provider.stream(
        InferenceRequest(messages=[{"role": "user", "content": "time"}])
    ):
        if delta.content:
            content_parts.append(delta.content)
        if delta.tool_calls:
            tool_calls = delta.tool_calls

    assert "".join(content_parts) == "checking"
    assert tool_calls == [
        {
            "id": "call_abc",
            "type": "function",
            "function": {"name": "get_current_time", "arguments": "{}"},
        }
    ]
    await client.aclose()


# --- Manager fallback ---


class FailingProvider(FakeInferenceProvider):
    async def generate(self, request):
        raise RuntimeError("primary down")


async def test_manager_stream_falls_back_before_first_delta():
    registry = ModelRegistry()
    registry.register(
        FailingProvider(
            model_id="broken",
            capabilities=ModelCapabilities(tool_calling=True, streaming=True),
            priority=10,
        )
    )
    working = FakeInferenceProvider(
        model_id="working",
        capabilities=ModelCapabilities(tool_calling=True, streaming=True),
        priority=0,
        scripted=[InferenceResponse(model="working", content="fallback answer")],
    )
    registry.register(working)

    manager = InferenceManager(registry)

    deltas = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    response = await manager.stream(
        InferenceRequest(messages=[{"role": "user", "content": "hi"}]),
        on_delta=on_delta,
    )

    assert response.content == "fallback answer"
    # Never re-emitted by broken provider; only the working one streamed.
    assert "".join(deltas) == "fallback answer"


# --- Loop streaming ---


async def test_loop_handle_stream_delivers_deltas_and_persists(database):
    provider = FakeInferenceProvider(
        scripted=[InferenceResponse(model="fake", content="Streamed reply!")],
        capabilities=ModelCapabilities(tool_calling=True, streaming=True),
    )
    registry = ModelRegistry()
    registry.register(provider)
    inference_manager = InferenceManager(registry)

    tool_registry = ToolRegistry()
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

    loop = ConversationLoop(
        inference_manager=inference_manager,
        tool_registry=tool_registry,
        permission_engine=PermissionEngine(),
        execution_engine=ExecutionEngine(tool_registry),
        approval_manager=ApprovalManager(database),
        message_store=message_store,
        context_builder=builder,
        compressor=compressor,
        max_iterations=3,
    )

    deltas = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    response = await loop.handle_stream(
        UserMessage(content="Say something"), on_delta=on_delta
    )

    # Fake chunks at 12 chars: multiple deltas, full content preserved.
    assert len(deltas) > 1
    assert "".join(deltas) == "Streamed reply!"
    assert response.content == "Streamed reply!"

    messages = await message_store.recent(10)
    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    assert assistant["content"] == "Streamed reply!"


async def test_stream_sanitizes_leaked_chat_tokens_in_tool_calls():
    """gpt-oss-style `<|channel|>commentary` leaks must never reach the
    tool registry as part of a name."""

    def sse():
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"id":"c1","type":"function","function":'
            '{"name":"start_task","arguments":""}}]}}]}\n\n'
        )
        yield (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"name":"<|channel|>commentary",'
            '"arguments":"<|channel|>commentary{}}"}}]}}]}\n\n'
        )
        yield "data: [DONE]\n\n"

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _sse_response(sse))
    )
    provider = OpenAICompatibleProvider(
        config=ProviderConfig(api_key="k", base_url="https://example.com/v1"),
        metadata=ModelMetadata(
            provider="test",
            model_id="m",
            context_window=4096,
            capabilities=ModelCapabilities(streaming=True),
        ),
        client=client,
    )

    tool_calls = []
    async for delta in provider.stream(
        InferenceRequest(messages=[{"role": "user", "content": "hi"}])
    ):
        if delta.tool_calls:
            tool_calls.extend(delta.tool_calls)
    await client.aclose()

    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "start_task"
    assert tool_calls[0]["function"]["arguments"] == "{}"
