import asyncio

from app.config import settings
from app.inference.models import InferenceRequest
from app.inference.providers.config import ProviderConfig
from app.inference.providers.openai_compatible import OpenAICompatibleProvider
from config.model_catalog import get_model_catalog


async def main() -> None:
    if not settings.nvidia_api_key:
        raise RuntimeError("NVIDIA_API_KEY is not configured")

    models = [
        model
        for model in get_model_catalog()
        if model.provider == "nvidia" and model.available
    ]

    if not models:
        raise RuntimeError("No available NVIDIA models configured")

    # Test the highest-priority configured NVIDIA model.
    metadata = max(models, key=lambda model: model.priority)

    provider = OpenAICompatibleProvider(
        config=ProviderConfig(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
        ),
        metadata=metadata,
    )

    response = await provider.generate(
        InferenceRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Reply with exactly: "
                        "SAGE NVIDIA connection successful. and who are you?"
                    ),
                }
            ],
            temperature=0.2,
            max_tokens=50,
        )
    )

    print(f"Model: {response.model}")
    print(f"Response: {response.content}")
    print(f"Input tokens: {response.input_tokens}")
    print(f"Output tokens: {response.output_tokens}")
    print(f"Latency: {response.latency_ms:.2f} ms")


if __name__ == "__main__":
    asyncio.run(main())
