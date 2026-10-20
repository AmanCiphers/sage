from collections.abc import Awaitable, Callable

from .fallback import InferenceFallback
from .models import (
    InferenceRequest,
    InferenceResponse,
    ModelRequirements,
)
from .registry import ModelRegistry
from .router import ModelRouter

OnDelta = Callable[[str], Awaitable[None]]


class InferenceManager:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry
        self.router = ModelRouter(registry)

    async def generate(
        self,
        request: InferenceRequest,
        requirements: ModelRequirements | None = None,
    ) -> InferenceResponse:
        requirements = requirements or ModelRequirements()

        fallback = InferenceFallback(
            providers=self._candidates(requirements),
        )

        return await fallback.generate(request)

    async def stream(
        self,
        request: InferenceRequest,
        requirements: ModelRequirements | None = None,
        on_delta: OnDelta | None = None,
    ) -> InferenceResponse:
        """Stream content via on_delta as it arrives; return the full
        accumulated response (same shape as generate).

        Fallback to the next provider only before the first delta was
        delivered — once the user has seen output, never re-emit it.
        """

        requirements = requirements or ModelRequirements()
        candidates = self._candidates(requirements)

        last_error: Exception | None = None

        for provider in candidates:
            parts: list[str] = []
            tool_calls = None
            input_tokens = None
            output_tokens = None
            latency_ms = None
            model = None

            try:
                async for delta in provider.stream(request):
                    if delta.content:
                        parts.append(delta.content)
                        if on_delta is not None:
                            await on_delta(delta.content)
                    if delta.tool_calls:
                        tool_calls = delta.tool_calls
                    if delta.input_tokens is not None:
                        input_tokens = delta.input_tokens
                    if delta.output_tokens is not None:
                        output_tokens = delta.output_tokens
                    if delta.latency_ms is not None:
                        latency_ms = delta.latency_ms
                    if provider.metadata:
                        model = provider.metadata.model_id
            except Exception as exc:
                last_error = exc
                if parts:
                    # Mid-stream failure: the user already saw output.
                    raise
                continue

            return InferenceResponse(
                content="".join(parts),
                model=model or provider.metadata.model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                tool_calls=tool_calls,
            )

        raise last_error or RuntimeError("No providers available")

    def _candidates(self, requirements: ModelRequirements) -> list:
        primary = self.router.route(requirements)

        compatible = [
            provider
            for metadata in self.registry.list()
            if (
                metadata.available
                and metadata.model_id != primary.metadata.model_id
            )
            for provider in [self.registry.get(metadata.model_id)]
            if self._supports(metadata, requirements)
        ]

        return [primary, *compatible]

    @staticmethod
    def _supports(metadata, requirements: ModelRequirements) -> bool:
        capabilities = metadata.capabilities

        return (
            (not requirements.tool_calling or capabilities.tool_calling)
            and (not requirements.vision or capabilities.vision)
            and (not requirements.reasoning or capabilities.reasoning)
            and (not requirements.streaming or capabilities.streaming)
        )
