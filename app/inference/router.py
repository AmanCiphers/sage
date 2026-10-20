from .interface import InferenceProvider
from .models import ModelRequirements
from .registry import ModelRegistry


class ModelRouter:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def route(
        self,
        requirements: ModelRequirements,
    ) -> InferenceProvider:
        candidates = []

        for metadata in self.registry.list():
            if not metadata.available:
                continue

            capabilities = metadata.capabilities

            if requirements.tool_calling and not capabilities.tool_calling:
                continue

            if requirements.vision and not capabilities.vision:
                continue

            if requirements.reasoning and not capabilities.reasoning:
                continue

            if requirements.streaming and not capabilities.streaming:
                continue

            candidates.append(metadata)

        if not candidates:
            raise RuntimeError(
                "No model satisfies the requested capabilities"
            )

        # Temporary selection policy:
        # choose the first compatible registered model.
        selected = max(
            candidates,
            key=lambda metadata: metadata.priority,
        )

        return self.registry.get(selected.model_id)
