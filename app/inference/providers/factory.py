from app.config import settings
from app.inference.interface import InferenceProvider
from app.inference.models import ModelMetadata

from .config import ProviderConfig
from .openai_compatible import OpenAICompatibleProvider


def create_provider(metadata: ModelMetadata) -> InferenceProvider:
    if metadata.provider == "nvidia":
        if not settings.nvidia_api_key:
            raise RuntimeError(
                "NVIDIA_API_KEY is not configured"
            )

        return OpenAICompatibleProvider(
            config=ProviderConfig(
                api_key=settings.nvidia_api_key,
                base_url=settings.nvidia_base_url,
                timeout=settings.model_request_timeout,
            ),
            metadata=metadata,
        )

    if metadata.provider == "nebius":
        if not settings.nebius_api_key:
            raise RuntimeError(
                "NEBIUS_API_KEY is not configured"
            )

        return OpenAICompatibleProvider(
            config=ProviderConfig(
                api_key=settings.nebius_api_key,
                base_url=settings.nebius_base_url,
                timeout=settings.model_request_timeout,
            ),
            metadata=metadata,
        )

    raise ValueError(
        f"Unsupported inference provider: {metadata.provider}"
    )
