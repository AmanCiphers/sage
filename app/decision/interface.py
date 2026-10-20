from abc import ABC, abstractmethod

from app.conversation.models import UserMessage
from app.decision.models import Decision


class DecisionEngine(ABC):

    @abstractmethod
    async def decide(self, message: UserMessage) -> Decision:
        raise NotImplementedError

    @abstractmethod
    async def respond_to_tool_result(
        self,
        message: UserMessage,
        tool_name: str,
        tool_result: object,
    ) -> str:
        raise NotImplementedError
