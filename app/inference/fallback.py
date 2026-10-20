from .interface import InferenceProvider
from .models import InferenceRequest, InferenceResponse


class InferenceFallback:
    def __init__(
        self,
        providers: list[InferenceProvider],
    ) -> None:
        self.providers = providers

    async def generate(
        self,
        request: InferenceRequest,
    ) -> InferenceResponse:
        errors: list[str] = []

        for provider in self.providers:
            try:
                return await provider.generate(request)
            except Exception as exc:
                errors.append(
                    f"{provider.metadata.model_id}: {exc}"
                )

        raise RuntimeError(
            "All inference providers failed: "
            + "; ".join(errors)
        )
