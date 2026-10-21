import json

from app.conversation.models import UserMessage
from app.inference.manager import InferenceManager
from app.inference.models import (
    InferenceRequest,
    ModelRequirements,
)
from app.tools.registry import ToolRegistry

from .models import Decision


class LLMDecisionEngine:
    def __init__(
        self,
        inference_manager: InferenceManager,
        tool_registry: ToolRegistry,
    ) -> None:
        self.inference_manager = inference_manager
        self.tool_registry = tool_registry

    def _tool_prompt(self) -> str:
        tools = self.tool_registry.describe()

        if not tools:
            return "No tools are currently available."

        return json.dumps(
            tools,
            indent=2,
        )

    async def decide(
        self,
        message: UserMessage,
    ) -> Decision:
        prompt = f"""
        {self._system_prompt()}

        Available tools:
        {self._tool_prompt()}

        User message:
        {message.content}

        Return ONLY valid JSON.

        For a direct response:
        {{
        "action": "respond",
        "tool_name": null,
        "arguments": {{}},
        "response": "your response"
        }}

        For a tool call:
        {{
        "action": "use_tool",
        "tool_name": "registered_tool_name",
        "arguments": {{}},
        "response": null
        }}

        Rules:
        - Only use tools listed above.
        - Never invent a tool.
        - Arguments must match the tool's input schema.
        - If no tool is appropriate, respond directly.
        """

        response = await self.inference_manager.generate(
            InferenceRequest(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=500,
                reasoning_effort="low",
            ),
            requirements=ModelRequirements(
                reasoning=True,
            ),
        )

        return self._parse_decision(response.content)

    @staticmethod
    def _system_prompt() -> str:
        from .personality import SAGE_PERSONALITY

        return SAGE_PERSONALITY

    @staticmethod
    def _parse_decision(content: str) -> Decision:
        try:
            data = json.loads(content)
            return Decision.model_validate(data)
        except Exception as exc:
            raise ValueError(
                "LLM returned an invalid decision"
            ) from exc


