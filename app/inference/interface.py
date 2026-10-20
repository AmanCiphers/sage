from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from .models import (
    InferenceRequest,
    InferenceResponse,
    ModelMetadata,
    StreamDelta,
)


class InferenceProvider(ABC):
    @property
    @abstractmethod
    def metadata(self) -> ModelMetadata:
        raise NotImplementedError

    @abstractmethod
    async def generate(
        self,
        request: InferenceRequest,
    ) -> InferenceResponse:
        raise NotImplementedError

    async def stream(
        self,
        request: InferenceRequest,
    ) -> AsyncIterator[StreamDelta]:
        """Default: no real streaming — one delta with the full content.

        Providers that support SSE override this with live chunks.
        """

        response = await self.generate(request)

        if response.content:
            yield StreamDelta(content=response.content)

        if response.tool_calls:
            yield StreamDelta(tool_calls=response.tool_calls)
