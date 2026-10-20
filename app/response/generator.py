from app.conversation.models import UserMessage
from app.inference.manager import InferenceManager
from app.inference.models import InferenceRequest, ModelRequirements

from .interface import ResponseGenerator


class LLMResponseGenerator(ResponseGenerator):
    def __init__(
        self,
        inference_manager: InferenceManager,
    ) -> None:
        self.inference_manager = inference_manager

    async def generate(
        self,
        message: UserMessage,
        tool_name: str | None = None,
        tool_result: object | None = None,
    ) -> str:

        if tool_name is None:
            prompt = f"""
The user said:

{message.content}

Provide a natural response to the user.

Rules:
- Be concise and natural.
- Do not invent information.
- Return only the response text.
"""
        else:
            prompt = f"""
The user asked:

{message.content}

The tool used was:
{tool_name}

The tool returned:
{tool_result}

Provide the final response to the user.

Rules:
- Answer the original request using the tool result.
- Do not mention internal tools or implementation details.
- Do not invent information.
- Be concise and natural.
- Return only the response text.
"""

        response = await self.inference_manager.generate(
            InferenceRequest(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0.2,
                max_tokens=300,
            ),
            requirements=ModelRequirements(reasoning=True),
        )

        return response.content.strip()
