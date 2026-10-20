import pytest

from app.inference.fake import FakeInferenceProvider
from app.inference.manager import InferenceManager
from app.inference.models import InferenceRequest
from app.inference.registry import ModelRegistry


@pytest.mark.asyncio
async def test_inference_manager_generates_response():
    provider = FakeInferenceProvider()

    registry = ModelRegistry()
    registry.register(provider)

    manager = InferenceManager(registry)

    response = await manager.generate(
        InferenceRequest(
            messages=[
                {"role": "user", "content": "Hello"},
            ],
        )
    )

    assert response.content == "This is a fake inference response."
    assert response.model == "fake-model"


@pytest.mark.asyncio
async def test_provider_exposes_metadata():
    provider = FakeInferenceProvider()

    assert provider.metadata.provider == "fake"
    assert provider.metadata.model_id == "fake-model"
    assert provider.metadata.context_window == 4096
    assert provider.metadata.capabilities.tool_calling is True
