import pytest

from app.conversation.models import UserMessage
from app.response.generator import LLMResponseGenerator


class StubInferenceManager:
    def __init__(self, response: str):
        self.response = response

    async def generate(self, request, requirements=None):
        class Response:
            content = self.response

        return Response()


@pytest.mark.asyncio
async def test_llm_response_generator_formats_tool_result():
    manager = StubInferenceManager(
        "It's 9:43 AM."
    )

    generator = LLMResponseGenerator(manager)

    response = await generator.generate(
        UserMessage(content="What time is it?"),
        "get_current_time",
        "2026-10-03 09:43:12 IST",
    )

    assert response == "It's 9:43 AM."
