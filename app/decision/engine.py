from app.conversation.models import UserMessage

from .interface import DecisionEngine
from .models import Decision, DecisionAction


class DeterministicDecisionEngine(DecisionEngine):
    async def decide(self, message: UserMessage) -> Decision:
        text = message.content.lower().strip()

        if "time" in text:
            return Decision(
                action=DecisionAction.USE_TOOL,
                tool_name="get_current_time",
                arguments={},
            )

        if "date" in text:
            return Decision(
                action=DecisionAction.USE_TOOL,
                tool_name="get_current_date",
                arguments={},
            )

        return Decision(
            action=DecisionAction.RESPOND,
            response="I don't know how to handle that request yet.",
        )

    async def respond_to_tool_result(
        self,
        message: UserMessage,
        tool_name: str,
        tool_result: object,
    ) -> str:
        return str(tool_result)
