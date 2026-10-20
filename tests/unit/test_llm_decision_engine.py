import json

import pytest

from app.conversation.models import UserMessage
from app.decision.llm_engine import LLMDecisionEngine
from app.inference.models import InferenceResponse
from app.tools.builtin import register_builtin_tools
from app.tools.registry import ToolRegistry


class StubInferenceManager:
    def __init__(self, response: str) -> None:
        self.response = response

    async def generate(self, request, requirements=None):
        return InferenceResponse(
            content=self.response,
            model="stub",
        )
        
        
def create_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry




@pytest.mark.asyncio
async def test_llm_decision_engine_returns_response_decision():
    manager = StubInferenceManager(
        json.dumps(
            {
                "action": "respond",
                "tool_name": None,
                "arguments": {},
                "response": "Hello. How can I help?",
            }
        )
    )

    engine = LLMDecisionEngine(
        manager,
        create_tool_registry(),
    )

    decision = await engine.decide(
        UserMessage(content="Hello SAGE")
    )

    assert decision.action.value == "respond"
    assert decision.response == "Hello. How can I help?"


@pytest.mark.asyncio
async def test_llm_decision_engine_returns_tool_decision():
    manager = StubInferenceManager(
        json.dumps(
            {
                "action": "use_tool",
                "tool_name": "get_current_time",
                "arguments": {},
                "response": None,
            }
        )
    )

    engine = LLMDecisionEngine(
        manager,
        create_tool_registry(),
    )

    decision = await engine.decide(
        UserMessage(content="What time is it?")
    )

    assert decision.action.value == "use_tool"
    assert decision.tool_name == "get_current_time"


@pytest.mark.asyncio
async def test_llm_decision_engine_rejects_invalid_json():
    manager = StubInferenceManager(
        "This is not valid JSON."
    )

    engine = LLMDecisionEngine(
        manager,
        create_tool_registry(),
    )

    with pytest.raises(ValueError, match="invalid decision"):
        await engine.decide(
            UserMessage(content="Hello")
        )


