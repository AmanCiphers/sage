import pytest

from app.conversation.engine import ConversationEngine
from app.conversation.models import UserMessage
from app.decision.engine import DeterministicDecisionEngine
from app.execution.engine import ExecutionEngine
from app.tools.builtin import register_builtin_tools
from app.tools.registry import ToolRegistry


class StubResponseGenerator:
    async def generate(
        self,
        message,
        tool_name=None,
        tool_result=None,
    ) -> str:
        return str(tool_result)


def create_conversation_engine() -> ConversationEngine:
    registry = ToolRegistry()
    register_builtin_tools(registry)

    execution_engine = ExecutionEngine(registry)
    decision_engine = DeterministicDecisionEngine()
    response_generator = StubResponseGenerator()

    return ConversationEngine(
        decision_engine,
        execution_engine,
        response_generator,
    )


@pytest.mark.asyncio
async def test_time_request() -> None:
    engine = create_conversation_engine()

    response = await engine.handle(
        UserMessage(content="What time is it?")
    )

    assert response.content
    assert "IST" in response.content


@pytest.mark.asyncio
async def test_unknown_request() -> None:
    engine = create_conversation_engine()

    response = await engine.handle(
        UserMessage(content="Build me a rocket")
    )

    assert "don't know" in response.content.lower()

