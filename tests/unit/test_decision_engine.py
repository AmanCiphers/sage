import pytest

from app.conversation.models import UserMessage
from app.decision.engine import DeterministicDecisionEngine
from app.decision.models import DecisionAction


@pytest.mark.asyncio
async def test_time_request_creates_tool_decision() -> None:
    engine = DeterministicDecisionEngine()

    decision = await engine.decide(
        UserMessage(content="What time is it?")
    )

    assert decision.action == DecisionAction.USE_TOOL
    assert decision.tool_name == "get_current_time"


@pytest.mark.asyncio
async def test_unknown_request_creates_response_decision() -> None:
    engine = DeterministicDecisionEngine()

    decision = await engine.decide(
        UserMessage(content="Build me a rocket")
    )

    assert decision.action == DecisionAction.RESPOND
    assert decision.response is not None


@pytest.mark.asyncio
async def test_date_request_creates_tool_decision() -> None:
    engine = DeterministicDecisionEngine()

    decision = await engine.decide(
        UserMessage(content="What is today's date?")
    )

    assert decision.action == DecisionAction.USE_TOOL
    assert decision.tool_name == "get_current_date"
