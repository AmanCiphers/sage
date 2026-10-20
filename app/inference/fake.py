from collections.abc import AsyncIterator

from .interface import InferenceProvider
from .models import (
    InferenceRequest,
    InferenceResponse,
    ModelCapabilities,
    ModelMetadata,
    StreamDelta,
)


class FakeInferenceProvider(InferenceProvider):
    def __init__(
        self,
        model_id: str = "fake-model",
        capabilities: ModelCapabilities | None = None,
        priority: int = 0,
        should_fail: bool = False,
        scripted: list[InferenceResponse] | None = None,
    ) -> None:
        self._should_fail = should_fail
        self._scripted = list(scripted or [])
        self.requests: list[InferenceRequest] = []

        self._metadata = ModelMetadata(
            provider="fake",
            model_id=model_id,
            context_window=4096,
            capabilities=capabilities or ModelCapabilities(
                tool_calling=True,
                streaming=True,
            ),
            latency_class="fast",
            cost_class="free",
            priority=priority,
        )

    @property
    def metadata(self) -> ModelMetadata:
        return self._metadata

    async def generate(
        self,
        request: InferenceRequest,
    ) -> InferenceResponse:
        self.requests.append(request)

        if self._should_fail:
            raise RuntimeError(
                f"Fake provider {self.metadata.model_id} failed"
            )

        if self._scripted:
            return self._scripted.pop(0)

        return InferenceResponse(
            content="This is a fake inference response.",
            model=self.metadata.model_id,
        )

    async def stream(
        self,
        request: InferenceRequest,
    ) -> AsyncIterator[StreamDelta]:
        response = await self.generate(request)

        chunk_size = 12
        for start in range(0, len(response.content), chunk_size):
            yield StreamDelta(content=response.content[start : start + chunk_size])

        if response.tool_calls:
            yield StreamDelta(tool_calls=response.tool_calls)
