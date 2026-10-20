from abc import ABC, abstractmethod

from app.conversation.models import UserMessage


class ResponseGenerator(ABC):
    @abstractmethod
    async def generate(
        self,
        message: UserMessage,
        tool_name: str | None = None,
        tool_result: object | None = None,
    ) -> str:
        raise NotImplementedError
