import asyncio

from config.model_catalog import get_model_catalog
from app.conversation.models import UserMessage
from app.inference.providers.factory import create_provider
from app.inference.manager import InferenceManager
from app.inference.registry import ModelRegistry
from app.decision.llm_engine import LLMDecisionEngine
from app.tools.builtin import register_builtin_tools
from app.tools.registry import ToolRegistry


def build_inference_manager() -> InferenceManager:
    registry = ModelRegistry()

    for metadata in get_model_catalog():
        try:
            provider = create_provider(metadata)
            registry.register(provider)
        except RuntimeError:
            # Provider credentials may not be configured.
            continue

    return InferenceManager(registry)


async def main() -> None:
    inference_manager = build_inference_manager()

    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)

    decision_engine = LLMDecisionEngine(
        inference_manager,
        tool_registry,
    )

    message = UserMessage(
        content="What time is it?"
    )

    decision = await decision_engine.decide(message)

    print("=== SAGE LLM Decision ===")
    print(f"Action:    {decision.action}")
    print(f"Tool:      {decision.tool_name}")
    print(f"Arguments: {decision.arguments}")
    print(f"Response:  {decision.response}")


if __name__ == "__main__":
    asyncio.run(main())
