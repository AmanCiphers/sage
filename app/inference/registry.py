from .interface import InferenceProvider
from .models import ModelMetadata


class ModelRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, InferenceProvider] = {}

    def register(self, provider: InferenceProvider) -> None:
        model_id = provider.metadata.model_id

        if model_id in self._providers:
            raise ValueError(f"Model already registered: {model_id}")

        self._providers[model_id] = provider

    def get(self, model_id: str) -> InferenceProvider:
        try:
            return self._providers[model_id]
        except KeyError:
            raise KeyError(f"Unknown model: {model_id}") from None

    def list(self) -> list[ModelMetadata]:
        return [
            provider.metadata
            for provider in self._providers.values()
        ]
